"""Only the browser tests import this app; no Azure requests or real user data."""

import tempfile
from pathlib import Path

from backend.config import Settings
from backend.main import create_app
from tests.fakes import FakeAzure


class BrowserAzure(FakeAzure):
    def answer(self, question, sources):
        answer = super().answer(question, sources)
        if "chart" in question.lower():
            return {"answer": answer, "generative_ui": {"root": {
                "component": "Chart", "props": {"title": "APAC revenue", "variant": "bar",
                "data": [{"label": "APAC", "revenue": 82}], "citations": [1]},
            }}}
        return answer

app = create_app(
    Settings(
        _env_file=None,
        data_dir=Path(tempfile.mkdtemp(prefix="folio-browser-test-")),
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key="test-only",
        azure_openai_chat_deployment="test-chat",
        azure_openai_embedding_deployment="test-embedding",
    ),
    BrowserAzure(),
)
