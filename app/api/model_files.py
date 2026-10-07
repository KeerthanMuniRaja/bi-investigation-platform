"""
Understanding a Power BI file: upload it, then read its documentation,
health check, measure explanations, table data and lineage.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.connectors.base import CapabilityError
from app.engine.dax_explain import category, explain
from app.engine.datasets import rows_from_query_result
from app.services import recon_service
from app.store.database import Store, get_store

router = APIRouter(tags=["power bi files"])


@router.post("/model-files")
def upload_model_file(file: UploadFile = File(...), store: Store = Depends(get_store)) -> dict[str, Any]:
    """Upload a .pbix / .pbit / .bim / zipped PBIP and get back a connection
    whose model, report and lineage are ready to explore. (Plain def: the
    file is streamed to disk and parsed in a worker thread.)"""
    record = recon_service.save_model_upload(store, file.filename or "model.pbix", file.file)
    connection_id = None
    try:
        connection_id, warnings = recon_service.open_model_file(store, record)
        overview = store.overview(connection_id)
    except Exception:
        if connection_id:
            store.remove_connection(connection_id)
        recon_service.delete_file(store, record.file_id, force=True)
        raise
    return {"connection_id": connection_id, "file_id": record.file_id, "name": overview["name"],
            "counts": overview["counts"], "warnings": warnings + overview["warnings"]}


@router.delete("/model-files/{connection_id}")
def delete_model(connection_id: str, store: Store = Depends(get_store)) -> dict[str, str]:
    """Remove an opened Power BI model: its connection, uploaded file and any extracted PBIP folder."""
    record = store.connection_records[connection_id]
    if record.platform not in ("pbix", "pbip"):
        raise HTTPException(400, f"{connection_id} is a live {record.platform} connection — delete it on the Sources page.")
    store.remove_connection(connection_id)
    if record.platform == "pbix":
        for file_id in record.file_ids:
            recon_service.delete_file(store, file_id, force=True)
    return {"deleted": connection_id}


@router.get("/connections/{connection_id}/overview")
def model_overview(connection_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    """Documentation + health check of the connection's semantic model and report."""
    return store.overview(connection_id)


@router.get("/connections/{connection_id}/table-data")
def table_data(connection_id: str, table: str, limit: int = Query(100, ge=1, le=10_000),
               store: Store = Depends(get_store)) -> dict[str, Any]:
    connector = store.connector(connection_id)
    if not hasattr(connector, "get_table_data"):
        raise CapabilityError("This connection can't read table data directly.")
    columns, rows = rows_from_query_result(connector.get_table_data(table, limit=limit))
    return {"table": table, "columns": columns, "rows": rows}


@router.post("/dax/explain")
def explain_dax(body: dict[str, str]) -> dict[str, str]:
    expression = body.get("expression", "")
    if not expression.strip():
        raise HTTPException(400, "expression is required")
    return {"explanation": explain(expression), "category": category(expression)}


@router.get("/model-files")
def list_model_connections(store: Store = Depends(get_store)) -> list[dict[str, Optional[str]]]:
    out = []
    files = {f.file_id: f for f in store.files.values()}
    for c in store.connection_records.values():
        if c.platform in ("pbix", "pbip"):  # live connections have no model file to document offline
            f = files.get(c.file_ids[0]) if c.file_ids else None
            out.append({"connection_id": c.id, "name": c.name, "platform": c.platform, "file_name": f.name if f else None,
                        "last_synced_at": c.last_synced_at})
    return out
