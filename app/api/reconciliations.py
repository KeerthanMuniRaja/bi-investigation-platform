from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.engine.reconciliation import reconcile
from app.models.reconciliation import Reconciliation
from app.store.database import Store, get_store

router = APIRouter(tags=["reconciliations"])


class CreateReconciliationRequest(BaseModel):
    snapshot_id_a: str
    snapshot_id_b: str


@router.post("/reconciliations", response_model=Reconciliation)
def create_reconciliation(req: CreateReconciliationRequest, store: Store = Depends(get_store)) -> Reconciliation:
    snap_a = store.snapshots.get(req.snapshot_id_a)
    snap_b = store.snapshots.get(req.snapshot_id_b)
    if snap_a is None or snap_b is None:
        raise HTTPException(404, "one or both snapshots not found")
    reconciliation_id = store.next_id("REC")
    reconciliation = reconcile(snap_a, snap_b, reconciliation_id)
    store.reconciliations[reconciliation_id] = reconciliation
    return reconciliation


@router.get("/reconciliations/{reconciliation_id}", response_model=Reconciliation)
def get_reconciliation(reconciliation_id: str, store: Store = Depends(get_store)) -> Reconciliation:
    rec = store.reconciliations.get(reconciliation_id)
    if rec is None:
        raise HTTPException(404, f"reconciliation {reconciliation_id} not found")
    return rec
