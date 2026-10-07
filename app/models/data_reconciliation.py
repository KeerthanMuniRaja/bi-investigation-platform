"""
Tabular reconciliation contracts: BI visual vs Excel, Excel vs Excel,
Tableau view vs Power BI visual, period vs period — any two Datasets.

Section 14 (Module 3) asks the product to *investigate* a variance, not
just report one, so a result carries a variance bridge (how much of the
difference comes from missing rows vs changed values), the dimension
members driving it, and ranked hypotheses — not only a diff table.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.models.dataset import DatasetSpec
from app.models.graph import Confidence, utcnow
from app.models.investigation import Hypothesis


class ColumnPair(BaseModel):
    left: str
    right: str
    label: Optional[str] = None

    def display(self) -> str:
        if self.label:
            return self.label
        return self.left if self.left == self.right else f"{self.left} / {self.right}"


class ValueComparison(ColumnPair):
    tolerance_abs: float = 0.0
    tolerance_pct: float = 0.0  # percent of the left value, e.g. 0.5 = 0.5%


class ReconciliationConfig(BaseModel):
    key_columns: list[ColumnPair] = Field(default_factory=list)
    value_columns: list[ValueComparison] = Field(default_factory=list)
    # auto/sum: sum duplicate keys (granularity mismatch); first: keep first row
    aggregate: Literal["auto", "sum", "first"] = "auto"
    case_insensitive_keys: bool = True
    treat_blank_as_zero: bool = True
    # multiply every value on one side (units: thousands vs units, pence vs pounds)
    scale_left: float = 1.0
    scale_right: float = 1.0
    # keep only rows whose (normalized) column value is in the list
    left_filters: dict[str, list[Any]] = Field(default_factory=dict)
    right_filters: dict[str, list[Any]] = Field(default_factory=dict)


class ColumnMatch(BaseModel):
    left: str
    right: str
    score: float
    role: Literal["key", "value"]
    reason: str = ""


class MappingSuggestion(BaseModel):
    key_columns: list[ColumnMatch] = Field(default_factory=list)
    value_columns: list[ColumnMatch] = Field(default_factory=list)
    unmapped_left: list[str] = Field(default_factory=list)
    unmapped_right: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    config: ReconciliationConfig = Field(default_factory=ReconciliationConfig)


class CellDiff(BaseModel):
    left: Optional[float] = None
    right: Optional[float] = None
    difference: float = 0.0
    difference_pct: Optional[float] = None
    within_tolerance: bool = True


RowStatus = Literal["MATCH", "MISMATCH", "ONLY_LEFT", "ONLY_RIGHT"]


class ResultRow(BaseModel):
    keys: dict[str, Any] = Field(default_factory=dict)
    status: RowStatus
    cells: list[CellDiff] = Field(default_factory=list)
    left_rows: int = 0   # source rows aggregated into this key on the left
    right_rows: int = 0


class VarianceBridge(BaseModel):
    """total_right - total_left, explained piece by piece."""

    missing_from_right: float = 0.0   # rows only on the left (subtracted)
    missing_from_left: float = 0.0    # rows only on the right (added)
    value_differences: float = 0.0    # matched keys whose values differ
    total: float = 0.0


class ValueSummary(BaseModel):
    label: str
    left_column: str
    right_column: str
    total_left: float = 0.0
    total_right: float = 0.0
    difference: float = 0.0
    difference_pct: Optional[float] = None
    mismatched_rows: int = 0
    bridge: VarianceBridge = Field(default_factory=VarianceBridge)


class DriverMember(BaseModel):
    member: str
    difference: float
    share_pct: float  # share of total absolute variance
    rows: int


class DimensionDrivers(BaseModel):
    dimension: str
    value_label: str
    members: list[DriverMember] = Field(default_factory=list)


class ReconciliationSummary(BaseModel):
    total_keys: int = 0
    matched: int = 0
    mismatched: int = 0
    only_left: int = 0
    only_right: int = 0
    match_rate_pct: float = 100.0
    left_rows: int = 0
    right_rows: int = 0


class DataReconciliation(BaseModel):
    reconciliation_id: str
    name: str
    left_dataset_id: str
    right_dataset_id: str
    left_name: str = "Left"
    right_name: str = "Right"
    config: ReconciliationConfig
    status: Literal["MATCHED", "DIFFERENCES_FOUND"] = "MATCHED"
    summary: ReconciliationSummary = Field(default_factory=ReconciliationSummary)
    value_summaries: list[ValueSummary] = Field(default_factory=list)
    drivers: list[DimensionDrivers] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    rows: list[ResultRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.VERIFIED
    definition_id: Optional[str] = None
    investigation_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())

    def key_labels(self) -> list[str]:
        return [p.display() for p in self.config.key_columns]

    def value_labels(self) -> list[str]:
        return [p.display() for p in self.config.value_columns]


class ReconciliationDefinition(BaseModel):
    """A saved, re-runnable reconciliation (e.g. the month-end check)."""

    definition_id: str
    name: str
    description: str = ""
    left: DatasetSpec
    right: DatasetSpec
    config: ReconciliationConfig
    create_investigation: bool = False
    run_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: utcnow().isoformat())


class RateVolumeLine(BaseModel):
    key: str
    value_a: float = 0.0
    value_b: float = 0.0
    rate_effect: float = 0.0
    volume_effect: float = 0.0
    interaction: float = 0.0
    new_or_lost: float = 0.0
    total_change: float = 0.0


class RateVolumeResult(BaseModel):
    total_a: float
    total_b: float
    total_change: float
    rate_effect: float
    volume_effect: float
    interaction: float
    new_or_lost: float
    lines: list[RateVolumeLine] = Field(default_factory=list)
    explanation: str = ""
