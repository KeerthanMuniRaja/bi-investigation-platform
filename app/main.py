"""
BI Investigation & Trust Platform — API + web app entrypoint.

Run locally:  uvicorn app.main:app --reload --port 8000   (or `npm run dev` in web/ for API + web app)
Web app:      http://localhost:3000/  (Next.js, web/) — http://localhost:8000/ hands over to it
API docs:     http://localhost:8000/docs
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import connections, datasets, exports, files, investigations, model_files, recon, reconciliations, semantic, snapshots
from app.connectors.base import CapabilityError, ConnectorError
from app.errors import NotFoundError
from app.sources.files import FileReadError
from app.store.database import Store, get_store

STATIC_DIR = Path(__file__).resolve().parent / "static"
log = logging.getLogger("bip")

app = FastAPI(
    title="BI Investigation & Trust Platform",
    version="1.0.0",
    description=(
        "Reconcile BI dashboards (Power BI, Tableau, or any tool's exports) against Excel and each other, "
        "explain the differences, trace where numbers come from, assess change impact, and keep "
        "evidence-backed investigation cases."
    ),
)

# The Next.js frontend (web/) calls this API directly from the browser —
# directly rather than through the Next proxy so multi-GB .pbix uploads
# stream straight here. Extra origins: BIP_CORS_ORIGINS="https://a,https://b".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000",
                   *[o.strip() for o in os.environ.get("BIP_CORS_ORIGINS", "").split(",") if o.strip()]],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


# ------------------------------------------------------------ error mapping
@app.exception_handler(KeyError)
async def _not_found(request: Request, exc: KeyError) -> JSONResponse:
    message = str(exc.args[0]) if exc.args else "not found"
    if isinstance(exc, NotFoundError) or message.endswith("not found"):
        return JSONResponse(status_code=404, content={"detail": message})
    # a bare KeyError is a bug, not a missing object: log it and say so
    log.error("Unhandled KeyError on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": f"Internal error (missing key {message!r}) — see the server log."})


@app.exception_handler(FileNotFoundError)
async def _missing_path(_: Request, exc: FileNotFoundError) -> JSONResponse:
    # e.g. a PBIP folder that was moved or deleted after the connection was created
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(CapabilityError)
async def _unsupported(_: Request, exc: CapabilityError) -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": str(exc)})


@app.exception_handler(NotImplementedError)
async def _not_implemented(_: Request, exc: NotImplementedError) -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": str(exc)})


@app.exception_handler(ConnectorError)
async def _connector_failed(_: Request, exc: ConnectorError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(FileReadError)
async def _bad_file(_: Request, exc: FileReadError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _bad_input(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


for module in (model_files, connections, semantic, files, datasets, recon, snapshots, reconciliations, investigations, exports):
    app.include_router(module.router)


@app.get("/api/info", tags=["meta"])
def info() -> dict:
    return {"product": "BI Investigation & Trust Platform", "version": app.version, "docs": "/docs"}


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/stats", tags=["meta"])
def stats(store: Store = Depends(get_store)) -> dict:
    runs = store.data_reconciliations.values()
    invs = store.investigations.values()
    open_statuses = {"OPEN", "INVESTIGATING"}
    return {
        "connections": len(store.connection_records),
        "files": len(store.files),
        "datasets": len(store.datasets),
        "templates": len(store.definitions),
        "reconciliations": len(runs),
        "reconciliations_with_differences": sum(1 for r in runs if r.status != "MATCHED"),
        "average_match_rate_pct": round(sum(r.summary.match_rate_pct for r in runs) / len(runs), 2) if runs else None,
        "rows_compared": sum(r.summary.total_keys for r in runs),
        "investigations": len(invs),
        "open_investigations": sum(1 for i in invs if i.status.value in open_statuses),
        "recent_runs": [
            {"reconciliation_id": r.reconciliation_id, "name": r.name, "status": r.status,
             "match_rate_pct": r.summary.match_rate_pct, "created_at": r.created_at,
             "difference": r.value_summaries[0].difference if r.value_summaries else None}
            for r in runs[:8]
        ],
    }


# ------------------------------------------------------------------ web app
# The app people use is the Next.js frontend (web/), with readable URLs.
# This server's root only hands over to it, translating the previous
# interface's "#/..." addresses; the previous interface stays at /legacy.
WEB_URL = os.environ.get("BIP_WEB_URL", "http://localhost:3000").rstrip("/")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def web_app() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "handoff.html").read_text(encoding="utf-8").replace("__WEB_URL__", json.dumps(WEB_URL)))


@app.get("/legacy", include_in_schema=False)
def legacy_web_app() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
