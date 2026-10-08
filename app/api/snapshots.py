from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.snapshot import FilterContext, VisualSnapshot
from app.store.database import Store, get_store

router = APIRouter(tags=["snapshots"])


class CreateSnapshotRequest(BaseModel):
    connection_id: str
    report_id: str
    page_id: str
    visual_id: str
    filters: list[FilterContext] = []


@router.post("/snapshots", response_model=VisualSnapshot)
def create_snapshot(req: CreateSnapshotRequest, store: Store = Depends(get_store)) -> VisualSnapshot:
    connector = store.connector(req.connection_id)
    snapshot_id = store.next_id("SNAP")
    snapshot = connector.get_visual_snapshot(
        report_id=req.report_id,
        page_id=req.page_id,
        visual_id=req.visual_id,
        filters=req.filters,
        snapshot_id=snapshot_id,
    )
    snapshot.connection_id = req.connection_id
    store.snapshots[snapshot_id] = snapshot
    return snapshot


@router.get("/snapshots", response_model=list[VisualSnapshot])
def list_snapshots(store: Store = Depends(get_store)) -> list[VisualSnapshot]:
    return store.snapshots.values()


@router.get("/snapshots/{snapshot_id}", response_model=VisualSnapshot)
def get_snapshot(snapshot_id: str, store: Store = Depends(get_store)) -> VisualSnapshot:
    snap = store.snapshots.get(snapshot_id)
    if snap is None:
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    return snap
