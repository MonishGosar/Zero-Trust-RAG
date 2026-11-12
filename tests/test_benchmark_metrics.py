import math

import pytest

from scripts.benchmark_retrieval import BM25, EmbeddingCache, retrieval_metrics


def test_metrics_penalize_missing_documents_and_rank():
    metrics = retrieval_metrics(["wrong", "a"], {"a": 1, "b": 1}, 10)
    assert metrics["recall@10"] == 0.5
    assert metrics["mrr@10"] == 0.5
    assert metrics["ndcg@10"] == pytest.approx((1 / math.log2(3)) / (1 + 1 / math.log2(3)))
    assert retrieval_metrics([], {"a": 1}, 10)["ndcg@10"] == 0


def test_graded_ndcg_uses_trec_linear_gain_and_rejects_duplicates():
    metrics = retrieval_metrics(["b", "a"], {"a": 3, "b": 1}, 2)
    assert metrics["ndcg@2"] == pytest.approx((1 + 3 / math.log2(3)) / (3 + 1 / math.log2(3)))
    with pytest.raises(ValueError, match="Duplicate"):
        retrieval_metrics(["a", "a"], {"a": 1}, 10)


def test_bm25_reference_prefers_relevant_terms_and_excludes_identical_id():
    model = BM25({"a": {"text": "unique enzyme molecule"},
                  "b": {"text": "calendar holiday leave"}})
    assert model.search("enzyme", 10, "query") == ["a"]
    assert model.search("enzyme", 10, "a") == []


def test_embedding_cache_persists_and_separates_text_and_deployments(tmp_path):
    path = tmp_path / "cache.sqlite"
    cache = EmbeddingCache(path, {"deployment": "one"})
    cache.put(["public document"], [[0.1, 0.2]])
    cache.close()
    cache = EmbeddingCache(path, {"deployment": "one"})
    assert cache.get("public document") == [0.1, 0.2]
    assert cache.get("different document") is None
    cache.close()
    cache = EmbeddingCache(path, {"deployment": "two"})
    assert cache.get("public document") is None
    cache.close()
