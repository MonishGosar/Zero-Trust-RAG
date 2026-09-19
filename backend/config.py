from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr = SecretStr("")
    azure_openai_chat_deployment: str = ""
    azure_openai_embedding_deployment: str = ""
    azure_openai_embedding_endpoint: str = ""
    azure_openai_embedding_api_key: SecretStr = SecretStr("")
    azure_openai_api_version: str = ""
    qdrant_url: str = ""
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_collection: str = "documents_v1"
    data_dir: Path = ROOT / ".data"
    auth_jwt_secret: SecretStr = SecretStr("")
    auth_cookie_secure: bool = False
    auth_allowed_origins: list[str] = [
        "http://127.0.0.1:3000", "http://localhost:3000", "http://127.0.0.1:3100",
        "http://127.0.0.1:3001", "http://localhost:3001",
    ]
    max_upload_mb: int = Field(default=20, ge=1, le=100)
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    retrieval_score_threshold: float = Field(default=0.25, ge=-1, le=1)
    pdf_parsing_mode: Literal["auto", "docling"] = "auto"
    embedding_batch_size: int = Field(default=32, ge=1, le=128)
    embedding_batch_max_tokens: int = Field(default=24000, ge=6000, le=100000)

    @field_validator("azure_openai_endpoint", "azure_openai_embedding_endpoint")
    @classmethod
    def valid_endpoint(cls, value: str) -> str:
        if value:
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.query:
                raise ValueError("Azure endpoint must be an HTTPS resource URL without a query.")
        return value.rstrip("/")

    @property
    def missing(self) -> list[str]:
        names = [
            "azure_openai_endpoint",
            "azure_openai_api_key",
            "azure_openai_chat_deployment",
            "azure_openai_embedding_deployment",
        ]
        return [
            name.upper()
            for name in names
            if not (
                getattr(self, name).get_secret_value()
                if isinstance(getattr(self, name), SecretStr)
                else getattr(self, name)
            )
        ]
