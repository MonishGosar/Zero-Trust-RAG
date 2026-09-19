"""Inspect/benchmark real files without changing the user's document library.

Default: offline PDF routing inspection. --live: real Azure ingestion and retrieval
in temporary local storage (normal Azure usage applies). Reports contain metrics,
not document contents or Azure configuration. --mode docling forces the slow path.
"""

import argparse
import json
import secrets
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.pdf_native import inspect_pdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--mode", choices=["auto", "docling"], default="auto")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {"mode": args.mode, "live_azure": args.live, "files": []}

    def checkpoint():
        if args.output:
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for path in args.paths:
        started = time.perf_counter()
        profile = inspect_pdf(path)
        report["files"].append({
            "file": path.name, "bytes": path.stat().st_size, "pages": profile.pages,
            "inspection_ms": round((time.perf_counter() - started) * 1000, 2),
            "fast_eligible": profile.fast_eligible, "needs_ocr": profile.needs_ocr,
            "routing_reasons": profile.reasons,
        })
    checkpoint()
    if args.live:
        with tempfile.TemporaryDirectory(prefix="folio-benchmark-") as directory:
            settings = Settings(data_dir=Path(directory), qdrant_url="", qdrant_api_key="",
                                pdf_parsing_mode=args.mode)
            if settings.missing:
                raise SystemExit("Configure Azure before using --live.")
            with TestClient(create_app(settings)) as client:
                client.post("/auth/setup", json={"tenant_id": "benchmark",
                    "email": "benchmark@example.test", "password": secrets.token_urlsafe(24),
                }).raise_for_status()
                started = time.perf_counter()
                jobs = []
                for path in args.paths:
                    with path.open("rb") as source:
                        response = client.post("/documents", files={"file": (path.name, source)})
                    response.raise_for_status()
                    jobs.append(response.json()["document_id"])
                for record, path, job in zip(report["files"], args.paths, jobs):
                    while time.perf_counter() - started < 600:
                        detail = client.get(f"/documents/{job}").json()
                        document = detail["document"]
                        if document["status"] in {"ready", "failed"}:
                            break
                        time.sleep(0.1)
                    else:
                        raise TimeoutError("Ingestion benchmark exceeded ten minutes.")
                    record.update(status=document["status"], timings_ms=document["timings_ms"],
                                  processing=document["processing"], chunks=document["chunk_count"])
                    checkpoint()
                    if document["status"] == "failed":
                        record["error"] = document["error"]
                        checkpoint()
                        continue
                    record["page_provenance_valid"] = all(
                        c["page"] is not None and 1 <= c["page"] <= record["pages"]
                        for c in detail["chunks"]
                    )
                    question = {"question": "What are the main terms and details?",
                                "document_ids": [job]}
                    retrieval = client.post("/retrieve", json=question)
                    retrieval.raise_for_status()
                    sources = retrieval.json()
                    record["retrieved"] = len(sources)
                    record["retrieval_scope_valid"] = bool(sources) and all(
                        s["document_id"] == job for s in sources
                    )
                    answer = client.post("/chat", json=question)
                    answer.raise_for_status()
                    record["citation_references_valid"] = answer.json()["grounded"]
                    repeat_started = time.perf_counter()
                    with path.open("rb") as source:
                        repeat = client.post("/documents", files={"file": (path.name, source)})
                    repeat.raise_for_status()
                    record["duplicate_ms"] = round((time.perf_counter() - repeat_started) * 1000, 2)
                    record["duplicate_reused"] = (repeat.json()["reused"]
                                                   and repeat.json()["document_id"] == job)
                    checkpoint()
    output = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    if args.live and any(r.get("status") != "ready" for r in report["files"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
