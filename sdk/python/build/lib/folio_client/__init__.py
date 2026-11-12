"""Preview Folio API client; authorization remains entirely on the server."""

from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field


class Source(BaseModel):
    model_config = ConfigDict(extra="allow")
    document_id: str
    chunk_id: str
    text: str
    filename: str
    citation: int
    score: float


class Answer(BaseModel):
    model_config = ConfigDict(extra="allow")
    answer: str
    sources: list[Source]
    grounded: bool
    retrieved_count: int
    duration_ms: int
    security_trace: dict | None = None
    evidence_checks: dict | None = None
    generative_ui: dict | None = None


class StreamEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["status", "answer", "error"]


class User(BaseModel):
    user_id: str
    email: str
    tenant_id: str
    roles: list[str] = Field(default_factory=list)


class AsyncFolioClient:
    """Use a revocable Folio Bearer token or cookie-based login.

    No client-side roles, ACL bypass, cache, automatic retry or permission inference.
    A stream can fail after HTTP 200; consume its error events or use stream_answer.
    """

    def __init__(self, base_url: str, *, token: str | None = None, timeout: float = 180,
                 transport: httpx.AsyncBaseTransport | None = None):
        url = httpx.URL(base_url)
        local_http = url.scheme == "http" and url.host in {"localhost", "127.0.0.1", "::1"}
        if (url.scheme != "https" and not local_http) or url.username or url.password:
            raise ValueError(
                "Use an HTTPS API URL, or localhost HTTP, without embedded credentials."
            )
        if url.query or url.fragment:
            raise ValueError("API base URL must not contain a query or fragment.")
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            follow_redirects=False,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self):
        await self._http.aclose()

    async def login(self, email: str, password: str) -> User:
        # A failed identity switch must not silently retain the previous user's session.
        self._http.headers.pop("Authorization", None)
        self._http.cookies.clear()
        response = await self._http.post("/auth/login", json={"email": email, "password": password})
        response.raise_for_status()
        return User.model_validate(response.json()["user"])

    async def logout(self):
        response = await self._http.post("/auth/logout")
        response.raise_for_status()
        self._http.headers.pop("Authorization", None)
        self._http.cookies.clear()

    @staticmethod
    def _payload(question: str, document_ids: list[str] | None):
        return {"question": question, "document_ids": document_ids or []}

    async def retrieve(
        self, question: str, *, document_ids: list[str] | None = None
    ) -> list[Source]:
        response = await self._http.post("/retrieve", json=self._payload(question, document_ids))
        response.raise_for_status()
        return [Source.model_validate(source) for source in response.json()]

    async def ask(self, question: str, *, document_ids: list[str] | None = None) -> Answer:
        response = await self._http.post("/chat", json=self._payload(question, document_ids))
        response.raise_for_status()
        return Answer.model_validate(response.json())

    async def stream_events(self, question: str, *, document_ids: list[str] | None = None
                            ) -> AsyncIterator[StreamEvent]:
        async with self._http.stream("POST", "/chat/stream",
                                     json=self._payload(question, document_ids)) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.strip():
                    yield StreamEvent.model_validate_json(line)

    async def stream_answer(
        self, question: str, *, document_ids: list[str] | None = None
    ) -> Answer:
        async with aclosing(self.stream_events(question, document_ids=document_ids)) as events:
            async for event in events:
                if event.type == "error":
                    raise RuntimeError("Folio stopped this answer; inspect server audit records.")
                if event.type == "answer":
                    return Answer.model_validate(event.model_dump())
        raise RuntimeError("Folio stream ended without an answer.")


__all__ = ["Answer", "AsyncFolioClient", "Source", "StreamEvent", "User"]
