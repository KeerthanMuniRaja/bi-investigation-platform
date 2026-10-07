"""
Investigation is the fundamental product object (Section 10 of the spec).
Everything else — snapshots, lineage, reconciliation, impact — feeds into
one of these, and one of these is what gets saved so the same problem is
never investigated from scratch twice (Section 52, 53).
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.graph import CONFIDENCE_RANK, Confidence, utcnow
from app.models.lineage import LineagePath
from app.models.reconciliation import ImpactResult


class InvestigationStatus(str, Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    ROOT_CAUSE_FOUND = "ROOT_CAUSE_FOUND"
    RESOLVED = "RESOLVED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceItem(BaseModel):
    evidence_id: str
    kind: str  # "snapshot_value" | "filter_context" | "dax_definition" | "lineage_path" | "relationship" | "source_query"
    description: str
    source_ref: Optional[str] = None   # id of the node/snapshot/query this evidence points to
    confidence: Confidence = Confidence.VERIFIED
    captured_at: str = Field(default_factory=lambda: utcnow().isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    hypothesis_id: str
    description: str
    supported: Optional[bool] = None  # None = not yet evaluated
    confidence: Confidence = Confidence.UNKNOWN
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    # Ranking detail (Section 18 / Module 6-7): competing explanations are
    # scored against each other rather than listed as equally likely.
    title: str = ""
    category: str = "general"
    score: float = 0.0            # 0..1, higher = better supported by the data
    facts: list[str] = Field(default_factory=list)
    suggested_action: str = ""
    affected_amount: Optional[float] = None


class Note(BaseModel):
    author: str = "analyst"
    text: str
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())


class Finding(BaseModel):
    finding_id: str
    summary: str
    confidence: Confidence = Confidence.UNKNOWN
    evidence_ids: list[str] = Field(default_factory=list)


class Investigation(BaseModel):
    investigation_id: str
    question: str
    status: InvestigationStatus = InvestigationStatus.OPEN
    connection_id: Optional[str] = None
    object_ids: list[str] = Field(default_factory=list)       # graph nodes in scope
    snapshot_ids: list[str] = Field(default_factory=list)
    reconciliation_id: Optional[str] = None
    data_reconciliation_ids: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    confidence: Confidence = Confidence.UNKNOWN
    lineage: Optional[LineagePath] = None
    impact: Optional[ImpactResult] = None
    notes: list[Note] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    owner: Optional[str] = None
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: utcnow().isoformat())

    def next_evidence_id(self) -> str:
        return f"{self.investigation_id}-EV{len(self.evidence) + 1:03d}"

    def next_finding_id(self) -> str:
        return f"{self.investigation_id}-F{len(self.findings) + 1:03d}"

    def next_hypothesis_id(self) -> str:
        return f"{self.investigation_id}-H{len(self.hypotheses) + 1:03d}"

    def touch(self) -> None:
        self.updated_at = utcnow().isoformat()

    def add_evidence(self, item: EvidenceItem) -> None:
        self.evidence.append(item)
        self.updated_at = utcnow().isoformat()

    def recompute_confidence(self) -> None:
        """
        Overall investigation confidence is only as strong as its weakest
        finding — this is what keeps the platform from claiming certainty
        it hasn't earned (Section 3.3, Section 35 "No False Completeness").
        """
        if not self.findings:
            self.confidence = Confidence.UNKNOWN
            return
        weakest = min(self.findings, key=lambda f: CONFIDENCE_RANK[f.confidence])
        self.confidence = weakest.confidence
        self.updated_at = utcnow().isoformat()
