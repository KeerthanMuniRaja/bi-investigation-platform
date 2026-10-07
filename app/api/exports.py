from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.engine.excel_export import export_data_reconciliation, export_reconciliation
from app.store.database import Store, get_store

router = APIRouter(tags=["exports"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExportExcelRequest(BaseModel):
    investigation_id: str


class ExportExcelResponse(BaseModel):
    investigation_id: str
    path: str
    download_url: str


def _write_investigation_workbook(store: Store, investigation_id: str) -> Path:
    inv = store.investigations[investigation_id]
    output_path = store.export_dir / f"{investigation_id}_reconciliation.xlsx"
    if inv.reconciliation_id and len(inv.snapshot_ids) >= 2:
        reconciliation = store.reconciliations.get(inv.reconciliation_id)
        snap_a = store.snapshots.get(inv.snapshot_ids[0])
        snap_b = store.snapshots.get(inv.snapshot_ids[1])
        if reconciliation is None or snap_a is None or snap_b is None:
            raise HTTPException(409, "The reconciliation/snapshots this investigation refers to have been deleted.")
        export_reconciliation(inv, reconciliation, snap_a, snap_b, str(output_path))
    elif inv.data_reconciliation_ids:
        # the newest linked run that still exists
        rec = next((r for r in (store.data_reconciliations.get(i) for i in reversed(inv.data_reconciliation_ids)) if r), None)
        if rec is None:
            raise HTTPException(409, "Every reconciliation linked to this investigation has been deleted — nothing to export.")
        export_data_reconciliation(rec, str(output_path), inv)
    else:
        raise HTTPException(400, "investigation has no reconciliation attached — nothing to export as a reconciliation workbook")
    return output_path


@router.post("/exports/excel", response_model=ExportExcelResponse)
def export_excel(req: ExportExcelRequest, store: Store = Depends(get_store)) -> ExportExcelResponse:
    path = _write_investigation_workbook(store, req.investigation_id)
    return ExportExcelResponse(investigation_id=req.investigation_id, path=str(path),
                               download_url=f"/exports/investigations/{req.investigation_id}.xlsx")


@router.get("/exports/investigations/{investigation_id}.xlsx")
def download_investigation_workbook(investigation_id: str, store: Store = Depends(get_store)) -> FileResponse:
    path = _write_investigation_workbook(store, investigation_id)
    return FileResponse(path, filename=path.name, media_type=XLSX)
