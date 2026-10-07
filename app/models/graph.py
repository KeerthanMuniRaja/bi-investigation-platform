"""
Canonical graph model.

Every fact the platform knows — a report, a visual, a measure, a column,
a relationship — becomes a Node. Every dependency between them becomes an
Edge. Lineage, impact analysis, and root-cause tracing are all just
traversals over this graph.

Spec reference: Section 36 (Internal Graph Model), Section 3.3 (Explicit
Uncertainty).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Confidence(str, Enum):
    """
    The platform must never claim more certainty than its evidence supports.
    This enum is attached to nodes, edges, lineage paths, and findings —
    never just a boolean "resolved" flag.
    """

    KNOWN = "KNOWN"
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    INFERRED = "INFERRED"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


# Weakest -> strongest. Shared by every rollup so "weakest link" means the
# same thing in lineage, investigations and reconciliations.
CONFIDENCE_RANK: dict["Confidence", int] = {
    Confidence.UNSUPPORTED: 0,
    Confidence.UNAVAILABLE: 1,
    Confidence.UNKNOWN: 2,
    Confidence.INFERRED: 3,
    Confidence.PARTIALLY_VERIFIED: 4,
    Confidence.VERIFIED: 5,
    Confidence.KNOWN: 6,
}


def weakest(*levels: "Confidence") -> "Confidence":
    return min(levels, key=lambda c: CONFIDENCE_RANK[c])


class NodeType(str, Enum):
    PLATFORM = "PLATFORM"
    WORKSPACE = "WORKSPACE"
    REPORT = "REPORT"
    PAGE = "PAGE"
    VISUAL = "VISUAL"
    MEASURE = "MEASURE"
    COLUMN = "COLUMN"
    TABLE = "TABLE"
    RELATIONSHIP = "RELATIONSHIP"
    MODEL = "MODEL"
    DATA_SOURCE = "DATA_SOURCE"
    FILTER = "FILTER"
    SNAPSHOT = "SNAPSHOT"
    INVESTIGATION = "INVESTIGATION"
    EVIDENCE = "EVIDENCE"


class EdgeType(str, Enum):
    USES = "USES"                # visual -> measure, visual -> column
    DEPENDS_ON = "DEPENDS_ON"    # measure -> measure
    DERIVED_FROM = "DERIVED_FROM"  # column -> table, measure -> column
    BELONGS_TO = "BELONGS_TO"    # column -> table (a column's data comes from its table)
    RELATES_TO = "RELATES_TO"    # table -> table via relationship
    FILTERED_BY = "FILTERED_BY"  # visual -> filter
    SOURCED_FROM = "SOURCED_FROM"  # table -> data source
    # Containment only (visual -> page -> report, measure -> its home
    # table). Not a data dependency, so lineage never walks it; impact
    # analysis uses it only to roll affected visuals up to reports.
    CONTAINED_IN = "CONTAINED_IN"


# Edges whose direction means "my data comes from there".
DATA_FLOW_EDGES = frozenset({
    EdgeType.USES, EdgeType.DEPENDS_ON, EdgeType.DERIVED_FROM, EdgeType.BELONGS_TO, EdgeType.SOURCED_FROM,
    EdgeType.FILTERED_BY,
})


class Node(BaseModel):
    id: str
    type: NodeType
    platform: str = "powerbi"
    name: str
    parent_id: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)
    version: str = "v1"
    confidence: Confidence = Confidence.KNOWN


class Edge(BaseModel):
    source_id: str
    target_id: str
    type: EdgeType
    confidence: Confidence = Confidence.KNOWN
    origin: str = "powerbi_metadata"
    version: str = "v1"


class CapabilityStatus(str, Enum):
    """Per-extraction-mode capability, per spec Section 34."""

    CAPABLE = "CAPABLE"
    PARTIALLY_CAPABLE = "PARTIALLY_CAPABLE"
    UNSUPPORTED = "UNSUPPORTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
