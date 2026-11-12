"""BEIR-format document retrieval scoring: BM25 reference or Folio Azure/Qdrant."""

import argparse
import csv
import hashlib
import heapq
import json
import math
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def retrieval_metrics(ranked, relevant, k):
    ranked = ranked[:k]
    if len(set(ranked)) != len(ranked):
        raise ValueError("Duplicate ranked document IDs")
    positive = {doc: grade for doc, grade in relevant.items() if grade > 0}
    if not positive:
        raise ValueError("Each scored query needs at least one positive relevance judgment")
    gains = [positive.get(doc, 0) for doc in ranked]
    dcg = sum(grade / math.log2(i + 2) for i, grade in enumerate(gains))
    ideal = sum(grade / math.log2(i + 2)
                for i, grade in enumerate(sorted(positive.values(), reverse=True)[:k]))
    hits = [i + 1 for i, doc in enumerate(ranked) if doc in positive]
    return {f"ndcg@{k}": dcg / ideal, f"recall@{k}": len(hits) / len(positive),
            f"mrr@{k}": 1 / hits[0] if hits else 0.0}


def load_dataset(path, split):
    def jsonl(name):
        values = [json.loads(line) for line in (path / name).read_text().splitlines() if line]
        result = {str(row["_id"]): row for row in values}
        if len(result) != len(values):
            raise ValueError(f"Duplicate IDs in {name}")
        return result

    corpus, queries = jsonl("corpus.jsonl"), jsonl("queries.jsonl")
    qrels = defaultdict(dict)
    with (path / "qrels" / f"{split}.tsv").open() as source:
        for row in csv.DictReader(source, delimiter="\t"):
            query, doc, grade = row["query-id"], row["corpus-id"], int(row["score"])
            if query not in queries or doc not in corpus or grade < 0:
                raise ValueError("Invalid or missing qrels reference")
            if doc in qrels[query]:
                raise ValueError("Duplicate qrels pair")
            qrels[query][doc] = grade
    if not corpus or not qrels:
        raise ValueError("Empty corpus or qrels")
    for judgments in qrels.values():
        if not any(grade > 0 for grade in judgments.values()):
            raise ValueError("Query without positive relevance judgment")
    digest = hashlib.sha256()
    for name in ["corpus.jsonl", "queries.jsonl", f"qrels/{split}.tsv"]:
        digest.update(name.encode() + b"\0" + (path / name).read_bytes())
    return corpus, queries, dict(qrels), digest.hexdigest()


def tokenize(text):
    return re.findall(r"\w+", text.casefold())


class BM25:
    """Small reference implementation, k1=1.2/b=0.75, no stemming or stopwords."""

    def __init__(self, corpus):
        self.postings = defaultdict(dict)
        self.lengths = {}
        for doc_id, doc in corpus.items():
            terms = Counter(tokenize(doc.get("title", "") + " " + doc["text"]))
            self.lengths[doc_id] = sum(terms.values())
            for term, count in terms.items():
                self.postings[term][doc_id] = count
        self.average = sum(self.lengths.values()) / len(self.lengths) or 1

    def search(self, question, k, query_id):
        scores = defaultdict(float)
        count = len(self.lengths)
        for term in set(tokenize(question)):
            posting = self.postings.get(term, {})
            idf = math.log(1 + (count - len(posting) + 0.5) / (len(posting) + 0.5))
            for doc_id, frequency in posting.items():
                if doc_id != query_id:
                    norm = 1.2 * (0.25 + 0.75 * self.lengths[doc_id] / self.average)
                    scores[doc_id] += idf * frequency * 2.2 / (frequency + norm)
        return heapq.nsmallest(k, scores, key=lambda doc: (-scores[doc], doc))

    def close(self):
        pass


class EmbeddingCache:
    """Opt-in local cache; invalidate it whenever the deployed embedding model changes."""

    def __init__(self, path, profile):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.namespace = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, value TEXT)")

    def key(self, text):
        return hashlib.sha256((self.namespace + "\0" + text).encode()).hexdigest()

    def get(self, text):
        with self.lock:
            row = self.db.execute("SELECT value FROM embeddings WHERE key = ?",
                                  (self.key(text),)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, texts, vectors):
        if len(texts) != len(vectors) or any(not vector for vector in vectors):
            raise ValueError("Invalid cached embedding batch")
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?)",
                                [(self.key(text), json.dumps(vector))
                                 for text, vector in zip(texts, vectors)])

    def close(self):
        self.db.close()


class TokenBudget:
    def __init__(self, per_minute):
        self.limit = per_minute
        self.calls = deque()
        self.lock = threading.Lock()

    def acquire(self, tokens):
        if tokens > self.limit:
            raise ValueError("An embedding batch exceeds the configured per-minute token budget")
        while True:
            with self.lock:
                now = time.monotonic()
                while self.calls and self.calls[0][0] <= now - 60:
                    self.calls.popleft()
                if sum(count for _, count in self.calls) + tokens <= self.limit:
                    self.calls.append((now, tokens))
                    return
                delay = max(0.1, self.calls[0][0] + 60 - now)
            time.sleep(min(delay, 5))


class FolioAzure:
    """One passage per benchmark document, using production embeddings and vector filters."""

    def __init__(self, corpus, data_dir, workers=4, cache_path=None, tokens_per_minute=120000):
        from uuid import NAMESPACE_URL, uuid5

        from backend.azure import AzureProvider
        from backend.config import Settings
        from backend.models import Chunk
        from backend.parsing import tokens
        from backend.security import UserContext
        from backend.vectors import VectorStore

        settings = Settings(data_dir=data_dir, qdrant_url="", qdrant_collection="beir_benchmark")
        if settings.missing:
            raise ValueError("Configure Azure settings first: " + ", ".join(settings.missing))
        self.provider = AzureProvider(settings)
        self.vectors = VectorStore(settings)
        profile = {"endpoint": settings.azure_openai_embedding_endpoint
                   or settings.azure_openai_endpoint,
                   "deployment": settings.azure_openai_embedding_deployment,
                   "api_version": settings.azure_openai_api_version}
        self.cache = EmbeddingCache(cache_path, profile) if cache_path else None
        self.budget = TokenBudget(tokens_per_minute)
        self.ids = sorted(corpus)
        self.workers = workers
        self.query_vectors = {}
        self.user = UserContext(user_id="benchmark", email="benchmark@example.test",
                                tenant_id="benchmark", roles=["benchmark"])
        self.config = {"embedding_deployment": settings.azure_openai_embedding_deployment,
                       "embedding_workers": workers, "embedding_batch_documents": 32,
                       "tokens_per_minute_budget": tokens_per_minute,
                       "embedding_cache_namespace": self.cache.namespace if self.cache else None,
                       "document_representation": "title + text, one passage per document"}
        try:
            def batch_chunks(start):
                chunks = []
                for doc_id in self.ids[start:start + 32]:
                    doc = corpus[doc_id]
                    text = (doc.get("title", "") + "\n" + doc["text"]).strip()
                    chunks.append(Chunk(
                        chunk_id=str(uuid5(NAMESPACE_URL, "folio-beir:" + doc_id)),
                        document_id=doc_id, filename=doc_id, text=text, token_count=tokens(text),
                        tenant_id="benchmark", owner_id="benchmark",
                    ))
                return chunks, self.embed([c.text for c in chunks])

            completed = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for chunks, embeddings in pool.map(batch_chunks, range(0, len(self.ids), 32)):
                    self.vectors.upsert(chunks, embeddings)
                    completed += len(chunks)
                    if completed % 512 == 0 or completed == len(self.ids):
                        print(json.dumps({"indexed": completed, "documents": len(self.ids)}),
                              flush=True)
        except Exception:
            self.close()
            raise

    def search(self, question, k, query_id):
        vector = self.query_vectors.get(query_id)
        if vector is None:
            vector = self.embed([question])[0]
        # This fresh collection contains only this public corpus and every point
        # belongs to the benchmark owner. All policy/scope predicates are true.
        # Avoid embedded Qdrant's quadratic list-membership filter evaluation;
        # this measures vector ranking, not production policy-filter latency.
        points = self.vectors.client.query_points(
            self.vectors.collection, query=vector, limit=k + 1, with_payload=True,
            score_threshold=-1,
        ).points
        return [p.payload["document_id"] for p in points
                if p.payload["document_id"] != query_id][:k]

    def verify_policy_parity(self, query_id, k):
        vector = self.query_vectors[query_id]
        sources = self.vectors.search(vector, self.ids, k + 1, -1, user=self.user)
        filtered = [s.document_id for s in sources if s.document_id != query_id][:k]
        if filtered != self.search("", k, query_id):
            raise ValueError("Raw benchmark ranking differs from the production policy search")

    def prepare_queries(self, queries):
        entries = list(queries.items())

        def embed_batch(start):
            batch = entries[start:start + 32]
            vectors = self.embed([text for _, text in batch])
            return dict(zip([query_id for query_id, _ in batch], vectors))

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for vectors in pool.map(embed_batch, range(0, len(entries), 32)):
                self.query_vectors.update(vectors)

    def embed(self, texts):
        from openai import RateLimitError

        from backend.parsing import tokens

        vectors = [self.cache.get(text) if self.cache else None for text in texts]
        missing = [i for i, vector in enumerate(vectors) if vector is None]
        if missing:
            pending = [texts[i] for i in missing]
            for attempt in range(9):
                self.budget.acquire(sum(tokens(text) for text in pending))
                try:
                    generated = self.provider.embed(pending)
                    break
                except RateLimitError as exc:
                    if attempt == 8:
                        raise
                    headers = exc.response.headers
                    try:
                        delay = float(headers.get("retry-after", 60))
                    except ValueError:
                        delay = 60
                    delay = max(1, delay)
                    print(json.dumps({"rate_limited": True, "retry_after_seconds": delay}),
                          flush=True)
                    deadline = time.monotonic() + delay
                    while time.monotonic() < deadline:
                        time.sleep(min(5, max(0, deadline - time.monotonic())))
            if self.cache:
                self.cache.put(pending, generated)
            for index, vector in zip(missing, generated):
                vectors[index] = vector
        return vectors

    def close(self):
        self.vectors.close()
        self.provider.close()
        if self.cache:
            self.cache.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--split", choices=["train", "dev", "test"], default="test")
    parser.add_argument("--backend", choices=["bm25", "azure"], default="bm25")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--embedding-workers", type=int, default=4)
    parser.add_argument("--embedding-cache", type=Path)
    parser.add_argument("--tokens-per-minute", type=int, default=120000)
    parser.add_argument(
        "--max-queries", type=int, help="Deterministic subset; full corpus retained"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.embedding_workers <= 8:
        parser.error("embedding-workers must be between 1 and 8")
    if args.tokens_per_minute < 24000:
        parser.error("tokens-per-minute must be at least the 24000-token provider batch limit")
    if args.k < 1 or (args.max_queries is not None and args.max_queries < 1):
        parser.error("k and max-queries must be positive")
    corpus, queries, qrels, digest = load_dataset(args.dataset, args.split)
    query_ids = sorted(qrels)[:args.max_queries]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="folio-beir-") as temp:
        retriever = (BM25(corpus) if args.backend == "bm25"
                     else FolioAzure(corpus, Path(temp), args.embedding_workers,
                                     args.embedding_cache, args.tokens_per_minute))
        index_seconds = time.monotonic() - started
        results = []
        try:
            before = time.monotonic()
            if args.backend == "azure":
                retriever.prepare_queries({qid: queries[qid]["text"] for qid in query_ids})
            query_embedding_seconds = time.monotonic() - before
            if args.backend == "azure":
                for query_id in query_ids[:5]:
                    retriever.verify_policy_parity(query_id, args.k)
                print(json.dumps({"production_policy_parity_queries": min(5, len(query_ids)),
                                  "passed": True}), flush=True)
            for query_id in query_ids:
                before = time.monotonic()
                ranked = retriever.search(queries[query_id]["text"], args.k, query_id)
                results.append({"query_id": query_id, "ranked_ids": ranked,
                                "metrics": retrieval_metrics(ranked, qrels[query_id], args.k),
                                "latency_ms": (time.monotonic() - before) * 1000})
                if len(results) % 25 == 0:
                    print(json.dumps({"completed_queries": len(results),
                                      "queries": len(query_ids)}), flush=True)
        finally:
            retriever.close()
    metrics = {key: sum(r["metrics"][key] for r in results) / len(results)
               for key in results[0]["metrics"]}
    report = {
        "schema_version": 1, "benchmark": "beir-document-retrieval", "backend": args.backend,
        "dataset": args.dataset.name, "dataset_sha256": digest, "split": args.split,
        "queries": len(results), "available_queries": len(qrels), "corpus_documents": len(corpus),
        "k": args.k, "metrics": metrics, "index_seconds": index_seconds,
        "query_embedding_seconds": query_embedding_seconds,
        "production_policy_parity_queries": (
            min(5, len(query_ids)) if args.backend == "azure" else 0
        ),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_dirty": bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True
        ).strip()),
        "configuration": getattr(retriever, "config", {"k1": 1.2, "b": 0.75,
                                                     "tokenizer": "unicode word, no stemming"}),
        "limitations": "Document retrieval only. BM25 is an independent lexical reference, "
        "not Folio's current retriever. Azure mode uses Folio embeddings and Qdrant, with "
        "one passage per document and no production score threshold. Neither mode evaluates "
        "generation, ingestion/chunking, permission correctness, or prompt-injection robustness. "
        "API token usage/cost is not measured; failures abort rather than omitting queries.",
        "latency_scope": (
            "Ranking only with precomputed Azure query vectors. The isolated "
            "public corpus is fully authorized, so raw ranking omits always-true policy/scope "
            "predicates; five queries are compared with production filtered search. Embedding "
            "wall time is separate. Not production filtered or end-to-end API latency."
            if args.backend == "azure" else "BM25 lexical ranking only, excluding index build."
        ),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
