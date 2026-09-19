from typing import Literal

from pydantic import BaseModel, Field


class Block(BaseModel):
    text: str
    content_type: Literal["text", "table", "picture"] = "text"
    section: str = ""
    page: int | None = None


class AccessPolicy(BaseModel):
    tenant_id: str = ""
    owner_id: str = ""
    allowed_roles: list[str] = Field(default_factory=list)
    allowed_users: list[str] = Field(default_factory=list)
    classification: Literal["internal", "confidential", "restricted"] = "internal"


class Chunk(Block, AccessPolicy):
    chunk_id: str
    document_id: str
    filename: str
    page_end: int | None = None
    token_count: int


class Document(AccessPolicy):
    document_id: str
    filename: str
    status: str = "queued"
    created_at: str
    size: int
    chunk_count: int = 0
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    fingerprint: str | None = None
    reused: bool = False
    timings_ms: dict[str, float] = Field(default_factory=dict)
    processing: dict = Field(default_factory=dict)
    stage_started_at: str | None = None
    completed_at: str | None = None


class Source(Chunk):
    citation: int
    score: float


class ChatRequest(BaseModel):
    model_config = {"extra": "forbid"}
    question: str = Field(min_length=1, max_length=4000)
    document_ids: list[str] = Field(default_factory=list, max_length=100)


class Answer(BaseModel):
    answer: str
    sources: list[Source]
    retrieved_count: int
    grounded: bool
    duration_ms: int
    generative_ui: dict | None = None
    security_trace: dict | None = None
