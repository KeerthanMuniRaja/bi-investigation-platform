"""Uploading Excel/CSV files and previewing their tabs before use."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.services import recon_service
from app.sources.files import detect_header_row, parse_table, read_grid
from app.store.database import FileRecord, Store, get_store

router = APIRouter(tags=["files"])


@router.post("/files", response_model=list[FileRecord])
def upload_files(files: list[UploadFile] = File(...), store: Store = Depends(get_store)) -> list[FileRecord]:
    # plain def: FastAPI runs it in a worker thread, so parsing a big workbook
    # doesn't freeze every other request
    saved: list[FileRecord] = []
    try:
        for upload in files:
            saved.append(recon_service.save_upload(store, upload.filename or "upload", upload.file, upload.content_type))
    except Exception:
        for record in saved:  # all or nothing: don't leave half an upload behind
            recon_service.delete_file(store, record.file_id, force=True)
        raise
    return saved


class DirectUpload(BaseModel):
    filename: str = Field(..., max_length=255)
    kind: str = Field("table", pattern="^(table|model)$")
    size: Optional[int] = None


class FinishUpload(BaseModel):
    key: str = Field(..., max_length=512)
    filename: str = Field(..., max_length=255)


@router.post("/uploads")
def start_upload(req: DirectUpload, store: Store = Depends(get_store)) -> dict:
    """Big files: get a one-time URL to upload straight to cloud storage,
    then call POST /files/import (or /model-files/import) with the key."""
    return recon_service.start_direct_upload(store, req.filename, req.kind, req.size)


@router.post("/files/import", response_model=FileRecord)
def import_uploaded_file(req: FinishUpload, store: Store = Depends(get_store)) -> FileRecord:
    return recon_service.finish_direct_upload(store, req.key, req.filename, "table")


@router.get("/files", response_model=list[FileRecord])
def list_files(store: Store = Depends(get_store)) -> list[FileRecord]:
    return store.files.values()


@router.get("/files/{file_id}", response_model=FileRecord)
def get_file(file_id: str, store: Store = Depends(get_store)) -> FileRecord:
    return store.files[file_id]


@router.delete("/files/{file_id}")
def delete_file(file_id: str, store: Store = Depends(get_store)) -> dict:
    try:
        deleted = recon_service.delete_file(store, file_id)
    except ValueError as e:  # still used by a connection or template
        raise HTTPException(409, str(e)) from e
    if not deleted:
        raise HTTPException(404, f"file {file_id} not found")
    return {"deleted": file_id}


@router.get("/files/{file_id}/preview")
def preview_file(file_id: str, sheet: Optional[str] = None, header_row: Optional[int] = Query(None, ge=0),
                 limit: int = Query(50, ge=1, le=1000), store: Store = Depends(get_store)) -> dict:
    """The raw top of the tab (so the user can see titles/headers) plus the
    parsed table using the detected — or chosen — header row."""
    record = store.files[file_id]
    _, path = store.resolve_file(file_id)
    sheet = sheet or (record.sheets[0]["name"] if record.sheets else None)
    grid = read_grid(path, sheet)
    detected = detect_header_row(grid)
    table = parse_table(grid, header_row=header_row if header_row is not None else detected, sheet=sheet or "Sheet1")
    raw = [[_cell(c) for c in row] for row in grid[:15]]
    return {
        "file_id": file_id,
        "sheet": sheet,
        "detected_header_row": detected,
        "header_row": table.header_row,
        "columns": table.columns,
        "rows": table.rows[:limit],
        "row_count": len(table.rows),
        "warnings": table.warnings,
        "raw": raw,
    }


def _cell(value):
    from app.engine.values import to_jsonable

    return to_jsonable(value)
