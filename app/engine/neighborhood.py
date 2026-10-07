"""
The neighbourhood of one object for the interactive lineage explorer:
everything N steps upstream (where its data comes from) and M steps
downstream (what uses it), as plain nodes + edges the browser lays out.

Each node carries how many direct upstream / downstream neighbours it has
in the whole graph, so the UI can offer "+" to expand one more step from
any node without re-fetching the whole graph.
"""
from __future__ import annotations

from typing import Any, Iterable

from app.engine.graph_store import GraphStore
from app.models.graph import DATA_FLOW_EDGES, Edge, Node, NodeType

# properties small enough to ship with every node (source_detail can be huge)
_NODE_PROPS = ("dax_expression", "expression", "visual_type", "page_name", "kind", "source_ref", "data_type",
               "calculated", "display_folder", "hidden", "upstream_traced")
MAX_NODES = 600


def _hidden(graph: GraphStore, hide_auto_date: bool) -> set[str]:
    if not hide_auto_date:
        return set()
    auto = {n.id for n in graph.all_nodes() if n.type == NodeType.TABLE and n.properties.get("kind") == "auto_date"}
    return auto | {n.id for n in graph.all_nodes() if n.parent_id in auto}


def _up_edges(graph: GraphStore, node_id: str, hidden: set[str]) -> list[Edge]:
    """Edges to what this node's data comes from (source -> target = 'depends on')."""
    return [e for e in graph.outgoing(node_id) if e.type in DATA_FLOW_EDGES and e.target_id not in hidden]


def _down_edges(graph: GraphStore, node_id: str, hidden: set[str]) -> list[Edge]:
    """Edges from the objects that use this node."""
    return [e for e in graph.incoming(node_id) if e.type in DATA_FLOW_EDGES and e.source_id not in hidden]


def _walk(graph: GraphStore, start: str, depth: int, direction: str, hidden: set[str], levels: dict[str, int],
          edges: dict[tuple[str, str, str], Edge], budget: list[int]) -> bool:
    """Breadth-first walk; returns True if it stopped early (depth or size)."""
    step = -1 if direction == "up" else 1
    frontier, truncated = [start], False
    for d in range(1, depth + 1):
        nxt: list[str] = []
        for node_id in frontier:
            for e in (_up_edges if direction == "up" else _down_edges)(graph, node_id, hidden):
                other = e.target_id if direction == "up" else e.source_id
                if other not in levels:
                    if budget[0] <= 0:
                        truncated = True
                        continue
                    budget[0] -= 1
                    levels[other] = levels[node_id] + step if node_id in levels else step * d
                    nxt.append(other)
                edges[(e.source_id, e.target_id, e.type.value)] = e
        frontier = nxt
        if not frontier:
            break
    else:
        truncated = truncated or any(
            (_up_edges if direction == "up" else _down_edges)(graph, n, hidden) for n in frontier)
    return truncated


def _node_out(graph: GraphStore, node: Node | None, node_id: str, level: int, hidden: set[str]) -> dict[str, Any]:
    if node is None:  # referenced but not in the model (renamed / removed field)
        return {"id": node_id, "name": node_id, "type": "MISSING", "level": level, "parent_id": None,
                "properties": {}, "up_count": 0, "down_count": 0, "confidence": "UNKNOWN"}
    return {
        "id": node.id, "name": node.name, "type": node.type.value, "level": level, "parent_id": node.parent_id,
        "properties": {k: node.properties[k] for k in _NODE_PROPS if node.properties.get(k) not in (None, "")},
        "up_count": len(_up_edges(graph, node.id, hidden)), "down_count": len(_down_edges(graph, node.id, hidden)),
        "confidence": node.confidence.value,
    }


def neighborhood(graph: GraphStore, node_id: str, up: int = 2, down: int = 2, hide_auto_date: bool = True,
                 base_level: int = 0, directions: Iterable[str] = ("up", "down")) -> dict[str, Any]:
    """Nodes within `up` steps upstream and `down` steps downstream of node_id.
    base_level lets an "expand" call place new nodes relative to where the
    clicked node already sits in the browser's layout."""
    if graph.get_node(node_id) is None:
        raise KeyError(f"'{node_id}' not found")
    hidden = _hidden(graph, hide_auto_date)
    levels: dict[str, int] = {node_id: base_level}
    edges: dict[tuple[str, str, str], Edge] = {}
    budget = [MAX_NODES]
    truncated_up = "up" in directions and _walk(graph, node_id, up, "up", hidden, levels, edges, budget)
    truncated_down = "down" in directions and _walk(graph, node_id, down, "down", hidden, levels, edges, budget)
    nodes = [_node_out(graph, graph.get_node(i), i, lvl, hidden) for i, lvl in levels.items()]
    return {
        "root": node_id,
        "nodes": nodes,
        "edges": [{"source": e.source_id, "target": e.target_id, "type": e.type.value, "confidence": e.confidence.value,
                   "origin": e.origin} for e in edges.values()],
        "truncated_up": bool(truncated_up),
        "truncated_down": bool(truncated_down),
        "limit_reached": budget[0] <= 0,
    }
