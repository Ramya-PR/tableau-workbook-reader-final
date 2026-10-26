"""FastAPI application.

Run (from ``backend/``)::

    uvicorn app.main:create_app --factory --reload --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, File, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .archive import DataFile, WorkbookArchive
from .config import Settings, load_settings
from .errors import AppError, invalid_input, not_found, source_unavailable
from .migration import assess as assess_migration
from .parser import parse_workbook
from .preview import describe_file, hyper_available, list_tables, read_rows
from .sources import WorkbookSource, build_sources, save_upload
from .store import Session, SessionStore

log = logging.getLogger("twbparser")


class ParseRequest(BaseModel):
    source: str = Field(description="'public' or 'server'")
    reference: str = Field(min_length=1, max_length=2000, description="Tableau Public URL/name, or a server workbook id")


def create_app(
    settings: Settings | None = None,
    sources: dict[str, WorkbookSource] | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    sources = sources if sources is not None else build_sources(settings)
    store = SessionStore(settings.session_ttl_seconds, settings.max_sessions)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        store.close()  # remove every temp directory on shutdown

    app = FastAPI(title="Tableau Workbook Parser", version=__version__, lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.to_detail()})

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("Unhandled error")
        return JSONResponse(
            status_code=500,
            content={"detail": {"code": "INTERNAL_ERROR", "message": "Something went wrong on the server."}},
        )

    api = APIRouter(prefix="/api")

    # -- helpers -----------------------------------------------------------
    def get_source(name: str) -> WorkbookSource:
        src = sources.get(name)
        if src is None:
            raise not_found(f"Unknown source {name!r}.", "UNKNOWN_SOURCE")
        status = src.status()
        if not status.get("available"):
            raise source_unavailable(status.get("reason") or f"The {name} source is not available.")
        return src

    def analyze(session: Session, path: Path) -> dict:
        archive = WorkbookArchive(path, settings)
        metadata = parse_workbook(archive.twb_bytes(), [d.path for d in archive.data_files])
        warnings = list(metadata.pop("warnings"))
        metadata["migration"] = assess_migration(metadata)

        files = []
        for df in archive.data_files:
            df.previewable, df.reason = describe_file(df.kind, df.path)
            files.append(df.to_dict())
        if archive.kind == "twb":
            warnings.append(
                "This is a .twb file, which never contains data extracts. Use the packaged .twbx to preview rows."
            )
        elif not files:
            warnings.append(
                "This .twbx contains no data files, so its data sources are probably live connections."
            )

        session.archive, session.archive_path, session.data_files = archive, path, archive.data_files
        session.filename = path.name
        return {
            "workbook_id": session.id,
            "source": session.source,
            "filename": path.name,
            "file_kind": archive.kind,
            "metadata": metadata,
            "data_files": files,
            "warnings": warnings,
        }

    def data_file(session: Session, file: str) -> tuple[DataFile, Path]:
        match = next((d for d in session.data_files if d.path == file), None)
        if match is None:
            raise not_found("That data file is not part of this workbook.", "UNKNOWN_DATA_FILE")
        if not match.previewable:
            raise AppError("PREVIEW_UNAVAILABLE", match.reason or "This file cannot be previewed.", 501)
        assert session.archive is not None
        return match, session.archive.extract(match.path, session.data_dir)

    # -- routes ------------------------------------------------------------
    @api.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @api.get("/capabilities")
    def capabilities():
        return {
            "version": __version__,
            "sources": {name: src.status() for name, src in sources.items()},
            "preview": {"hyper": hyper_available(), "csv": True},
            "limits": {
                "max_file_mb": settings.max_file_mb,
                "default_preview_rows": settings.default_preview_rows,
                "max_preview_rows": settings.max_preview_rows,
                "session_ttl_minutes": settings.session_ttl_minutes,
            },
        }

    @api.get("/sources/{source}/workbooks")
    def list_workbooks(
        source: str,
        q: str | None = Query(None, max_length=200),
        profile: str | None = Query(None, max_length=100),
    ):
        refs = get_source(source).list_workbooks(q, profile=profile)
        return {"workbooks": [r.to_dict() for r in refs]}

    @api.post("/workbooks/parse")
    def parse(body: ParseRequest):
        if body.source not in ("public", "server"):
            raise invalid_input("source must be 'public' or 'server' (use /workbooks/upload for files).")
        src = get_source(body.source)
        session = store.create(body.source, body.reference[:200])
        try:
            path = src.fetch(body.reference, session.dir / "archive")
            return analyze(session, path)
        except Exception:
            store.delete(session.id)
            raise

    @api.post("/workbooks/upload")
    def upload(file: UploadFile = File(...)):
        session = store.create("upload", file.filename or "upload")
        try:
            path = save_upload(file.file, file.filename, session.dir / "archive", settings.max_file_bytes)
            return analyze(session, path)
        except Exception:
            store.delete(session.id)
            raise

    @api.get("/workbooks/{workbook_id}/preview/tables")
    def preview_tables(workbook_id: str, file: str = Query(..., max_length=500)):
        session = store.get(workbook_id)
        df, path = data_file(session, file)
        return {"file": df.path, "kind": df.kind, "tables": list_tables(path, df.kind)}

    @api.get("/workbooks/{workbook_id}/preview/rows")
    def preview_rows(
        workbook_id: str,
        file: str = Query(..., max_length=500),
        table: str | None = Query(None, max_length=500),
        offset: int = Query(0, ge=0),
        limit: int | None = Query(None, ge=1),
    ):
        session = store.get(workbook_id)
        df, path = data_file(session, file)
        capped = min(limit or settings.default_preview_rows, settings.max_preview_rows)
        return read_rows(path, df.kind, table, offset, capped)

    @api.delete("/workbooks/{workbook_id}")
    def delete_workbook(workbook_id: str):
        return {"deleted": store.delete(workbook_id)}

    app.include_router(api)

    # Optionally serve the built React app from the same process.
    dist = frontend_dist or Path(os.environ.get("FRONTEND_DIST") or Path(__file__).resolve().parents[2] / "frontend" / "dist")
    if dist.is_dir() and (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    return app
