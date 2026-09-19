import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from openai import APIConnectionError, APIStatusError

from backend.azure import AzureProvider, validate_citations
from backend.catalog import Catalog
from backend.config import Settings
from backend.ingestion import PIPELINE_VERSION, IngestionPipeline, utc_now
from backend.models import Answer, ChatRequest, Document
from backend.security import (
    ACLUpdate,
    Security,
    admin_user,
    can_read,
    current_user,
)
from backend.security import (
    router as auth_router,
)
from backend.vectors import VectorStore

logger = logging.getLogger(__name__)
SUPPORTED = {".pdf", ".docx", ".md", ".txt"}


def safe_error(exc: Exception) -> str:
    if isinstance(exc, APIStatusError):
        return (
            f"Azure returned HTTP {exc.status_code}. Check the resource, key, deployment "
            "names, quota, and model compatibility, then try again."
        )
    if isinstance(exc, APIConnectionError):
        return "Cannot reach Azure. Check the endpoint and network connection."
    if isinstance(exc, UnicodeDecodeError):
        return "Text files must use UTF-8 encoding."
    if isinstance(exc, ValueError):
        # Only allow our known validation messages; provider/parser exceptions can contain data.
        message = str(exc)
        if message.startswith(
            (
                "No readable",
                "A table",
                "Document exceeds",
                "Document conversion",
                "Azure returned no answer",
                "Embedding count",
                "Embedding response",
                "Embedding dimensions",
            )
        ):
            return message
    return "Processing failed. Check the parser models, Azure configuration, and Qdrant connection."


def create_app(settings: Settings | None = None, provider=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "uploads").mkdir(exist_ok=True)
        app.state.catalog = Catalog(settings.data_dir / "catalog.sqlite")
        app.state.security = Security(app.state.catalog, settings)
        app.state.vectors = VectorStore(settings)
        app.state.provider = provider or AzureProvider(settings)
        app.state.upload_lock = asyncio.Lock()
        app.state.pipeline = IngestionPipeline(
            settings, app.state.catalog, app.state.vectors, app.state.provider, safe_error
        )
        for document in app.state.catalog.list():
            if document.status not in {"ready", "failed"}:
                document.status = "failed"
                document.error = "Ingestion was interrupted by a restart. Upload the file again."
                document.completed_at = utc_now()
                app.state.catalog.save(document)
        try:
            yield
        finally:
            await asyncio.to_thread(app.state.pipeline.close)
            app.state.vectors.close()
            app.state.provider.close()

    app = FastAPI(title="Folio document intelligence", lifespan=lifespan)
    app.include_router(auth_router)

    @app.middleware("http")
    async def browser_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            if origin not in settings.auth_allowed_origins:
                return JSONResponse({"detail": "Request origin is not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def require_azure():
        if settings.missing and provider is None:
            raise HTTPException(
                503,
                "Add the missing Azure settings to the root .env and restart "
                "the API: " + ", ".join(settings.missing),
            )

    @app.get("/health")
    def health():
        try:
            app.state.vectors.health()
            vector_status = "ready"
        except Exception:
            vector_status = "unavailable"
        return {
            "status": "ok",
            "azure_configured": not settings.missing,
            "missing": settings.missing,
            "vector_store": vector_status,
            "vector_mode": "server" if settings.qdrant_url else "local",
            "chat_deployment": settings.azure_openai_chat_deployment or None,
            "embedding_deployment": settings.azure_openai_embedding_deployment or None,
        }

    @app.get("/documents")
    def documents(user=Depends(current_user)) -> list[Document]:
        return [d for d in app.state.catalog.list() if can_read(user, d)]

    @app.post("/documents", status_code=202)
    async def upload(file: UploadFile = File(...), acl: str = Form("{}"),
                     user=Depends(admin_user)) -> Document:
        # Serialize admission/hash lookup across concurrent HTTP requests. Parsing
        # and Azure work happen in the pipeline, outside this short-lived lock.
        try:
            async with app.state.upload_lock:
                try:
                    policy = ACLUpdate.model_validate_json(acl)
                except ValueError:
                    raise HTTPException(422, "Document permissions are invalid.") from None
                validate_grants(policy, user)
                return await accept_upload(file, policy, user)
        finally:
            await file.close()

    async def accept_upload(file: UploadFile, policy: ACLUpdate, user) -> Document:
        require_azure()
        filename = (file.filename or "document").replace("\\", "/").rsplit("/", 1)[-1]
        if len(filename) > 200:
            raise HTTPException(400, "Filename must be at most 200 characters.")
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED:
            raise HTTPException(415, "Supported formats: PDF, DOCX, Markdown, and UTF-8 text.")
        document_id = str(uuid4())
        path = settings.data_dir / "uploads" / (document_id + suffix)
        identity = (
            PIPELINE_VERSION, suffix, settings.pdf_parsing_mode, app.state.vectors.collection,
            user.tenant_id, user.user_id, policy.model_dump(),
        )
        fingerprint = hashlib.sha256(json.dumps(identity).encode())
        size = 0
        try:
            with path.open("xb") as dest:
                while data := await file.read(1024 * 1024):
                    size += len(data)
                    if size > settings.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, f"Upload limit is {settings.max_upload_mb} MB.")
                    dest.write(data)
                    fingerprint.update(data)
            if not size:
                raise HTTPException(400, "The uploaded file is empty.")
            if suffix == ".pdf":
                with path.open("rb") as source:
                    if not source.read(1024).lstrip().startswith(b"%PDF-"):
                        raise HTTPException(400, "This file is not a valid PDF.")
            existing = app.state.catalog.find_fingerprint(fingerprint.hexdigest())
            if existing:
                path.unlink()
                return existing.model_copy(update={"reused": True})
            active = sum(d.status not in {"ready", "failed"} for d in app.state.catalog.list())
            if active >= 5:
                raise HTTPException(
                    429, "Five documents are already queued. Wait for ingestion to finish."
                )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        document = Document(
            tenant_id=user.tenant_id, owner_id=user.user_id, **policy.model_dump(),
            document_id=document_id,
            filename=filename,
            size=size,
            created_at=datetime.now(timezone.utc).isoformat(),
            stage_started_at=utc_now(),
            fingerprint=fingerprint.hexdigest(),
        )
        app.state.catalog.save(document)
        app.state.security.audit(user, "document_upload", document_id=document_id)
        # Hand the worker its own copy so the 202 response always reports the accepted job.
        app.state.pipeline.submit(document.model_copy(deep=True), path)
        return document

    @app.get("/documents/{document_id}")
    def document_detail(document_id: str, user=Depends(current_user)):
        document = readable(document_id, user)
        return {"document": document, "chunks": app.state.catalog.chunks(document_id)}

    @app.get("/documents/{document_id}/file")
    def original(document_id: str, user=Depends(current_user)):
        document = readable(document_id, user)
        path = (
            settings.data_dir
            / "uploads"
            / (document.document_id + Path(document.filename).suffix.lower())
        )
        if not path.is_file():
            raise HTTPException(404, "Original document is no longer available.")
        app.state.security.audit(user, "document_download", document_id=document_id)
        return FileResponse(path, filename=document.filename)

    def readable(document_id, user):
        document = app.state.catalog.get(document_id)
        if not document or not can_read(user, document):
            app.state.security.audit(user, "permission_denied", action="document_access")
            raise HTTPException(404, "Document not found.")
        return document

    def validate_grants(policy, user):
        with app.state.catalog.connect() as db:
            valid = {r[0] for r in db.execute("SELECT id FROM users WHERE tenant = ?",
                                             (user.tenant_id,))}
        if not set(policy.allowed_users).issubset(valid):
            raise HTTPException(422, "Explicit users must belong to your workspace.")

    @app.patch("/documents/{document_id}/acl")
    def update_acl(document_id: str, policy: ACLUpdate, user=Depends(admin_user)):
        document = readable(document_id, user)
        if document.status not in {"ready", "failed"}:
            raise HTTPException(409, "Wait for ingestion before changing permissions.")
        validate_grants(policy, user)
        # Clear dedup identity on policy change so re-upload cannot reuse stale access.
        values = {**policy.model_dump(), "fingerprint": None}
        app.state.vectors.set_acl(document_id, policy.model_dump())
        updated = app.state.catalog.set_acl(document, values)
        app.state.security.audit(user, "acl_update", document_id=document_id,
                                 **policy.model_dump())
        return updated

    @app.get("/audit")
    def audit(user=Depends(current_user)):
        with app.state.catalog.connect() as db:
            rows = db.execute("SELECT id, event, created_at, data FROM audit_logs "
                              "WHERE tenant = ? AND user_id = ? ORDER BY rowid DESC LIMIT 50",
                              (user.tenant_id, user.user_id)).fetchall()
        return [dict(audit_id=r[0], event=r[1], created_at=r[2], **json.loads(r[3])) for r in rows]

    def scope(request: ChatRequest, user) -> list[str]:
        require_azure()
        if not request.question.strip():
            raise HTTPException(422, "Enter a question.")
        ready = {d.document_id for d in app.state.catalog.list()
                 if d.status == "ready" and can_read(user, d)}
        if request.document_ids and not set(request.document_ids).issubset(ready):
            app.state.security.audit(user, "permission_denied", action="query_scope")
            raise HTTPException(409, "One or more selected documents are unavailable.")
        return request.document_ids or sorted(ready)

    def retrieve(request: ChatRequest, ids: list[str], user):
        if not ids:
            return []
        vector = app.state.provider.embed([request.question])[0]
        sources = app.state.vectors.search(
            vector, ids, settings.retrieval_top_k, settings.retrieval_score_threshold, user=user
        )
        # Bound generation context including potentially large atomic tables.
        selected = []
        used = 0
        for source in sources:
            if used + source.token_count <= 12000:
                selected.append(source.model_copy(update={"citation": len(selected) + 1}))
                used += source.token_count
        return selected

    def trace_query(body, user, ids, sources):
        query_hash = hmac.new(app.state.security.key.encode(), body.question.encode(),
                              hashlib.sha256).hexdigest()
        trace = dict(user_id=user.user_id, email=user.email, tenant_id=user.tenant_id,
                     roles=user.roles, authorized_documents=len(ids),
                     retrieved_chunks=len(sources), context_chunks=len(sources),
                     policy="tenant AND (owner OR role OR explicit user)",
                     query_hash=query_hash,
                     retrieved_chunk_ids=[s.chunk_id for s in sources],
                     retrieved_document_ids=sorted({s.document_id for s in sources}))
        trace["audit_id"] = app.state.security.audit(user, "rag_retrieval", **trace)
        return trace

    def generate(question, sources, started):
        if not sources:
            return Answer(
                answer="No authorized supporting passages were found. "
                "Try another question or ask your administrator for access.",
                sources=[],
                retrieved_count=0,
                grounded=False,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        generated = app.state.provider.answer(question, sources)
        ui = generated.get("generative_ui") if isinstance(generated, dict) else None
        text_value = generated.get("answer", "") if isinstance(generated, dict) else generated
        text, cited, grounded = validate_citations(text_value, sources)
        if ui and grounded:
            available = {source.citation for source in sources}
            ui_citations = ui.get("root", {}).get("props", {}).get("citations", [])
            if not set(ui_citations).issubset(available):
                ui = None
        else:
            ui = None
        return Answer(
            answer=text,
            sources=cited,
            retrieved_count=len(sources),
            grounded=grounded,
            duration_ms=round((time.monotonic() - started) * 1000),
            generative_ui=ui,
        )

    @app.post("/retrieve")
    def retrieve_only(request: ChatRequest, user=Depends(current_user)):
        ids = scope(request, user)
        try:
            sources = retrieve(request, ids, user)
            trace_query(request, user, ids, sources)
            return sources
        except Exception as exc:
            raise HTTPException(502, safe_error(exc)) from None

    @app.post("/chat")
    def chat(request: ChatRequest, user=Depends(current_user)) -> Answer:
        ids = scope(request, user)
        started = time.monotonic()
        try:
            sources = retrieve(request, ids, user)
            trace = trace_query(request, user, ids, sources)
            answer = generate(request.question, sources, started)
            answer.security_trace = trace
            return answer
        except Exception as exc:
            raise HTTPException(502, safe_error(exc)) from None

    @app.post("/chat/stream")
    async def chat_stream(body: ChatRequest, request: Request, user=Depends(current_user)):
        ids = scope(body, user)

        async def events():
            started = time.monotonic()

            def event(kind, data):
                return json.dumps({"type": kind, **data}) + "\n"

            yield event("status", {"stage": "retrieving"})
            try:
                sources = await asyncio.to_thread(retrieve, body, ids, user)
                trace = await asyncio.to_thread(trace_query, body, user, ids, sources)
                if await request.is_disconnected():
                    return
                yield event("status", {"stage": "answering", "retrieved_count": len(sources)})
                answer = await asyncio.to_thread(generate, body.question, sources, started)
                answer.security_trace = trace
                if not await request.is_disconnected():
                    yield event("answer", answer.model_dump())
            except Exception as exc:
                yield event("error", {"message": safe_error(exc)})

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
