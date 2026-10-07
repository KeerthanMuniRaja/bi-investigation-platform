"""
Data reconciliation: suggest a column mapping, run a comparison, page
through results, export to Excel, raise an investigation, and save the
setup as a re-runnable template. Plus rate/volume variance analysis.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from app.engine import investigation as inv_engine
from app.engine.excel_export import export_data_reconciliation
from app.engine.variance import rate_volume
from app.models.data_reconciliation import (
    DataReconciliation,
    MappingSuggestion,
    RateVolumeResult,
    ReconciliationConfig,
    ReconciliationDefinition,
)
from app.models.dataset import DatasetSpec
from app.models.graph import utcnow
from app.models.investigation import Investigation
from app.services import recon_service
from app.store.database import Store, get_store

router = APIRouter(tags=["reconciliation"])


class PairRequest(BaseModel):
    left_dataset_id: str
    right_dataset_id: str


class RunRequest(PairRequest):
    config: Optional[ReconciliationConfig] = None  # omitted -> auto-mapped
    name: Optional[str] = None
    create_investigation: bool = False
    question: Optional[str] = None


class RunPage(BaseModel):
    reconciliation: DataReconciliation
    row_total: int
    offset: int
    limit: int


class RunListItem(BaseModel):
    reconciliation_id: str
    name: str
    left_name: str
    right_name: str
    status: str
    match_rate_pct: float
    total_keys: int
    differences: int
    top_difference: Optional[float] = None
    confidence: str
    definition_id: Optional[str] = None
    investigation_id: Optional[str] = None
    created_at: str


class InvestigateRequest(BaseModel):
    question: Optional[str] = None


class SaveTemplateRequest(BaseModel):
    name: str
    description: str = ""
    create_investigation: bool = False


class TemplateRequest(BaseModel):
    name: str
    description: str = ""
    left: DatasetSpec
    right: DatasetSpec
    config: ReconciliationConfig
    create_investigation: bool = False


class RunTemplateRequest(BaseModel):
    left_file_id: Optional[str] = None   # a newer upload of the same file
    right_file_id: Optional[str] = None


class RateVolumeRequest(BaseModel):
    dataset_a_id: str
    dataset_b_id: str
    key_columns: list[str] = Field(default_factory=list)
    volume_column: str
    rate_column: Optional[str] = None
    value_column: Optional[str] = None
    investigation_id: Optional[str] = None
    label: str = "Value"


def _page(rec: DataReconciliation, status: Optional[str], search: Optional[str], offset: int, limit: int) -> RunPage:
    rows = rec.rows
    if status:
        wanted = set(status.upper().split(","))
        rows = [r for r in rows if r.status in wanted]
    if search:
        needle = search.lower()
        rows = [r for r in rows if any(needle in str(v).lower() for v in r.keys.values())]
    return RunPage(reconciliation=rec.model_copy(update={"rows": rows[offset: offset + limit]}),
                   row_total=len(rows), offset=offset, limit=limit)


@router.post("/recon/suggest", response_model=MappingSuggestion)
def suggest_mapping(req: PairRequest, store: Store = Depends(get_store)) -> MappingSuggestion:
    return recon_service.suggest(store, req.left_dataset_id, req.right_dataset_id)


@router.post("/recon/run", response_model=RunPage)
def run_reconciliation(req: RunRequest, limit: int = Query(500, ge=1, le=10_000), store: Store = Depends(get_store)) -> RunPage:
    rec = recon_service.run(store, req.left_dataset_id, req.right_dataset_id, req.config, req.name,
                            create_investigation=req.create_investigation, question=req.question)
    return _page(rec, None, None, 0, limit)


@router.get("/recon", response_model=list[RunListItem])
def list_runs(definition_id: Optional[str] = None, store: Store = Depends(get_store)) -> list[RunListItem]:
    out = []
    for rec in store.data_reconciliations.values():
        if definition_id and rec.definition_id != definition_id:
            continue
        s = rec.summary
        out.append(RunListItem(
            reconciliation_id=rec.reconciliation_id, name=rec.name, left_name=rec.left_name, right_name=rec.right_name,
            status=rec.status, match_rate_pct=s.match_rate_pct, total_keys=s.total_keys,
            differences=s.mismatched + s.only_left + s.only_right,
            top_difference=rec.value_summaries[0].difference if rec.value_summaries else None,
            confidence=rec.confidence.value, definition_id=rec.definition_id, investigation_id=rec.investigation_id,
            created_at=rec.created_at,
        ))
    return out


@router.get("/recon/{reconciliation_id}", response_model=RunPage)
def get_run(reconciliation_id: str, status: Optional[str] = None, search: Optional[str] = None,
            offset: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=10_000),
            store: Store = Depends(get_store)) -> RunPage:
    return _page(store.data_reconciliations[reconciliation_id], status, search, offset, limit)


@router.delete("/recon/{reconciliation_id}")
def delete_run(reconciliation_id: str, store: Store = Depends(get_store)) -> dict:
    rec = store.data_reconciliations[reconciliation_id]
    store.data_reconciliations.delete(reconciliation_id)
    # don't leave dangling references behind (exports of the investigation would 404)
    for inv in store.investigations.values():
        if reconciliation_id in inv.data_reconciliation_ids:
            store.investigations.update(inv.investigation_id, lambda i: i.data_reconciliation_ids.remove(reconciliation_id))
    if rec.definition_id and rec.definition_id in store.definitions:
        store.definitions.update(rec.definition_id, lambda d: d.run_ids.remove(reconciliation_id)
                                 if reconciliation_id in d.run_ids else None)
    return {"deleted": reconciliation_id}


@router.get("/recon/{reconciliation_id}/export")
def export_run(reconciliation_id: str, store: Store = Depends(get_store)) -> FileResponse:
    rec = store.data_reconciliations[reconciliation_id]
    inv = store.investigations.get(rec.investigation_id) if rec.investigation_id else None
    # unique per request: concurrent downloads (or a copy open in Excel) don't collide
    path = store.export_dir / f"{reconciliation_id}_{uuid.uuid4().hex[:8]}.xlsx"
    export_data_reconciliation(rec, str(path), inv)
    return FileResponse(path, filename=f"{reconciliation_id}_reconciliation.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        background=BackgroundTask(path.unlink, missing_ok=True))


@router.post("/recon/{reconciliation_id}/investigate", response_model=Investigation)
def investigate_run(reconciliation_id: str, req: InvestigateRequest, store: Store = Depends(get_store)) -> Investigation:
    rec = store.data_reconciliations[reconciliation_id]
    if rec.investigation_id and rec.investigation_id in store.investigations:
        return store.investigations[rec.investigation_id]
    inv = recon_service.investigate(store, rec, req.question)
    store.data_reconciliations.update(reconciliation_id, lambda r: setattr(r, "investigation_id", inv.investigation_id))
    return inv


@router.post("/recon/{reconciliation_id}/save-template", response_model=ReconciliationDefinition)
def save_template(reconciliation_id: str, req: SaveTemplateRequest, store: Store = Depends(get_store)) -> ReconciliationDefinition:
    rec = store.data_reconciliations[reconciliation_id]
    definition = recon_service.definition_from_run(store, rec, req.name, req.description, req.create_investigation)
    store.data_reconciliations.update(reconciliation_id, lambda r: setattr(r, "definition_id", definition.definition_id))
    return definition


# ---------------------------------------------------------------- templates
@router.get("/recon-templates", response_model=list[ReconciliationDefinition])
def list_templates(store: Store = Depends(get_store)) -> list[ReconciliationDefinition]:
    return store.definitions.values()


@router.post("/recon-templates", response_model=ReconciliationDefinition)
def create_template(req: TemplateRequest, store: Store = Depends(get_store)) -> ReconciliationDefinition:
    definition = ReconciliationDefinition(definition_id=store.next_id("TPL"), **req.model_dump())
    store.definitions[definition.definition_id] = definition
    return definition


@router.get("/recon-templates/{definition_id}", response_model=ReconciliationDefinition)
def get_template(definition_id: str, store: Store = Depends(get_store)) -> ReconciliationDefinition:
    return store.definitions[definition_id]


@router.put("/recon-templates/{definition_id}", response_model=ReconciliationDefinition)
def update_template(definition_id: str, req: TemplateRequest, store: Store = Depends(get_store)) -> ReconciliationDefinition:
    existing = store.definitions[definition_id]
    # validate, don't model_copy: nested specs/config must become real models
    updated = ReconciliationDefinition.model_validate({**existing.model_dump(), **req.model_dump(), "updated_at": utcnow().isoformat()})
    store.definitions[definition_id] = updated
    return updated


@router.delete("/recon-templates/{definition_id}")
def delete_template(definition_id: str, store: Store = Depends(get_store)) -> dict:
    if not store.definitions.delete(definition_id):
        raise HTTPException(404, f"template {definition_id} not found")
    return {"deleted": definition_id}


@router.post("/recon-templates/{definition_id}/run", response_model=RunPage)
def run_template(definition_id: str, req: RunTemplateRequest, limit: int = Query(500, ge=1, le=10_000),
                 store: Store = Depends(get_store)) -> RunPage:
    rec = recon_service.run_definition(store, definition_id, req.left_file_id, req.right_file_id)
    return _page(rec, None, None, 0, limit)


# ---------------------------------------------------------------- analysis
@router.post("/analysis/rate-volume", response_model=RateVolumeResult)
def rate_volume_analysis(req: RateVolumeRequest, store: Store = Depends(get_store)) -> RateVolumeResult:
    a, b = store.datasets[req.dataset_a_id], store.datasets[req.dataset_b_id]
    result = rate_volume(a, b, req.key_columns, req.volume_column, req.rate_column, req.value_column)
    if req.investigation_id:
        inv = store.investigations[req.investigation_id]
        inv_engine.attach_rate_volume(inv, result, req.label)
        inv_engine.finalize(inv)
        store.investigations[inv.investigation_id] = inv
    return result

