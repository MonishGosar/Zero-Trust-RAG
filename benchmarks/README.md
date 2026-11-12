# Reproducible Folio evaluation

Keep permission safety, retrieval ranking, and answer smoke scores separate.
A useful answer score cannot compensate for unauthorized exposure.

## Permission iteration

The same suite was run against baseline
`7c1f7b5b47d7dfb47778cbabf605e7632a514894` and the proposed implementation.
Both JSON reports carry the same suite SHA256.

| Measure | Baseline | Improved |
| --- | --- | --- |
| Cases passing all checks | 40/60 | 60/60 |
| Unauthorized response exposure among denial cases | 20/47 | 0/47 |
| Unauthorized model context among denial cases | 10/47 | 0/47 |
| Incorrect denials among authorized cases | 0/13 | 0/13 |

Coverage: 36 user/document combinations across owner, role, named-user and tenant
boundaries; 24 revocation variations over `/chat` and `/chat/stream`. The matrix
also checks `/retrieve`, document detail and original downloads. Revocation
hooks force ACL, role, session, tenant or deletion changes during embedding or
generation. Static cases compare consistent revocation with stale/widened index
grants. Checks inspect actual model context, response text, IDs and filenames.

The provider is deterministic; authentication, catalog, Qdrant and routes are
real and isolated in temporary directories. These are synthetic regressions,
not Azure quality scores, statistical concurrency measurements, BIPIA results,
or proof that all unauthorized exposures are impossible.

The fix reauthenticates the session and reloads tenant, roles and catalog policy
after embedding/search, before generation and before release. Detected changes
suppress the whole result. The catalog overrides stale vector grants. An Azure
request that already received authorized context cannot be undone; changes after
the final check still race with delivery. See [ARCHITECTURE.md](../ARCHITECTURE.md).

```sh
uv sync --locked
uv run python -m scripts.benchmark_permissions --output benchmark-output/permissions.json --gate
```

`--gate` requires every case to pass. CI needs no Azure credentials and uploads
the JSON artifact. For Markdown-only benchmark dependencies without the GPU
document converter:

```sh
uv export --locked --no-hashes --prune docling --no-emit-project --output-file /tmp/benchmark-requirements.txt
uv venv
uv pip install -r /tmp/benchmark-requirements.txt
uv run --no-sync python -m scripts.benchmark_permissions --output benchmark-output/permissions.json --gate
```

## Public retrieval benchmark

[BEIR](https://github.com/beir-cellar/beir) supplies the SciFact corpus and
relevance judgments: 5,183 documents and 300 test queries. The complete corpus
is retained even for an optional `--max-queries` smoke run. Scores are mean
nDCG@10 (linear relevance gain), Recall@10 and MRR@10. Identical query/document
IDs are excluded, ties are deterministic for BM25, and failures abort the run.

Download the official archive once:

```sh
mkdir -p .scratch/datasets
curl -fL https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip -o .scratch/scifact.zip
python - <<'PY'
import hashlib
import zipfile
from pathlib import Path
archive = Path('.scratch/scifact.zip')
assert hashlib.md5(archive.read_bytes()).hexdigest() == '5f7d1de60b170fc8027bb7898e2efca1'
with zipfile.ZipFile(archive) as source:
    for name in ['scifact/corpus.jsonl', 'scifact/queries.jsonl', 'scifact/qrels/test.tsv']:
        target = Path('.scratch/datasets') / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read(name))
PY
```

Run a small BM25 reference with no model dependency:

```sh
python -m scripts.benchmark_retrieval --dataset .scratch/datasets/scifact --backend bm25 --output benchmark-output/scifact.bm25.json
```

Run Folio's production Azure embeddings and Qdrant with the configured deployment:

```sh
uv run python -m scripts.benchmark_retrieval --dataset .scratch/datasets/scifact --backend azure --embedding-cache .scratch/embeddings.sqlite --tokens-per-minute 120000 --output benchmark-output/scifact.azure.json
```

The Azure runner uses one title+text passage per benchmark document, a benchmark
tenant/owner, and a score threshold of -1 to rank the whole corpus. Its isolated
collection contains only the public corpus, entirely owned by the benchmark
user, so every scope/ACL predicate is true. It omits these predicates during raw
ranking to avoid embedded Qdrant's slow large-list scope checks. Five queries
must produce identical rankings through production filtered search before the
run can finish. It measures production embeddings and Qdrant ranking, not upload
parsing, chunking, production filter latency, generation or ACL quality.
BM25 is an independent lexical reference, not Folio's production retriever or
the official BEIR BM25 implementation. Its tokenizer has no stemming/stopwords.
No claim of leaderboard comparability is made for different preprocessing.

The runner batches query embeddings, caps concurrent embedding work, schedules
an input-token budget and retries rate limits. The optional cache checkpoints
successful embeddings so an interrupted run can resume. Cache keys cover text,
endpoint, deployment and API version; **clear the cache if the deployed model
changes**, even when its deployment name stays the same. Use a fresh cache when
comparing embedding revisions. Index time can mix cached and uncached work;
reported query latency is raw ranking only, with embedding wall time separate.
Reports contain dataset fingerprints and per-query rankings; corpus files,
credentials, caches and vectors are not committed. API usage/cost is not measured.

Full recorded scores are in `results/scifact.bm25.json` and
`results/scifact.azure.json`. Tune future retrieval changes on training data,
then evaluate the same untouched test corpus and query set.

| Full SciFact test, 300 queries | nDCG@10 | Recall@10 | MRR@10 |
| --- | --- | --- | --- |
| BM25 lexical reference | 0.6617 | 0.7909 | 0.6276 |
| Azure `text-embedding-ada-002` + Qdrant | 0.7292 | 0.8459 | 0.6973 |

The final Azure ranking pass reused all corpus/query vectors embedded through
Azure earlier in this evaluation. Its index/embedding times are warm-cache
measurements, not service throughput measurements. Five production-filter
parity checks passed. This is a comparison of retrieval approaches, separate
from the before/after permission improvement.

## Live Azure answer checks

`scripts/benchmark_answers.py` creates an isolated real app, uploads the repository's
fictional quarterly report, and asks all 20 questions in `examples/evaluation.json`.
It uses the production ingestion, retrieval, generation and citation checks.
The configured deployments were `text-embedding-ada-002`, `gpt-5-mini` and then
the requested `gpt-6-luna` chat deployment. No default deployment is hardcoded.

| Measure | gpt-5-mini deployment | gpt-6-luna deployment |
| --- | --- | --- |
| Requests, retrieval hits, citation hits, valid references | 20/20 each | 20/20 each |
| Original literal answer matching | 19/20 | 16/20 |
| Equivalent expected-value matching | 20/20 | 20/20 |

Literal matching flagged equivalent currency formats such as `$54 million`
versus `$54M`. A value matcher was added after reviewing these formatting
false negatives, with tests rejecting incorrect magnitudes, signs and values.
Both saved runs were rescored without making more model calls. The original
literal scores and answers remain in the reports. Value matching finds expected
values anywhere in the answer; it does not verify all claims or entailment.
These 20 questions on one document do not establish a model winner or broad
answer quality, and are not RAGBench/BIPIA evaluations.

Set the documented Azure variables through local environment settings. The
preferred chat deployment for subsequent runs is `gpt-6-luna`.

```sh
uv run python -m scripts.benchmark_answers --output benchmark-output/answers.json --gate
uv run python -m scripts.benchmark_answers --rescore benchmark-output/answers.json --output benchmark-output/rescored.json --gate
```

The live gate requires at least 90% of cases to pass request, retrieval,
expected-value, citation and reference checks. It is separate from the stricter
permission gate. A successful finite suite does not establish universal security.

Validation: 68 targeted backend/scoring tests pass (one real Docling DOCX
conversion test deselected), Ruff passes, and the permission gate passes.
Full Docling conversion and frontend/browser suites were outside this change's
targeted validation. CI runs the security, Azure-provider and scoring tests plus
the 60-case permission gate without cloud credentials.

## Next experiments

1. Expand permission scenarios to source ACL sync, index lag, concurrent load and
   lifecycle operations. Keep denials and authorized usefulness visible together.
2. Use SciFact training judgments to test hybrid retrieval or reranking against
   the fixed Azure/BM25 references; repeat on a second public corpus.
3. Add claim-level answer/citation judgments and unanswerable questions. Calibrate
   the evaluator on annotated [RAGBench](https://github.com/rungalileo/ragbench)
   data and test accessible-document injection with
   [BIPIA](https://github.com/microsoft/BIPIA). Neither score is claimed here.
