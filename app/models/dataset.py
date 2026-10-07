"""
Tabular dataset contracts.

A Dataset is the common currency of reconciliation: a Power BI visual's
data, a Tableau view's CSV, a sheet in a finance Excel workbook and a
manually uploaded export all become the same shape, so one comparison
engine serves every BI tool and every source.

A DatasetSpec is the *recipe* for a dataset (which file/sheet, which
visual + filters, which query, which transforms). Saving the recipe rather
than only the rows is what makes a reconciliation re-runnable next month
with one click.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.models.graph import Confidence, utcnow
from app.models.snapshot import FilterContext


class Transform(BaseModel):
    """A small, safe, declarative reshaping step (no arbitrary code)."""

    op: Literal["rename", "drop", "select", "filter", "unpivot"]
    # rename: {"old": "new"}
    mapping: dict[str, str] = Field(default_factory=dict)
    # drop/select: column names; unpivot: id columns kept as-is
    columns: list[str] = Field(default_factory=list)
    # filter: keep rows where `column` (normalized) is in `values`
    column: Optional[str] = None
    values: list[Any] = Field(default_factory=list)
    exclude: bool = False
    # unpivot: wide -> long (e.g. months as columns -> one Month column)
    var_name: str = "Attribute"
    value_name: str = "Value"


class DatasetSpec(BaseModel):
    kind: Literal["file", "bi_visual", "bi_query", "model_table", "dataset", "inline"]
    name: Optional[str] = None
    # file
    file_id: Optional[str] = None
    sheet: Optional[str] = None
    header_row: Optional[int] = Field(None, ge=0)  # 0-based row index in the sheet; None = auto-detect
    # BI
    connection_id: Optional[str] = None
    report_id: Optional[str] = None
    page_id: Optional[str] = None
    visual_id: Optional[str] = None
    filters: list[FilterContext] = Field(default_factory=list)
    model_id: Optional[str] = None
    query: Optional[str] = None
    table: Optional[str] = None  # model_table: a table stored in a .pbix
    # reuse an already-captured dataset as-is
    dataset_id: Optional[str] = None
    transforms: list[Transform] = Field(default_factory=list)


class DatasetColumn(BaseModel):
    name: str
    dtype: str = "text"  # number | text | date | bool


class Dataset(BaseModel):
    dataset_id: str
    name: str
    source: DatasetSpec
    platform: Optional[str] = None  # powerbi | tableau | excel | csv | ...
    columns: list[DatasetColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    header_row: Optional[int] = None
    warnings: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.VERIFIED
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def summary(self) -> "DatasetSummary":
        return DatasetSummary(**self.model_dump(exclude={"rows"}))


class DatasetSummary(BaseModel):
    dataset_id: str
    name: str
    source: DatasetSpec
    platform: Optional[str] = None
    columns: list[DatasetColumn] = Field(default_factory=list)
    row_count: int = 0
    header_row: Optional[int] = None
    warnings: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.VERIFIED
    created_at: str
