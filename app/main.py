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
import sys
from pathlib import Path
from typing import Optional

# Vercel (FastAPI preset) loads this file as the entry point: make sure the
# project root is importable so `app.*` resolves wherever it's run from
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# settings from <project>/.env (DATABASE_URL, SUPABASE_URL, ...) — real environment variables win
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.api import account, connections, datasets, exports, files, investigations, model_files, recon, reconciliations, semantic, snapshots
from app.auth import User, auth_enabled, require_user, signup_allowed
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
def _allowed_origins() -> list[str]:
    """The web app's address(es). BIP_WEB_URL counts too, and a trailing "/"
    (an easy slip when pasting an address) doesn't stop it matching."""
    extra = os.environ.get("BIP_CORS_ORIGINS", "").split(",") + [os.environ.get("BIP_WEB_URL", "")]
    return list(dict.fromkeys(["http://localhost:3000", "http://127.0.0.1:3000",
                               *[o.strip().rstrip("/") for o in extra if o.strip()]]))


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
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


@app.exception_handler(RuntimeError)
async def _misconfigured(request: Request, exc: RuntimeError) -> JSONResponse:
    # e.g. DATABASE_URL missing on Vercel: say so instead of a bare 500
    log.error("Server error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _bad_input(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# every data route needs a signed-in user (when sign-in is configured) and
# only ever sees that user's own records
for module in (model_files, connections, semantic, files, datasets, recon, snapshots, reconciliations, investigations, exports):
    app.include_router(module.router, dependencies=[Depends(require_user)])
app.include_router(account.router)  # sign-in itself is open


@app.get("/me", tags=["meta"])
def me(user: Optional[User] = Depends(require_user)) -> dict:
    """Who is signed in (the web app shows it; also a cheap "is my token ok" check)."""
    if user is None:
        return {"auth": False, "username": None, "name": "Local workspace"}
    return {"auth": True, "username": user.username, "name": user.name or user.username}


@app.get("/api/info", tags=["meta"])
def info() -> dict:
    from app.store.blobs import blobs_from_env

    # the web app reads this first: show the sign-in screen or not, offer "Create account" or not
    return {"product": "BI Investigation & Trust Platform", "version": app.version, "docs": "/docs",
            "auth": auth_enabled(), "signup": auth_enabled() and signup_allowed(),
            # with cloud storage, the browser uploads files straight to it (no size limit from the API host)
            "direct_upload": blobs_from_env().enabled}


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/database", tags=["meta"])
def health_database() -> JSONResponse:
    """Setup check: can the API reach its database and storage? (No data is read.)"""
    from app.store.blobs import blobs_from_env

    out: dict = {"database": "postgres" if os.environ.get("DATABASE_URL") else "sqlite (local file)",
                 "storage": "supabase" if blobs_from_env().enabled else "local disk", "sign_in": auth_enabled()}
    try:
        db = get_store().db
        out["accounts"] = db.count_users()
        out["status"] = "ok"
        return JSONResponse(out)
    except Exception as e:  # noqa: BLE001 - report the reason, it's what the person setting up needs
        out["status"] = "error"
        out["error"] = f"{type(e).__name__}: {e}"
        return JSONResponse(out, status_code=500)


@app.get("/stats", tags=["meta"], dependencies=[Depends(require_user)])
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
# This server's root only hands over to it (also translating old "#/..."
# links from the first version of the app).
WEB_URL = os.environ.get("BIP_WEB_URL", "http://localhost:3000").rstrip("/")


@app.get("/", include_in_schema=False)
def web_app() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "handoff.html").read_text(encoding="utf-8").replace("__WEB_URL__", json.dumps(WEB_URL)))
