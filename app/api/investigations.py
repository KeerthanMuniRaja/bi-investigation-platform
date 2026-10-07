from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.engine import investigation as inv_engine
from app.engine.impact import analyze_impact
from app.engine.lineage import trace_upstream
from app.engine.reconciliation import reconcile
from app.models.investigation import EvidenceItem, Investigation, InvestigationStatus, Note
from app.models.lineage import LineagePath
from app.models.reconciliation import ImpactResult
from app.store.database import Store, get_store

router = APIRouter(tags=["investigations"])


class CreateInvestigationRequest(BaseModel):
    question: str
    connection_id: Optional[str] = None
    snapshot_id_a: Optional[str] = None
    snapshot_id_b: Optional[str] = None
    lineage_root_id: Optional[str] = None
    impact_target_id: Optional[str] = None
    data_reconciliation_id: Optional[str] = None
    owner: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class UpdateInvestigationRequest(BaseModel):
    status: Optional[InvestigationStatus] = None
    question: Optional[str] = None
    owner: Optional[str] = None
    tags: Optional[list[str]] = None


class NoteRequest(BaseModel):
    text: str
    author: str = "analyst"


class GraphTargetRequest(BaseModel):
    node_id: str
    connection_id: Optional[str] = None


class InvestigationListItem(BaseModel):
    investigation_id: str
    question: str
    status: InvestigationStatus
    confidence: str
    top_hypothesis: Optional[str] = None
    evidence_count: int
    owner: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


def _update(store: Store, investigation_id: str, change) -> Investigation:
    """Atomic change + touch, so two notes added at once both survive."""
    def apply(inv: Investigation) -> None:
        change(inv)
        inv.touch()

    return store.investigations.update(investigation_id, apply)


def _graph_with(store: Store, connection_id: str, node_id: str):
    """The connection's graph, refusing unknown/unsynced connections and unknown objects."""
    from app.api.semantic import _graph

    graph = _graph(store, connection_id)
    if graph.get_node(node_id) is None:
        raise HTTPException(404, f"'{node_id}' is not an object of connection {connection_id}")
    return graph


@router.post("/investigations", response_model=Investigation)
def create_investigation(req: CreateInvestigationRequest, store: Store = Depends(get_store)) -> Investigation:
    investigation_id = store.next_id("INV")
    investigation = inv_engine.create_investigation(investigation_id, req.question)
    investigation.connection_id = req.connection_id
    investigation.owner = req.owner
    investigation.tags = req.tags

    if req.snapshot_id_a and req.snapshot_id_b:
        snap_a = store.snapshots.get(req.snapshot_id_a)
        snap_b = store.snapshots.get(req.snapshot_id_b)
        if snap_a is None or snap_b is None:
            raise HTTPException(404, "one or both snapshots not found")
        reconciliation_id = store.next_id("REC")
        reconciliation = reconcile(snap_a, snap_b, reconciliation_id)
        store.reconciliations[reconciliation_id] = reconciliation
        inv_engine.attach_reconciliation(investigation, reconciliation, snap_a, snap_b)

    if req.data_reconciliation_id:
        inv_engine.attach_data_reconciliation(investigation, store.data_reconciliations[req.data_reconciliation_id])

    if (req.lineage_root_id or req.impact_target_id) and not req.connection_id:
        raise HTTPException(400, "connection_id is required for lineage / impact analysis")
    if req.lineage_root_id:
        inv_engine.attach_lineage(investigation, trace_upstream(_graph_with(store, req.connection_id, req.lineage_root_id), req.lineage_root_id))
    if req.impact_target_id:
        inv_engine.attach_impact(investigation, analyze_impact(_graph_with(store, req.connection_id, req.impact_target_id), req.impact_target_id))

    inv_engine.finalize(investigation)
    store.investigations[investigation_id] = investigation
    if req.data_reconciliation_id:
        store.data_reconciliations.update(req.data_reconciliation_id, lambda r: setattr(r, "investigation_id", investigation_id))
    return investigation


@router.get("/investigations", response_model=list[InvestigationListItem])
def list_investigations(status: Optional[InvestigationStatus] = None, store: Store = Depends(get_store)) -> list[InvestigationListItem]:
    out = []
    for inv in store.investigations.values():
        if status and inv.status != status:
            continue
        top = next((h.title or h.description for h in inv.hypotheses), None)
        out.append(InvestigationListItem(
            investigation_id=inv.investigation_id, question=inv.question, status=inv.status, confidence=inv.confidence.value,
            top_hypothesis=top, evidence_count=len(inv.evidence), owner=inv.owner, tags=inv.tags,
            created_at=inv.created_at, updated_at=inv.updated_at,
        ))
    return out


@router.get("/investigations/{investigation_id}", response_model=Investigation)
def get_investigation(investigation_id: str, store: Store = Depends(get_store)) -> Investigation:
    return store.investigations[investigation_id]


@router.patch("/investigations/{investigation_id}", response_model=Investigation)
def update_investigation(investigation_id: str, req: UpdateInvestigationRequest, store: Store = Depends(get_store)) -> Investigation:
    changes = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}
    return _update(store, investigation_id, lambda inv: [setattr(inv, k, v) for k, v in changes.items()])


@router.delete("/investigations/{investigation_id}")
def delete_investigation(investigation_id: str, store: Store = Depends(get_store)) -> dict:
    store.investigations[investigation_id]  # 404 if unknown
    store.investigations.delete(investigation_id)
    # the run page offers "Investigate" again instead of linking to a deleted case
    for rec in store.data_reconciliations.values():
        if rec.investigation_id == investigation_id:
            store.data_reconciliations.update(rec.reconciliation_id, lambda r: setattr(r, "investigation_id", None))
    return {"deleted": investigation_id}


@router.post("/investigations/{investigation_id}/notes", response_model=Investigation)
def add_note(investigation_id: str, req: NoteRequest, store: Store = Depends(get_store)) -> Investigation:
    if not req.text.strip():
        raise HTTPException(400, "A note needs some text.")
    return _update(store, investigation_id, lambda inv: inv.notes.append(Note(author=req.author, text=req.text)))


@router.post("/investigations/{investigation_id}/lineage", response_model=Investigation)
def attach_lineage(investigation_id: str, req: GraphTargetRequest, store: Store = Depends(get_store)) -> Investigation:
    inv = store.investigations[investigation_id]
    connection_id = req.connection_id or inv.connection_id
    if not connection_id:
        raise HTTPException(400, "connection_id is required")
    lineage = trace_upstream(_graph_with(store, connection_id, req.node_id), req.node_id)
    return _update(store, investigation_id, lambda i: (inv_engine.attach_lineage(i, lineage), inv_engine.finalize(i)))


@router.post("/investigations/{investigation_id}/impact", response_model=Investigation)
def attach_impact(investigation_id: str, req: GraphTargetRequest, store: Store = Depends(get_store)) -> Investigation:
    inv = store.investigations[investigation_id]
    connection_id = req.connection_id or inv.connection_id
    if not connection_id:
        raise HTTPException(400, "connection_id is required")
    impact = analyze_impact(_graph_with(store, connection_id, req.node_id), req.node_id)
    return _update(store, investigation_id, lambda i: (inv_engine.attach_impact(i, impact), inv_engine.finalize(i)))


@router.get("/investigations/{investigation_id}/lineage", response_model=LineagePath)
def get_investigation_lineage(investigation_id: str, store: Store = Depends(get_store)) -> LineagePath:
    inv = store.investigations[investigation_id]
    if inv.lineage is None:
        raise HTTPException(404, f"investigation {investigation_id} has no lineage attached")
    return inv.lineage


@router.get("/investigations/{investigation_id}/impact", response_model=ImpactResult)
def get_investigation_impact(investigation_id: str, store: Store = Depends(get_store)) -> ImpactResult:
    inv = store.investigations[investigation_id]
    if inv.impact is None:
        raise HTTPException(404, f"investigation {investigation_id} has no impact analysis attached")
    return inv.impact


@router.get("/investigations/{investigation_id}/evidence", response_model=list[EvidenceItem])
def get_investigation_evidence(investigation_id: str, store: Store = Depends(get_store)) -> list[EvidenceItem]:
    return store.investigations[investigation_id].evidence
