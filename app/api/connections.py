"""
Connections to BI platforms, browsing their content (workspaces -> reports
-> pages/tabs -> visuals), previewing a visual's data, and syncing the
metadata graph used by lineage and impact analysis.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.connectors.base import Page, Report, Visual, Workspace
from app.connectors.registry import PLATFORMS, ConnectionRecord
from app.engine.datasets import rows_from_query_result
from app.models.snapshot import FilterContext
from app.store.database import Store, get_store

router = APIRouter(tags=["connections"])


class CreateConnectionRequest(BaseModel):
    platform: str = "powerbi"      # files | powerbi | pbip | tableau
    name: Optional[str] = None
    workspace_id: Optional[str] = None
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    pbip_dir: Optional[str] = None
    server_url: Optional[str] = None
    site: Optional[str] = None
    pat_name: Optional[str] = None
    pat_secret: Optional[str] = None
    api_version: str = "3.21"
    file_ids: list[str] = Field(default_factory=list)
    source_platform: str = "generic"


class ConnectionOut(BaseModel):
    id: str
    name: str
    platform: str
    mode: str
    workspace_id: Optional[str] = None
    file_ids: list[str] = Field(default_factory=list)
    source_platform: str = "generic"
    server_url: Optional[str] = None
    site: Optional[str] = None
    pbip_dir: Optional[str] = None
    synced_workspaces: list[str] = Field(default_factory=list)
    last_synced_at: Optional[str] = None
    needs_credentials: bool = False
    created_at: str


class SecretsRequest(BaseModel):
    client_secret: Optional[str] = None
    pat_secret: Optional[str] = None


class AddFilesRequest(BaseModel):
    file_ids: list[str]


class VisualDataRequest(BaseModel):
    report_id: str
    page_id: str
    visual_id: str
    filters: list[FilterContext] = Field(default_factory=list)
    limit: int = Field(200, ge=1, le=100_000)


def _out(store: Store, r: ConnectionRecord) -> ConnectionOut:
    needs = r.platform in ("powerbi", "tableau") and not store.has_secrets(r.id) and not _env_secret(r.platform)
    return ConnectionOut(**r.model_dump(), mode=r.mode, needs_credentials=needs)


def _env_secret(platform: str) -> bool:
    import os

    return bool(os.environ.get("AZURE_CLIENT_SECRET" if platform == "powerbi" else "TABLEAU_PAT_SECRET"))


@router.get("/platforms")
def list_platforms() -> list[dict[str, Any]]:
    return PLATFORMS


@router.post("/connections", response_model=ConnectionOut)
def create_connection(req: CreateConnectionRequest, store: Store = Depends(get_store)) -> ConnectionOut:
    platform = req.platform
    if platform not in {p["id"] for p in PLATFORMS}:
        raise HTTPException(400, f"Unknown platform '{req.platform}'. Supported: {', '.join(p['id'] for p in PLATFORMS)}")
    for file_id in req.file_ids:
        if file_id not in store.files:
            raise HTTPException(404, f"file {file_id} not found — upload it first")
    if platform == "files" and not req.file_ids:
        raise HTTPException(400, "An exported-files connection needs at least one uploaded file (file_ids).")
    if platform == "pbix":
        raise HTTPException(400, "Power BI files are opened by uploading them: POST /model-files (Power BI models page).")
    if platform == "pbip" and not (req.pbip_dir and Path(req.pbip_dir).is_dir()):
        raise HTTPException(400, f"PBIP folder not found on the server: {req.pbip_dir or '(none given)'}")
    if req.pbip_dir and platform == "powerbi" and not Path(req.pbip_dir).is_dir():
        raise HTTPException(400, f"PBIP export folder not found on the server: {req.pbip_dir}")

    conn_id = store.next_id("CONN")
    default_names = {"files": "Exported files", "pbip": "Power BI project",
                     "powerbi": "Power BI", "tableau": "Tableau"}
    record = ConnectionRecord(
        id=conn_id,
        name=req.name or default_names.get(platform, platform),
        platform=platform,
        **req.model_dump(include={"workspace_id", "tenant_id", "client_id", "pbip_dir", "server_url", "site",
                                  "pat_name", "api_version", "file_ids", "source_platform"}),
    )
    store.add_connection(record, {"client_secret": req.client_secret, "pat_secret": req.pat_secret})
    return _out(store, record)


@router.get("/connections", response_model=list[ConnectionOut])
def list_connections(store: Store = Depends(get_store)) -> list[ConnectionOut]:
    return [_out(store, r) for r in store.connection_records.values()]


@router.get("/connections/{connection_id}", response_model=ConnectionOut)
def get_connection(connection_id: str, store: Store = Depends(get_store)) -> ConnectionOut:
    return _out(store, store.connection_records[connection_id])


@router.delete("/connections/{connection_id}")
def delete_connection(connection_id: str, store: Store = Depends(get_store)) -> dict:
    from app.services import recon_service

    record = store.connection_records[connection_id]
    store.remove_connection(connection_id)
    if record.platform == "pbix":  # the uploaded model file belongs to this connection only
        for file_id in record.file_ids:
            recon_service.delete_file(store, file_id, force=True)
    return {"deleted": connection_id}


@router.put("/connections/{connection_id}/secrets", response_model=ConnectionOut)
def set_secrets(connection_id: str, req: SecretsRequest, store: Store = Depends(get_store)) -> ConnectionOut:
    """Secrets aren't persisted; re-enter them after a restart."""
    record = store.connection_records[connection_id]
    store.add_connection(record, req.model_dump())
    return _out(store, record)


@router.post("/connections/{connection_id}/files", response_model=ConnectionOut)
def add_files(connection_id: str, req: AddFilesRequest, store: Store = Depends(get_store)) -> ConnectionOut:
    record = store.connection_records[connection_id]
    if record.platform != "files":
        raise HTTPException(400, "Only exported-files connections hold files")
    for file_id in req.file_ids:
        if file_id not in store.files:
            raise HTTPException(404, f"file {file_id} not found")

    def add(r: ConnectionRecord) -> None:
        r.file_ids += [f for f in req.file_ids if f not in r.file_ids]

    record = store.connection_records.update(connection_id, add)
    store.add_connection(record, store._secrets.get(connection_id, {}))  # rebuilds the connector, drops the stale graph
    return _out(store, record)


@router.get("/connections/{connection_id}/health")
def connection_health(connection_id: str, store: Store = Depends(get_store)) -> dict:
    try:
        connector = store.connector(connection_id)
        caps = connector.capabilities()
    except KeyError:
        raise HTTPException(404, f"connection {connection_id} not found")
    except Exception as e:  # noqa: BLE001 — health check must not raise
        return {"connection_id": connection_id, "reachable": False, "error": str(e)}
    try:
        workspaces = connector.get_workspaces()
        return {"connection_id": connection_id, "reachable": True, "workspaces": len(workspaces),
                "capabilities": caps.model_dump()}
    except Exception as e:  # noqa: BLE001
        return {"connection_id": connection_id, "reachable": False, "error": str(e), "capabilities": caps.model_dump()}


@router.post("/connections/{connection_id}/sync")
def sync_connection(connection_id: str, workspace_id: Optional[str] = None, store: Store = Depends(get_store)) -> dict:
    """Connect, extract metadata, normalize and build the canonical graph
    for one workspace (Epics 1-4). Lineage, impact and investigations read
    from the resulting graph."""
    record = store.connection_records[connection_id]
    if not workspace_id:
        workspace_id = record.workspace_id or next(iter(w.id for w in store.connector(connection_id).get_workspaces()), None)
    if not workspace_id:
        raise HTTPException(400, "No workspace found to sync")
    graph, warnings = store.sync(connection_id, workspace_id)
    counts: dict[str, int] = {}
    for node in graph.all_nodes():
        counts[node.type.value] = counts.get(node.type.value, 0) + 1
    return {
        "connection_id": connection_id,
        "workspace_id": workspace_id,
        "nodes": len(graph.all_nodes()),
        "edges": len(graph.all_edges()),
        "by_type": counts,
        "warnings": warnings,
    }


# ----------------------------------------------------------- browsing
@router.get("/connections/{connection_id}/workspaces", response_model=list[Workspace])
def list_workspaces(connection_id: str, store: Store = Depends(get_store)) -> list[Workspace]:
    return store.connector(connection_id).get_workspaces()


@router.get("/connections/{connection_id}/reports", response_model=list[Report])
def list_connection_reports(connection_id: str, workspace_id: Optional[str] = None, store: Store = Depends(get_store)) -> list[Report]:
    connector = store.connector(connection_id)
    record = store.connection_records[connection_id]
    workspace_id = workspace_id or record.workspace_id or next(iter(w.id for w in connector.get_workspaces()), "")
    return connector.get_reports(workspace_id)


@router.get("/connections/{connection_id}/reports/{report_id}/pages", response_model=list[Page])
def list_connection_pages(connection_id: str, report_id: str, store: Store = Depends(get_store)) -> list[Page]:
    return store.connector(connection_id).get_pages(report_id)


@router.get("/connections/{connection_id}/reports/{report_id}/pages/{page_id}/visuals", response_model=list[Visual])
def list_connection_visuals(connection_id: str, report_id: str, page_id: str, store: Store = Depends(get_store)) -> list[Visual]:
    return store.connector(connection_id).get_visuals(report_id, page_id)


@router.post("/connections/{connection_id}/visual-data")
def preview_visual_data(connection_id: str, req: VisualDataRequest, store: Store = Depends(get_store)) -> dict:
    result = store.connector(connection_id).get_visual_data(req.report_id, req.page_id, req.visual_id, req.filters)
    columns, rows = rows_from_query_result(result)
    return {"columns": columns, "rows": rows[: req.limit], "row_count": len(rows), "warnings": result.warnings}
