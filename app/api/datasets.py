"""
Datasets: the tabular inputs to a reconciliation — an Excel tab, a BI
visual's data, a custom query result, or rows posted directly.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.dataset import Dataset, DatasetSpec, DatasetSummary
from app.services import recon_service
from app.store.database import Store, get_store

router = APIRouter(tags=["datasets"])


class CreateDatasetRequest(DatasetSpec):
    pass


class InlineDatasetRequest(BaseModel):
    name: str
    columns: Optional[list[str]] = None
    rows: list[dict[str, Any]]


@router.post("/datasets", response_model=DatasetSummary)
def create_dataset(req: CreateDatasetRequest, store: Store = Depends(get_store)) -> DatasetSummary:
    """kind=file (file_id, sheet, header_row) · kind=bi_visual (connection_id,
    report_id, page_id, visual_id, filters) · kind=bi_query (connection_id,
    model_id, query) · kind=dataset (dataset_id + transforms)."""
    spec = DatasetSpec(**req.model_dump())
    return recon_service.materialize(store, spec).summary()


@router.post("/datasets/inline", response_model=DatasetSummary)
def create_inline_dataset(req: InlineDatasetRequest, store: Store = Depends(get_store)) -> DatasetSummary:
    columns = req.columns or list(dict.fromkeys(k for row in req.rows for k in row))
    return recon_service.inline_dataset(store, req.name, columns, req.rows).summary()


@router.get("/datasets", response_model=list[DatasetSummary])
def list_datasets(store: Store = Depends(get_store)) -> list[DatasetSummary]:
    return [d.summary() for d in store.datasets.values()]


@router.get("/datasets/{dataset_id}", response_model=Dataset)
def get_dataset(dataset_id: str, limit: int = 200, store: Store = Depends(get_store)) -> Dataset:
    ds = store.datasets[dataset_id]
    return ds.model_copy(update={"rows": ds.rows[:limit]})


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, store: Store = Depends(get_store)) -> dict:
    if not store.datasets.delete(dataset_id):
        raise HTTPException(404, f"dataset {dataset_id} not found")
    return {"deleted": dataset_id}
