import json

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.evidence import check_financial_evidence
from backend.main import create_app
from backend.models import Source
from tests.auth_helpers import sign_in
from tests.fakes import FakeAzure
from tests.test_pipeline import upload_ready


@pytest.fixture
def setup(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, financial_evidence_checks=True)
    provider = FakeAzure()
    with TestClient(create_app(settings, provider)) as client:
        sign_in(client)
        yield client, provider


def sources():
    return [Source(chunk_id="one", document_id="one", filename="finance.md", citation=1,
                   score=1, token_count=20, text="APAC revenue was $82M, growth 12%, loss -$5M."),
            Source(chunk_id="two", document_id="two", filename="europe.md", citation=2,
                   score=1, token_count=10, text="Europe revenue was $65M, growth 8%.")]


@pytest.mark.parametrize("answer", [
    "APAC revenue was $82 million [1].", "Growth was 12 percent [1].",
    "APAC $82M [1]. Europe $65M [2].", "The loss was -$5 million [1].",
    "APAC revenue was $82,000,000. [1]", "Revenue was $0.082 billion [1].",
])
def test_supported_financial_literals(answer):
    assert check_financial_evidence(answer, sources())["status"] == "passed"


@pytest.mark.parametrize("answer", [
    "APAC revenue was $90M [1].", "APAC revenue was $82M [2].",
    "APAC $82M [1]. Europe $65M [1].", "APAC $82M [1] and Europe $90M.",
    "APAC grew 112% [1].", "The loss was $5M [1].", "APAC revenue was $82 billion [1].",
])
def test_unsupported_values_and_wrong_citation_bindings(answer):
    assert check_financial_evidence(answer, sources())["status"] == "failed"


def test_nonfinancial_prose_is_explicitly_not_checked_and_entity_entailment_is_not_claimed():
    result = check_financial_evidence("Onboarding is the priority [1].", sources())
    assert result["status"] == "not_checked"
    # Same literal / wrong entity is deliberately a documented limitation of this guard.
    assert check_financial_evidence("Europe revenue was $82M [1].", sources())["status"] == "passed"


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
def test_valid_reference_with_invented_amount_is_withheld_at_api_boundary(setup, route):
    client, provider = setup
    upload_ready(client)
    provider.answer_text = "APAC revenue was $90M. [1]"
    response = client.post(route, json={"question": "APAC revenue?"})
    if route.endswith("stream"):
        body = [json.loads(line) for line in response.text.splitlines()][-1]
    else:
        body = response.json()
    assert response.status_code == 200
    assert body["grounded"] is False
    assert body["sources"] == [] and body["generative_ui"] is None
    assert body["evidence_checks"]["status"] == "failed"
    assert "$90M" not in response.text
