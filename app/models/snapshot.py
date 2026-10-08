"""
VisualSnapshot data contract — Section 89 of the spec, extended with the
filter/value detail Section 13 (Module 2 — Visual / Value Capture) calls for.

A snapshot is a frozen, timestamped capture of one visual's value and the
exact context (filters) that produced it. Reconciliation always compares
two snapshots, never a live value against a memory of what it used to be.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.graph import Confidence, utcnow


class FilterContext(BaseModel):
    field: str          # e.g. "Calendar[Year]" or "Calendar[Month]"
    operator: str = "in"  # in, equals, between, >=, etc.
    values: list[Any] = Field(default_factory=list)


class CapturedValue(BaseModel):
    metric_name: str
    value: float
    formatted_value: Optional[str] = None
    unit: Optional[str] = None


class VisualSnapshot(BaseModel):
    snapshot_id: str
    connection_id: Optional[str] = None   # the dashboard it was captured from
    platform: str = "powerbi"
    report_id: str
    page_id: str
    visual_id: str
    visual_title: str
    measure_id: Optional[str] = None
    captured_at: str = Field(default_factory=lambda: utcnow().isoformat())
    filters: list[FilterContext] = Field(default_factory=list)
    values: list[CapturedValue] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    confidence: Confidence = Confidence.VERIFIED

    def context_fingerprint(self) -> str:
        """
        A stable string representation of the filter context, used to
        detect whether two snapshots are actually comparable (same
        context, different period) or comparing apples to oranges.
        """
        parts = sorted(
            f"{f.field}:{f.operator}:{','.join(str(v) for v in f.values)}"
            for f in self.filters
        )
        return "|".join(parts)

    def primary_value(self) -> Optional[float]:
        return self.values[0].value if self.values else None
