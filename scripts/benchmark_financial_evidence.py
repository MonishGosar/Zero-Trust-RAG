"""Offline regression score for cited money/percentage literal consistency, not entailment."""

import argparse
import hashlib
import json
from pathlib import Path

from backend.azure import validate_citations
from backend.evidence import check_financial_evidence
from backend.models import Source

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()
    raw = (ROOT / "benchmarks/financial-evidence.json").read_bytes()
    dataset = json.loads(raw)
    sources = [Source(chunk_id=str(i), document_id=str(i), filename=f"synthetic-{i}.md",
                      text=text, token_count=30, citation=i, score=1)
               for i, text in enumerate(dataset["sources"], 1)]
    results = []
    for case in dataset["cases"]:
        valid = validate_citations(case["answer"], sources)[2]
        check = check_financial_evidence(case["answer"], sources)
        allowed = valid and check["status"] != "failed"
        results.append({**case, "reference_only_allowed": valid, "financial_guard_allowed": allowed,
                        "before_correct": valid == case["expected_allow"],
                        "after_correct": allowed == case["expected_allow"], "check": check})
    report = {
        "schema_version": 1, "benchmark": "financial-literal-consistency-v1",
        "dataset_sha256": hashlib.sha256(raw).hexdigest(), "cases": len(results),
        "metrics": {
            "before_case_pass_rate": sum(r["before_correct"] for r in results) / len(results),
            "after_case_pass_rate": sum(r["after_correct"] for r in results) / len(results),
            "before_unsupported_accepts": sum(
                not r["expected_allow"] and r["reference_only_allowed"] for r in results
            ),
            "after_unsupported_accepts": sum(
                not r["expected_allow"] and r["financial_guard_allowed"] for r in results
            ),
            "after_supported_denials": sum(r["expected_allow"] and not r["financial_guard_allowed"]
                                            for r in results),
        },
        "limitations": "Authored literal-consistency cases, not semantic claim entailment. "
        "Same number/wrong entity or period, derived quantities, chart/table UI values, "
        "spelled-out numbers and unstated currency units are not verified. Guard is opt-in.",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    if args.gate and report["metrics"]["after_case_pass_rate"] != 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
