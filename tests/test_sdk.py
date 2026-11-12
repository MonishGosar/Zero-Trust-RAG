import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from folio_client import Answer, AsyncFolioClient

from backend.config import Settings
from backend.main import create_app
from tests.auth_helpers import sign_in
from tests.fakes import FakeAzure
from tests.test_security import account, upload


def test_sdk_preserves_server_permissions_and_returns_typed_evidence(tmp_path):
    provider = FakeAzure()
    settings = Settings(_env_file=None, data_dir=tmp_path, financial_evidence_checks=True)
    with TestClient(create_app(settings, provider)) as admin:
        sign_in(admin)
        account(admin, "alice@example.test", ["employee"])
        account(admin, "bob@example.test", ["finance"])
        doc_id = upload(admin, roles=["finance"])["document"]["document_id"]
        transport = httpx.ASGITransport(app=admin.app)

        async def exercise():
            async with AsyncFolioClient("http://localhost", transport=transport) as alice:
                with pytest.raises(httpx.HTTPStatusError) as error:
                    await alice.retrieve("APAC")
                assert error.value.response.status_code == 401
                await alice.login("alice@example.test", "test-workspace-password")
                assert await alice.retrieve("APAC") == []
                with pytest.raises(httpx.HTTPStatusError) as error:
                    await alice.ask("APAC", document_ids=[doc_id])
                assert error.value.response.status_code == 409
            async with AsyncFolioClient("http://localhost", transport=transport) as bob:
                await bob.login("bob@example.test", "test-workspace-password")
                result = await bob.ask("APAC revenue?", document_ids=[doc_id])
                assert isinstance(result, Answer)
                assert result.sources[0].document_id == doc_id
                assert result.security_trace["tenant_id"] == "acme"
                assert result.evidence_checks["status"] == "passed"
                streamed = await bob.stream_answer("APAC revenue?")
                assert streamed.sources[0].document_id == doc_id
                with pytest.raises(httpx.HTTPStatusError):
                    await bob.login("alice@example.test", "incorrect-password")
                with pytest.raises(httpx.HTTPStatusError) as error:
                    await bob.retrieve("APAC")
                assert error.value.response.status_code == 401
                await bob.login("bob@example.test", "test-workspace-password")
                await bob.logout()
                with pytest.raises(httpx.HTTPStatusError) as error:
                    await bob.ask("APAC")
                assert error.value.response.status_code == 401

        asyncio.run(exercise())

        async def bearer_session():
            async with AsyncFolioClient(
                "http://localhost", transport=transport,
                token=admin.cookies.get("folio_session"),
            ) as owner:
                assert (await owner.retrieve("APAC"))[0].document_id == doc_id
                assert admin.post("/auth/logout").status_code == 200
                with pytest.raises(httpx.HTTPStatusError) as error:
                    await owner.retrieve("APAC")
                assert error.value.response.status_code == 401

        asyncio.run(bearer_session())


def test_sdk_surfaces_post_http_success_stream_errors():
    async def respond(request):
        return httpx.Response(200, text='{"type":"error","message":"revoked"}\n')

    async def exercise():
        async with AsyncFolioClient("https://example.test",
                                    transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(RuntimeError, match="stopped this answer"):
                await client.stream_answer("question")

    asyncio.run(exercise())


def test_sdk_closes_stream_after_final_answer():
    class ResponseStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield (b'{"type":"answer","answer":"No context","sources":[],'
                   b'"grounded":false,"retrieved_count":0,"duration_ms":1}\n')

        async def aclose(self):
            self.closed = True

    stream = ResponseStream()

    async def respond(request):
        return httpx.Response(200, stream=stream)

    async def exercise():
        async with AsyncFolioClient("https://example.test",
                                    transport=httpx.MockTransport(respond)) as client:
            await client.stream_answer("question")
            assert stream.closed

    asyncio.run(exercise())


@pytest.mark.parametrize("url", ["http://external.example", "https://user:secret@example.test",
                                 "https://example.test?key=secret"])
def test_sdk_rejects_unsafe_base_urls_without_echoing_secrets(url):
    with pytest.raises(ValueError) as error:
        AsyncFolioClient(url)
    assert "secret" not in str(error.value)
