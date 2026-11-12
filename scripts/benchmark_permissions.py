"""Score real API/Qdrant policy gates with a deterministic provider, without Azure calls."""

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.security import Credentials
from tests.auth_helpers import sign_in
from tests.fakes import FakeAzure
from tests.test_security import account, login, upload

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "test-workspace-password"
QUESTION = "What was APAC revenue?"
MARKER = "PERMISSION_BENCH_SECRET"
DYNAMIC = [
    "revoked_before_request", "stale_index_widened",
    "acl_during_embedding", "acl_during_generation",
    "role_during_embedding", "role_during_generation",
    "session_during_embedding", "session_during_generation",
    "tenant_during_embedding", "tenant_during_generation",
    "delete_during_embedding", "delete_during_generation",
]


class ProbeProvider(FakeAzure):
    def __init__(self):
        super().__init__()
        self.on_embed = None
        self.on_answer = None

    def embed(self, texts):
        vectors = super().embed(texts)
        if self.on_embed:
            hook, self.on_embed = self.on_embed, None
            hook()
        return vectors

    def answer(self, question, sources):
        super().answer(question, sources)
        if self.on_answer:
            hook, self.on_answer = self.on_answer, None
            hook()
        return f"{sources[0].text.strip()} [1]"


def matrix_cases():
    """Explicit expected grants; expectations do not call the production policy function."""
    visible = {
        "admin": {"private", "finance", "employee", "named", "shared"},
        "alice": {"employee", "named", "shared"},
        "bob": {"finance", "shared"},
        "charlie": {"employee", "shared"},
        "guest": set(),
        "foreign": {"foreign"},
    }
    policies = {
        "private": {}, "finance": {"roles": ["finance"]},
        "employee": {"roles": ["employee"]}, "named": {},
        "shared": {"roles": ["employee", "finance"]}, "foreign": {},
    }
    with tempfile.TemporaryDirectory(prefix="folio-matrix-") as temp:
        provider = ProbeProvider()
        settings = Settings(_env_file=None, data_dir=Path(temp), retrieval_score_threshold=0.05)
        with TestClient(create_app(settings, provider)) as client:
            sign_in(client)
            alice = account(client, "alice@example.test", ["employee"])
            for name, roles in [("bob", ["finance"]), ("charlie", ["employee"]),
                                ("guest", ["engineering"])]:
                account(client, f"{name}@example.test", roles)
            client.app.state.security.add_user(
                Credentials(email="foreign@example.test", password=PASSWORD),
                "other", ["admin", "finance"],
            )
            docs = {}
            for name, policy in policies.items():
                if name == "foreign":
                    login(client, "foreign@example.test")
                if name == "named":
                    policy = {"users": [alice["user_id"]]}
                docs[name] = upload(
                    client, **policy, name=f"{name}.md",
                    text=f"# Finance\n\nAPAC revenue was $82M. {MARKER}_{name}",
                )["document"]["document_id"]
            for name, allowed in visible.items():
                login(client, f"{name}@example.test")
                for doc_name, doc_id in docs.items():
                    started = time.monotonic()
                    expected_allow = doc_name in allowed
                    provider.last_sources = []
                    before = provider.answer_calls
                    payload = {"question": QUESTION, "document_ids": [doc_id]}
                    retrieved = client.post("/retrieve", json=payload)
                    answer = client.post("/chat", json=payload)
                    download = client.get(f"/documents/{doc_id}/file")
                    detail = client.get(f"/documents/{doc_id}")
                    if expected_allow:
                        checks = {
                            "retrieval": retrieved.status_code == 200 and any(
                                s["document_id"] == doc_id for s in retrieved.json()
                            ),
                            "answer": answer.status_code == 200
                            and MARKER in answer.json().get("answer", ""),
                            "citation": answer.status_code == 200 and any(
                                s["document_id"] == doc_id for s in answer.json().get("sources", [])
                            ),
                            "download": download.status_code == 200,
                            "detail": detail.status_code == 200,
                        }
                        exposed_context = exposed_response = False
                    else:
                        exposed_context = bool(provider.last_sources)
                        response_text = retrieved.text + answer.text + download.text + detail.text
                        exposed_response = any(
                            value in response_text for value in [MARKER, doc_id, f"{doc_name}.md"]
                        )
                        checks = {
                            "context_denied": not exposed_context,
                            "response_denied": not exposed_response,
                            "no_generation": provider.answer_calls == before,
                            "routes_denied": retrieved.status_code == answer.status_code == 409
                            and download.status_code == detail.status_code == 404,
                        }
                    yield {
                        "id": f"matrix/{name}/{doc_name}", "expected_allow": expected_allow,
                        "checks": checks, "passed": all(checks.values()),
                        "unauthorized_context": exposed_context,
                        "unauthorized_response": exposed_response,
                        "duration_ms": (time.monotonic() - started) * 1000,
                    }


def dynamic_case(mode, route):
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="folio-revocation-") as temp:
        provider = ProbeProvider()
        settings = Settings(_env_file=None, data_dir=Path(temp), retrieval_score_threshold=0.05)
        with TestClient(create_app(settings, provider)) as client:
            sign_in(client)
            bob = account(client, "bob@example.test", ["finance"])
            doc_id = upload(client, roles=["finance"],
                            text=f"# Finance\n\nAPAC revenue was $82M. {MARKER}")[
                                "document"]["document_id"]
            catalog = client.app.state.catalog

            def revoke():
                if (mode.startswith("acl") or mode.startswith("stale_index")
                        or mode == "revoked_before_request"):
                    # Leave the index stale deliberately. SQLite is the policy authority.
                    catalog.set_acl(catalog.get(doc_id), {"allowed_roles": []})
                else:
                    with catalog.connect() as db:
                        if mode.startswith("role"):
                            db.execute("UPDATE users SET roles = '[]' WHERE id = ?",
                                       (bob["user_id"],))
                        elif mode.startswith("session"):
                            db.execute("DELETE FROM sessions WHERE user_id = ?", (bob["user_id"],))
                        elif mode.startswith("tenant"):
                            db.execute("UPDATE users SET tenant = 'other' WHERE id = ?",
                                       (bob["user_id"],))
                        elif mode.startswith("delete"):
                            db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

            login(client, "bob@example.test")
            if mode in {"revoked_before_request", "stale_index_widened"}:
                revoke()
                vector_roles = ["finance"] if mode.endswith("widened") else []
                client.app.state.vectors.set_acl(doc_id, {"allowed_roles": vector_roles})
            elif mode.endswith("embedding"):
                provider.on_embed = revoke
            else:
                provider.on_answer = revoke
            provider.last_sources = []
            response = client.post(route, json={"question": QUESTION})
            # A generation hook runs after context was legitimately supplied. Its release
            # must be denied; the earlier authorized context is not counted as a leak.
            exposed_context = bool(provider.last_sources) and mode.endswith("embedding")
            exposed_response = MARKER in response.text or doc_id in response.text
            if route.endswith("stream"):
                events = [json.loads(line) for line in response.text.splitlines()]
                denied = any(e["type"] == "error" or (
                    e["type"] == "answer" and not e.get("sources")
                ) for e in events)
            else:
                denied = response.status_code in {401, 409} or (
                    response.status_code == 200 and not response.json().get("sources")
                )
            checks = {"context_denied_when_revoked_before_generation": not exposed_context,
                      "response_denied": not exposed_response, "denial_contract": denied}
            return {"id": f"revocation/{mode}/{route.removeprefix('/')}",
                    "expected_allow": False, "checks": checks, "passed": all(checks.values()),
                    "unauthorized_context": exposed_context,
                    "unauthorized_response": exposed_response,
                    "duration_ms": (time.monotonic() - started) * 1000}


def run():
    results = list(matrix_cases())
    for mode in DYNAMIC:
        for route in ["/chat", "/chat/stream"]:
            results.append(dynamic_case(mode, route))
    denied = [r for r in results if not r["expected_allow"]]
    allowed = [r for r in results if r["expected_allow"]]
    return {
        "schema_version": 1, "benchmark": "synthetic-permission-api-v1",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_dirty": bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True
        ).strip()),
        "suite_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": len(results), "authorized_cases": len(allowed), "denied_cases": len(denied),
        "metrics": {
            "case_pass_rate": sum(r["passed"] for r in results) / len(results),
            "unauthorized_context_rate": (
                sum(r["unauthorized_context"] for r in denied) / len(denied)
            ),
            "unauthorized_response_rate": (
                sum(r["unauthorized_response"] for r in denied) / len(denied)
            ),
            "false_denial_rate": sum(not r["passed"] for r in allowed) / len(allowed),
        },
        "limitations": "Synthetic policy regression, deterministic embeddings/answer double. "
        "Real auth, SQLite, Qdrant and API. Hooks force revocation interleavings; "
        "no load/concurrency "
        "statistics, Azure answer-quality, BIPIA robustness or proof of universal security.",
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    print(json.dumps({"failures": [r["id"] for r in report["results"] if not r["passed"]]}))
    if args.gate and report["metrics"]["case_pass_rate"] != 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
