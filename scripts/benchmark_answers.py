"""Live Azure smoke benchmark on the repository's synthetic quarterly report."""

import argparse
import hashlib
import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from scripts.answer_scoring import value_match
from tests.auth_helpers import sign_in

ROOT = Path(__file__).resolve().parents[1]


def add_value_scores(report, cases):
    expected = {case["question"]: case["expected_text"] for case in cases}
    for result in report["results"]:
        result["checks"]["answer_value_match"] = value_match(
            expected[result["question"]], result["answer"]
        )
    report["metrics"]["answer_value_match"] = sum(
        result["checks"]["answer_value_match"] for result in report["results"]
    ) / len(report["results"])
    report["value_case_pass_rate"] = sum(
        all(value for name, value in result["checks"].items()
            if name != "answer_contains_expected") for result in report["results"]
    ) / len(report["results"])
    report["scoring_note"] = (
        "Original literal scores retained. Value matching also accepts equivalent dollar "
        "scales ($54M/$54 million), comma formatting and percent wording. Matches values "
        "anywhere in the answer; it is not a claim-level factuality/entailment judgment."
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--rescore", type=Path, help="Score saved answers without Azure calls")
    args = parser.parse_args()
    cases_path = ROOT / "examples/evaluation.json"
    document_path = ROOT / "examples/quarterly-report.md"
    cases = json.loads(cases_path.read_text())
    dataset_hash = hashlib.sha256(
        cases_path.read_bytes() + b"\0" + document_path.read_bytes()
    ).hexdigest()
    if args.rescore:
        report = json.loads(args.rescore.read_text())
        if report["dataset_sha256"] != dataset_hash:
            raise ValueError("Saved answer dataset mismatch")
        add_value_scores(report, cases)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
        if args.gate and report["value_case_pass_rate"] < 0.9:
            raise SystemExit(1)
        return
    if args.max_cases is not None:
        if args.max_cases < 1:
            parser.error("max-cases must be positive")
        cases = cases[:args.max_cases]
    results = []
    with tempfile.TemporaryDirectory(prefix="folio-azure-answers-") as temp:
        settings = Settings(data_dir=Path(temp), qdrant_url="",
                            qdrant_collection="answer_benchmark")
        if settings.missing:
            raise ValueError("Configure Azure settings: " + ", ".join(settings.missing))
        with TestClient(create_app(settings)) as client:
            sign_in(client)
            response = client.post("/documents", files={
                "file": (document_path.name, document_path.read_bytes())
            })
            response.raise_for_status()
            doc_id = response.json()["document_id"]
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                detail = client.get(f"/documents/{doc_id}").json()["document"]
                if detail["status"] == "ready":
                    break
                if detail["status"] == "failed":
                    raise RuntimeError("Azure ingestion failed: " + detail["error"])
                time.sleep(0.2)
            else:
                raise RuntimeError("Ingestion exceeded 180 seconds")
            for case in cases:
                started = time.monotonic()
                payload = {"question": case["question"], "document_ids": [doc_id]}
                retrieval = client.post("/retrieve", json=payload)
                answer = client.post("/chat", json=payload)
                expected = case["expected_text"].casefold()
                retrieved = retrieval.json() if retrieval.status_code == 200 else []
                body = answer.json() if answer.status_code == 200 else {}
                def supporting(source):
                    return (source["document_id"] == doc_id
                            and source["section"] == case["section"]
                            and expected in source["text"].casefold())
                checks = {
                    "request_success": retrieval.status_code == answer.status_code == 200,
                    "retrieval_hit": any(supporting(source) for source in retrieved),
                    "answer_contains_expected": expected in body.get("answer", "").casefold(),
                    "citation_hit": any(supporting(source) for source in body.get("sources", [])),
                    "reference_valid": bool(body.get("grounded")),
                }
                results.append({"question": case["question"], "checks": checks,
                                "answer": body.get("answer", ""),
                                "passed": all(checks.values()),
                                "duration_ms": (time.monotonic() - started) * 1000,
                                "http_status": [retrieval.status_code, answer.status_code]})
                print(json.dumps({"completed": len(results), "cases": len(cases),
                                  "passed": results[-1]["passed"]}), flush=True)
    report = {
        "schema_version": 1, "benchmark": "live-azure-synthetic-answers-v1",
        "cases": len(results), "available_cases": 20,
        "dataset_sha256": dataset_hash,
        "configuration": {"chat_deployment": settings.azure_openai_chat_deployment,
                          "embedding_deployment": settings.azure_openai_embedding_deployment,
                          "retrieval_top_k": settings.retrieval_top_k,
                          "retrieval_score_threshold": settings.retrieval_score_threshold},
        "metrics": {key: sum(r["checks"][key] for r in results) / len(results)
                    for key in results[0]["checks"]},
        "case_pass_rate": sum(r["passed"] for r in results) / len(results),
        "limitations": "20 authored questions on one synthetic document, no independent holdout. "
        "Expected-substring matching can reject valid paraphrases. Citation hit checks source "
        "section/content; reference validity does not establish claim entailment. "
        "Not RAGBench or BIPIA. Live outputs vary; token usage/cost not measured.",
        "results": results,
    }
    add_value_scores(report, cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    if args.gate and report["value_case_pass_rate"] < 0.9:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
