"""A bounded two-stage pipeline: serialized parsing overlaps Azure/indexing work."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backend.models import Document
from backend.parsing import chunk_document, parse_document

logger = logging.getLogger(__name__)
# Bump when parsing/chunking semantics change so identical bytes are reprocessed.
PIPELINE_VERSION = "native-pdf-v2"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    document: Document
    path: Path
    started: float = field(default_factory=time.perf_counter)
    stage_started: float = field(init=False)

    def __post_init__(self):
        self.stage_started = self.started


class IngestionPipeline:
    def __init__(self, settings, catalog, vectors, provider, safe_error):
        self.settings = settings
        self.catalog = catalog
        self.vectors = vectors
        self.provider = provider
        self.safe_error = safe_error
        # Upload admission in main.py caps accepted active jobs at five, including
        # jobs waiting between these workers. Never increase PDF parsing threads.
        self.parsing = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parsing")
        self.embedding = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embedding")

    def submit(self, document: Document, path: Path):
        self.parsing.submit(self.parse, Job(document, path))

    def transition(self, job: Job, status: str):
        now = time.perf_counter()
        document = job.document
        document.timings_ms[document.status] = round((now - job.stage_started) * 1000, 2)
        document.status = status
        document.stage_started_at = utc_now()
        job.stage_started = now
        if status in {"ready", "failed"}:
            document.completed_at = document.stage_started_at
            document.timings_ms["total"] = round((now - job.started) * 1000, 2)
        self.catalog.save(document)

    def fail(self, job: Job, exc: Exception):
        logger.warning("Ingestion failed (%s)", type(exc).__name__)
        job.document.error = self.safe_error(exc)
        self.transition(job, "failed")
        try:
            self.vectors.remove(job.document.document_id)
        except Exception:
            logger.warning("Partial index cleanup unavailable")

    def parse(self, job: Job):
        try:
            self.transition(job, "parsing")
            blocks, warnings = parse_document(
                job.path, details=job.document.processing, pdf_mode=self.settings.pdf_parsing_mode
            )
            job.document.warnings = warnings
            self.transition(job, "chunking")
            chunks = chunk_document(blocks, job.document.document_id, job.document.filename)
            acl = {key: getattr(job.document, key) for key in (
                "tenant_id", "owner_id", "allowed_roles", "allowed_users", "classification"
            )}
            chunks = [chunk.model_copy(update=acl) for chunk in chunks]
            job.document.chunk_count = len(chunks)
            self.transition(job, "waiting_embedding")
            self.embedding.submit(self.index, job, chunks)
        except Exception as exc:
            self.fail(job, exc)

    def index(self, job: Job, chunks):
        try:
            self.transition(job, "embedding")
            vectors = self.provider.embed([chunk.text for chunk in chunks])
            self.transition(job, "indexing")
            self.vectors.upsert(chunks, vectors)
            self.catalog.save_chunks(chunks)
            self.transition(job, "ready")
        except Exception as exc:
            self.fail(job, exc)

    def close(self):
        # Parsing can still submit embedding jobs: drain it first, then drain
        # embedding before the application closes storage and Azure clients.
        self.parsing.shutdown(wait=True)
        self.embedding.shutdown(wait=True)
