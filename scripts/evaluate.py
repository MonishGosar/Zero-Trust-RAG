"""Run a transparent live baseline against an already-indexed synthetic document."""

import argparse
import json
import os
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--document-id", required=True, help="ID of the uploaded quarterly-report.md"
    )
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "examples/evaluation.json",
    )
    args = parser.parse_args()
    cases = json.loads(args.dataset.read_text(encoding="utf-8"))
    results = []
    with httpx.Client(base_url=args.api, timeout=180) as client:
        email, password = os.environ.get("FOLIO_EMAIL"), os.environ.get("FOLIO_PASSWORD")
        if not email or not password:
            raise SystemExit("Set FOLIO_EMAIL and FOLIO_PASSWORD for an authorized test account.")
        client.post("/auth/login", json={"email": email, "password": password}).raise_for_status()
        for case in cases:
            payload = {"question": case["question"], "document_ids": [args.document_id]}
            retrieval = client.post("/retrieve", json=payload)
            retrieval.raise_for_status()
            answer = client.post("/chat", json=payload)
            answer.raise_for_status()
            body = answer.json()
            expected = case["expected_text"].casefold()
            result = {
                "question": case["question"],
                "retrieval_hit": any(
                    expected in s["text"].casefold() and s["section"] == case["section"]
                    for s in retrieval.json()
                ),
                "answer_contains_expected": expected in body["answer"].casefold(),
                "citation_hit": any(
                    s["document_id"] == args.document_id
                    and s["section"] == case["section"]
                    and expected in s["text"].casefold()
                    for s in body["sources"]
                ),
            }
            results.append(result)
            print(json.dumps(result))
    print(
        json.dumps(
            {
                "cases": len(results),
                "metrics": {
                    key: sum(r[key] for r in results) / len(results)
                    for key in ("retrieval_hit", "answer_contains_expected", "citation_hit")
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
