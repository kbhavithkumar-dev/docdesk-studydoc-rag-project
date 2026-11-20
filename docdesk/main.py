"""FastAPI routes, application startup, and browser request protections."""

from contextlib import asynccontextmanager
from pathlib import Path
import logging
import secrets
import psycopg
from psycopg_pool import PoolTimeout
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel, StringConstraints
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders, UploadFile
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .documents import DocumentError
from .gemini import Gemini, ProviderError
from .retrieval import ask, index_document
from .settings import MAX_UPLOAD_BYTES, Settings
from .store import IndexFull, Store

STATIC = Path(__file__).with_name("static")
LOGGER = logging.getLogger(__name__)
DocumentId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class AskRequest(BaseModel):
    question: Question
    document_id: DocumentId | None = None


class RequestLimits:
    """Bound the request stream before a multipart parser can spool a large upload."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def protected_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Cache-Control"] = "no-store"
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'"
                )
            await send(message)

        path = scope["path"]
        headers = Headers(scope=scope)
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            # Cross-origin browser requests cannot send this header without CORS approval.
            if headers.get("X-DocDesk-Request") != "1":
                await JSONResponse({"detail": "Missing DocDesk request header."}, 403)(
                    scope, receive, protected_send,
                )
                return

        limit = MAX_UPLOAD_BYTES + 64 * 1024 if path == "/api/documents" else 16 * 1024
        declared = headers.get("Content-Length", "0")
        if not declared.isdecimal() or len(declared) > 12 or int(declared) > limit:
            await JSONResponse({"detail": "Request body is too large or invalid."}, 413)(
                scope, receive, protected_send,
            )
            return

        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, "Request body is too large.")
            return message

        await self.app(scope, bounded_receive, protected_send)


def create_app(settings: Settings | None = None, *, transport: httpx.BaseTransport | None = None):
    """Construct an app. Tests can pass a fake HTTP transport for Gemini."""
    settings = settings or Settings.from_env()
    store = Store(settings.database_url, settings.embedding_model)

    @asynccontextmanager
    async def lifespan(app):
        # Pool startup and database setup are blocking, so run them off the event loop.
        try:
            await run_in_threadpool(store.open)
            await run_in_threadpool(store.initialize)
            with httpx.Client(timeout=httpx.Timeout(45, connect=8), transport=transport) as client:
                app.state.gemini = Gemini(settings, client)
                yield
        finally:
            await run_in_threadpool(store.close)

    app = FastAPI(
        title="DocDesk", version="2.0.0", lifespan=lifespan,
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(RequestLimits)
    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
    basic = HTTPBasic(auto_error=False)

    def authorize(credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]):
        # With no password, run on loopback for local use as shown in the README.
        if not settings.password:
            return
        username = credentials.username if credentials else ""
        password = credentials.password if credentials else ""
        valid_user = secrets.compare_digest(username.encode(), settings.username.encode())
        valid_password = secrets.compare_digest(password.encode(), settings.password.encode())
        if not (valid_user and valid_password):
            raise HTTPException(401, "Sign in to DocDesk.", {"WWW-Authenticate": 'Basic realm="DocDesk"'})

    @app.exception_handler(ProviderError)
    async def provider_failure(request: Request, exc: ProviderError):
        LOGGER.warning("Gemini request failed for %s with status %s", request.url.path, exc.status)
        return JSONResponse({"detail": str(exc)}, exc.status)

    @app.exception_handler(PoolTimeout)
    @app.exception_handler(psycopg.Error)
    async def database_failure(request: Request, exc: Exception):
        LOGGER.error("PostgreSQL operation failed with code %s", getattr(exc, "sqlstate", None))
        return JSONResponse({"detail": "The document index is temporarily unavailable. Try again."}, 503)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/readyz")
    def readiness():
        store.ping()
        return {"status": "ready"}

    @app.get("/", dependencies=[Depends(authorize)])
    def home():
        return FileResponse(STATIC.parent / "index.html")

    @app.get("/api/documents", dependencies=[Depends(authorize)])
    def list_documents():
        return {"documents": store.list_documents()}

    @app.post("/api/documents", status_code=201, dependencies=[Depends(authorize)])
    async def upload(request: Request, response: Response):
        # Authenticate first, then parse exactly one file and close its temporary handle.
        async with request.form(max_files=1, max_fields=0) as form:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise HTTPException(422, "Choose a document to upload.")
            filename = file.filename or ""
            data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Each file must be at most 8 MB.")
        try:
            # Parsing PDFs and calling Gemini can block, so use a worker thread.
            entry, duplicate = await run_in_threadpool(
                index_document, filename, data, store, request.app.state.gemini,
            )
        except DocumentError as exc:
            raise HTTPException(422, str(exc)) from exc
        except IndexFull as exc:
            raise HTTPException(409, str(exc)) from exc
        if duplicate:
            response.status_code = 200
        return {"document": entry, "duplicate": duplicate}

    @app.delete("/api/documents/{document_id}", status_code=204, dependencies=[Depends(authorize)])
    def remove(document_id: DocumentId):
        if not store.delete(document_id):
            raise HTTPException(404, "This document is no longer in the library.")
        return Response(status_code=204)

    @app.post("/api/ask", dependencies=[Depends(authorize)])
    def answer(body: AskRequest, request: Request):
        if body.document_id and not store.has_documents(body.document_id):
            raise HTTPException(404, "This document is no longer in the library.")
        return ask(body.question, body.document_id, store, request.app.state.gemini)

    return app
