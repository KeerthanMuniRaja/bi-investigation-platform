"""
Reconciliation (Section 14, Module 3) and Impact Analysis (Section 21,
Module 9) data contracts.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.models.graph import Confidence, utcnow


class Variance(BaseModel):
    value_a: float
    value_b: float
    difference: float
    difference_pct: Optional[float] = None

    @classmethod
    def compute(cls, value_a: float, value_b: float) -> "Variance":
        diff = value_b - value_a
        pct = (diff / abs(value_a) * 100) if value_a not in (0, None) else None  # same sign rule as data reconciliation
        return cls(value_a=value_a, value_b=value_b, difference=diff, difference_pct=pct)


class Reconciliation(BaseModel):
    reconciliation_id: str
    snapshot_id_a: str
    snapshot_id_b: str
    variance: Variance
    context_matches: bool          # did the two snapshots share the same filter context aside from the thing being compared?
    context_diff: list[str] = Field(default_factory=list)  # human-readable list of differing filters
    confidence: Confidence = Confidence.VERIFIED
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AffectedNode(BaseModel):
    id: str
    name: str
    type: str
    depth: int


class ImpactResult(BaseModel):
    target_node_id: str
    affected_measure_ids: list[str] = Field(default_factory=list)
    affected_visual_ids: list[str] = Field(default_factory=list)
    affected_report_ids: list[str] = Field(default_factory=list)
    affected: list[AffectedNode] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW
    confidence: Confidence = Confidence.VERIFIED
    explanation: str = ""
