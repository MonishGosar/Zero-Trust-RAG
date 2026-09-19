from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from backend.models import Chunk, Document


class Catalog:
    """Small local manifest; vectors remain in Qdrant. Each write is transactional."""

    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                                                  data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
            """)

    @contextmanager
    def connect(self):
        # sqlite3's own context manager commits/rolls back but does not close.
        # Close explicitly so completed jobs release file handles on Windows.
        with closing(sqlite3.connect(self.path, timeout=30)) as connection:
            with connection:
                yield connection

    def save(self, document: Document):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO documents VALUES (?, ?)",
                (document.document_id, document.model_dump_json()),
            )

    def list(self) -> list[Document]:
        with self.connect() as db:
            return [
                Document.model_validate_json(row[0])
                for row in db.execute("SELECT data FROM documents ORDER BY rowid DESC")
            ]

    def get(self, document_id: str) -> Document | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM documents WHERE id = ?", (document_id,)).fetchone()
        return Document.model_validate_json(row[0]) if row else None

    def find_fingerprint(self, fingerprint: str) -> Document | None:
        # The manifest is bounded by the local library. No schema migration is needed
        # for documents saved before fingerprints were introduced.
        return next((d for d in self.list()
                     if d.fingerprint == fingerprint and d.status != "failed"), None)

    def save_chunks(self, chunks: list[Chunk]):
        with self.connect() as db:
            db.executemany(
                "INSERT INTO chunks VALUES (?, ?, ?)",
                [(c.chunk_id, c.document_id, c.model_dump_json()) for c in chunks],
            )

    def set_acl(self, document: Document, policy: dict):
        updated = document.model_copy(update=policy)
        with self.connect() as db:
            db.execute("UPDATE documents SET data = ? WHERE id = ?",
                       (updated.model_dump_json(), document.document_id))
            rows = db.execute("SELECT id, data FROM chunks WHERE document_id = ?",
                              (document.document_id,)).fetchall()
            for chunk_id, data in rows:
                chunk = json.loads(data)
                chunk.update(policy)
                db.execute("UPDATE chunks SET data = ? WHERE id = ?", (json.dumps(chunk), chunk_id))
        return updated

    def chunks(self, document_id: str) -> list[dict]:
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM chunks WHERE document_id = ? ORDER BY rowid", (document_id,)
                )
            ]
