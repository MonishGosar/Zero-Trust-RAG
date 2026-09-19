import json

import httpx
import pytest
from openai import OpenAI

from backend.azure import AzureProvider
from backend.config import Settings
from backend.parsing import tokens


def configured(**overrides):
    return Settings(
        _env_file=None,
        azure_openai_endpoint="https://test.openai.azure.com",
        azure_openai_api_key="test-only",
        azure_openai_chat_deployment="chat-deployed",
        azure_openai_embedding_deployment="embedding-deployed",
        **overrides,
    )


def test_v1_and_legacy_resource_routing():
    provider = AzureProvider(configured())
    assert str(provider.client().base_url) == "https://test.openai.azure.com/openai/v1/"
    provider.close()
    provider = AzureProvider(configured(azure_openai_api_version="2024-10-21"))
    assert str(provider.client().base_url) == "https://test.openai.azure.com/openai/"
    provider.close()
    provider = AzureProvider(
        configured(
            azure_openai_embedding_endpoint="https://embedding.openai.azure.com/openai/v1/",
            azure_openai_embedding_api_key="separate-test-key",
        )
    )
    assert str(provider.client(embedding=True).base_url) == (
        "https://embedding.openai.azure.com/openai/v1/"
    )
    provider.close()


def test_embedding_contract_uses_deployment_and_restores_order():
    def respond(request):
        body = json.loads(request.content)
        assert request.url.path == "/openai/v1/embeddings"
        assert body["model"] == "embedding-deployed"
        assert body["input"] == ["first", "second"]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
                    {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
                ],
                "model": "embedding-deployed",
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    provider = AzureProvider(configured())
    provider._clients["embedding"] = OpenAI(
        base_url="https://test.openai.azure.com/openai/v1/",
        api_key="test-only",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    assert provider.embed(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]
    provider.close()


def test_bad_endpoint_rejected():
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(_env_file=None, azure_openai_endpoint="http://example.com")


def test_embedding_batches_obey_count_and_token_limits_and_preserve_order():
    inputs = ["First " * 3500, "Second " * 3500, "third", "fourth", "fifth", "sixth"]
    batches = []

    def respond(request):
        batch = json.loads(request.content)["input"]
        batches.append(batch)
        assert len(batch) <= 3
        assert sum(tokens(t) for t in batch) <= 6000
        return httpx.Response(200, json={
            "object": "list", "model": "embedding-deployed",
            "data": [{"object": "embedding", "index": i,
                      "embedding": [float(inputs.index(text)), 1.0]}
                     for i, text in reversed(list(enumerate(batch)))],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        })

    provider = AzureProvider(configured(embedding_batch_size=3, embedding_batch_max_tokens=6000))
    provider._clients["embedding"] = OpenAI(
        base_url="https://test.openai.azure.com/openai/v1/", api_key="test-only",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    try:
        assert provider.embed(inputs) == [[float(i), 1.0] for i in range(len(inputs))]
        assert list(map(len, batches)) == [1, 3, 2]
        assert provider.embed([]) == []
        with pytest.raises(ValueError, match="token limit"):
            provider.embed(["valid", "overflow " * 9000])
        assert len(batches) == 3
    finally:
        provider.close()
