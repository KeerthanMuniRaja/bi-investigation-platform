"""
Lineage engine (Section 16, Module 5). Answers spec Question 1: "Where did
this number come from?" by walking the upstream graph from a visual to its
ultimate source, and reports honest coverage instead of claiming
completeness it can't back up (Section 17, Section 35).

Only data-flow edges are walked (visual -> measure -> column -> table ->
source). Containment (visual -> page, measure -> home table) is not
lineage. Relationships are included only on request, since they propagate
filters rather than supply values.
"""
from __future__ import annotations

from app.engine.graph_store import GraphStore
from app.models.graph import CONFIDENCE_RANK, DATA_FLOW_EDGES, Confidence, EdgeType, NodeType, weakest
from app.models.lineage import LineagePath, LineageStep


def trace_upstream(store: GraphStore, root_id: str, max_depth: int = 10, include_relationships: bool = False) -> LineagePath:
    root = store.get_node(root_id)
    if root is None:
        return LineagePath(
            root_id=root_id,
            coverage_pct=0.0,
            gaps=[f"node {root_id} not found in graph — has the connection been synced?"],
            overall_confidence=Confidence.UNAVAILABLE,
        )

    edge_types = set(DATA_FLOW_EDGES) | ({EdgeType.RELATES_TO} if include_relationships else set())
    edges_with_depth, cut_at = store.upstream_walk(root_id, max_depth=max_depth, edge_types=edge_types)
    steps: list[LineageStep] = []
    gaps: list[str] = []
    level = Confidence.KNOWN
    untraced_sources: set[str] = set()

    for edge, depth in edges_with_depth:
        source_node = store.get_node(edge.source_id)
        target_node = store.get_node(edge.target_id)
        if target_node is None:
            gaps.append(f"{edge.source_id} -> {edge.target_id} ({edge.type.value}): target not resolvable")
            continue
        steps.append(
            LineageStep(
                from_id=edge.source_id,
                from_name=source_node.name if source_node else edge.source_id,
                to_id=edge.target_id,
                to_name=target_node.name,
                edge_type=edge.type,
                confidence=edge.confidence,
                depth=depth,
            )
        )
        level = weakest(level, edge.confidence)
        if target_node.type == NodeType.DATA_SOURCE and not target_node.properties.get("upstream_traced", False):
            untraced_sources.add(target_node.name)

    for source in sorted(untraced_sources):
        gaps.append(f"Upstream of source '{source}' (ETL, views, stored procedures) is not traced")
    if cut_at:
        names = [(store.get_node(n).name if store.get_node(n) else n) for n in cut_at]
        gaps.append(f"Stopped at depth {max_depth}: lineage continues beyond {', '.join(names[:5])}"
                    + (f" and {len(names) - 5} more" if len(names) > 5 else ""))

    if not steps and root.type in (NodeType.VISUAL, NodeType.MEASURE):
        gaps.append(f"No dependencies recorded for {root.name} — the connector may not expose them")

    # Coverage = share of recorded dependency edges that resolve to a known
    # node. "Nothing traced upstream of the source system" is a scope limit,
    # reported separately (untraced_sources + gap), not a resolution failure.
    total = len(edges_with_depth)
    resolved = len(steps)
    coverage = 100.0 if total == 0 else round(100.0 * resolved / total, 1)
    if gaps:
        level = weakest(level, Confidence.PARTIALLY_VERIFIED)
    if not steps:
        level = weakest(level, Confidence.UNKNOWN)

    return LineagePath(root_id=root_id, steps=steps, coverage_pct=coverage, gaps=gaps, overall_confidence=level,
                       untraced_sources=sorted(untraced_sources), truncated=bool(cut_at))


__all__ = ["trace_upstream", "CONFIDENCE_RANK"]
