"""
Impact analysis engine (Section 21, Module 9; Section 22, Impact Risk
Scoring). Answers spec Question 3: "What will break if I change this?" —
e.g. what breaks if a column such as Stock[Buying Price] is deleted?
"""
from __future__ import annotations

from app.engine.graph_store import GraphStore
from app.models.graph import DATA_FLOW_EDGES, Confidence, EdgeType, NodeType, weakest
from app.models.reconciliation import AffectedNode, ImpactResult, RiskLevel

# Section 22: a broken visual (what people see) matters more than a broken
# intermediate measure. Reports are a roll-up of the visuals, so they are
# reported but not scored again — scoring them made any single visual HIGH.
_WEIGHTS = {NodeType.VISUAL: 2, NodeType.MEASURE: 1}
_REPORTED = (NodeType.MEASURE, NodeType.VISUAL, NodeType.REPORT)
_MAX_DEPTH = 10


def analyze_impact(store: GraphStore, target_node_id: str) -> ImpactResult:
    target = store.get_node(target_node_id)
    if target is None:
        return ImpactResult(
            target_node_id=target_node_id,
            risk=RiskLevel.LOW,
            confidence=Confidence.UNAVAILABLE,
            explanation=f"Node {target_node_id} not found in graph — impact cannot be assessed.",
        )

    dependents, cut_at = store.downstream_walk(target_node_id, max_depth=_MAX_DEPTH, edge_types=DATA_FLOW_EDGES)
    if target.type == NodeType.TABLE:
        # dropping a table also drops the measures stored in it
        for edge in store.incoming(target_node_id):
            if edge.type == EdgeType.CONTAINED_IN:
                dependents.append((edge, 1))
                more, cut = store.downstream_walk(edge.source_id, _MAX_DEPTH, DATA_FLOW_EDGES)
                dependents += [(e, d + 1) for e, d in more]
                cut_at += cut

    level = Confidence.VERIFIED
    found: dict[str, AffectedNode] = {}
    for edge, depth in dependents:
        node = store.get_node(edge.source_id)
        level = weakest(level, edge.confidence) if edge.confidence != Confidence.KNOWN else level
        if node is None or node.type not in (NodeType.MEASURE, NodeType.VISUAL):
            continue
        if node.id not in found or found[node.id].depth > depth:
            found[node.id] = AffectedNode(id=node.id, name=node.name, type=node.type.value, depth=depth)

    # roll affected visuals up to their pages' reports
    for visual in [n for n in found.values() if n.type == NodeType.VISUAL.value]:
        page = store.get_node(store.get_node(visual.id).parent_id or "")
        report = store.get_node(page.parent_id) if page and page.parent_id else None
        if report and report.id not in found:
            found[report.id] = AffectedNode(id=report.id, name=report.name, type=NodeType.REPORT.value, depth=visual.depth + 2)

    by_type = {t: sorted(n.id for n in found.values() if n.type == t.value) for t in _REPORTED}
    score = sum(_WEIGHTS[t] * len(by_type[t]) for t in _WEIGHTS)
    # LOW: at most one measure and no visual; MEDIUM: up to two visuals (or a
    # handful of measures); HIGH: three or more visuals or 6+ measures' worth.
    risk = RiskLevel.LOW if score <= 1 else RiskLevel.MEDIUM if score <= 5 else RiskLevel.HIGH

    m, v, r = (len(by_type[t]) for t in _REPORTED)
    explanation = (
        f"{target.name}: {m} dependent measure(s), {v} visual(s), {r} report(s) affected."
        if found
        else f"{target.name}: no dependents found in the current graph."
    )
    if cut_at:
        explanation += f" Stopped at depth {_MAX_DEPTH} — there may be further dependents."
        level = weakest(level, Confidence.PARTIALLY_VERIFIED)
    return ImpactResult(
        target_node_id=target_node_id,
        affected_measure_ids=by_type[NodeType.MEASURE],
        affected_visual_ids=by_type[NodeType.VISUAL],
        affected_report_ids=by_type[NodeType.REPORT],
        affected=sorted(found.values(), key=lambda n: (n.depth, n.type, n.name)),
        risk=risk,
        # "nothing depends on it" is only as good as the graph's coverage
        confidence=level if dependents else Confidence.PARTIALLY_VERIFIED,
        explanation=explanation,
    )
