"""
In-memory graph store.

Section 36 is explicit that the first implementation should not force a
graph-database decision too early ("A graph database may eventually be
appropriate, but..."). This is that first implementation: plain dicts,
swappable later for Neo4j/whatever without changing anything upstream,
because engine code only calls these methods, never touches storage
directly.
"""
from __future__ import annotations

from collections import defaultdict

from typing import Callable, Iterable, Optional

from app.models.graph import Edge, EdgeType, Node


class GraphStore:
    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._out_edges: dict[str, list[Edge]] = defaultdict(list)   # source_id -> edges
        self._in_edges: dict[str, list[Edge]] = defaultdict(list)    # target_id -> edges
        self._edge_keys: set[tuple[str, str, str, str]] = set()
        # ids claimed by two different node types (e.g. a table called 'report'
        # vs the report node): the first one is kept and the clash recorded
        self.collisions: list[str] = []

    def clear(self) -> None:
        self.__init__()

    # ------------------------------------------------------------- writes
    def add_node(self, node: Node) -> None:
        existing = self._nodes.get(node.id)
        if existing is not None and existing.type != node.type:
            note = f"id '{node.id}' is used by a {existing.type.value} and a {node.type.value}; kept the {existing.type.value}"
            if note not in self.collisions:
                self.collisions.append(note)
            return
        self._nodes[node.id] = node  # same type: a re-sync refreshes the node

    def add_nodes(self, nodes: list[Node]) -> None:
        for n in nodes:
            self.add_node(n)

    def add_edge(self, edge: Edge) -> None:
        # relationships are keyed by their id too: an active and an inactive
        # relationship between the same two tables are both real
        key = (edge.source_id, edge.target_id, edge.type.value, edge.origin if edge.type == EdgeType.RELATES_TO else "")
        if key in self._edge_keys:  # re-syncing must not duplicate lineage
            return
        self._edge_keys.add(key)
        self._out_edges[edge.source_id].append(edge)
        self._in_edges[edge.target_id].append(edge)

    def add_edges(self, edges: list[Edge]) -> None:
        for e in edges:
            self.add_edge(e)

    # -------------------------------------------------------------- reads
    def get_node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def outgoing(self, node_id: str) -> list[Edge]:
        return self._out_edges.get(node_id, [])

    def incoming(self, node_id: str) -> list[Edge]:
        return self._in_edges.get(node_id, [])

    def upstream(self, node_id: str, max_depth: int = 10,
                 edge_types: Optional[Iterable[EdgeType]] = None) -> list[tuple[Edge, int]]:
        """Everything this node depends on (walk outgoing edges), with depth."""
        return self.upstream_walk(node_id, max_depth, edge_types)[0]

    def upstream_walk(self, node_id: str, max_depth: int = 10,
                      edge_types: Optional[Iterable[EdgeType]] = None) -> tuple[list[tuple[Edge, int]], list[str]]:
        """upstream() plus the nodes where max_depth cut the walk short."""
        return self._walk(node_id, max_depth, self._filtered(self.outgoing, edge_types), lambda e: e.target_id)

    def downstream(self, node_id: str, max_depth: int = 10,
                   edge_types: Optional[Iterable[EdgeType]] = None) -> list[tuple[Edge, int]]:
        """Everything that depends on this node (walk incoming edges), with depth."""
        return self.downstream_walk(node_id, max_depth, edge_types)[0]

    def downstream_walk(self, node_id: str, max_depth: int = 10,
                        edge_types: Optional[Iterable[EdgeType]] = None) -> tuple[list[tuple[Edge, int]], list[str]]:
        """downstream() plus the nodes where max_depth cut the walk short."""
        return self._walk(node_id, max_depth, self._filtered(self.incoming, edge_types), lambda e: e.source_id)

    @staticmethod
    def _filtered(fn: Callable[[str], list[Edge]], edge_types: Optional[Iterable[EdgeType]]) -> Callable[[str], list[Edge]]:
        if edge_types is None:
            return fn
        allowed = set(edge_types)
        return lambda node_id: [e for e in fn(node_id) if e.type in allowed]

    def all_edges(self) -> list[Edge]:
        return [e for edges in self._out_edges.values() for e in edges]

    def search(self, text: str, limit: int = 50) -> list[Node]:
        needle = text.lower()
        hits = [n for n in self._nodes.values() if needle in n.name.lower() or needle in n.id.lower()]
        return sorted(hits, key=lambda n: (len(n.name), n.name))[:limit]

    def _walk(self, start_id, max_depth, edge_fn, next_id_fn) -> tuple[list[tuple[Edge, int]], list[str]]:
        visited: set[str] = {start_id}
        frontier = [start_id]
        results: list[tuple[Edge, int]] = []
        depth = 0
        while frontier and depth < max_depth:
            depth += 1
            next_frontier = []
            for node_id in frontier:
                for edge in edge_fn(node_id):
                    results.append((edge, depth))
                    nxt = next_id_fn(edge)
                    if nxt not in visited:
                        visited.add(nxt)
                        next_frontier.append(nxt)
            frontier = next_frontier
        # nodes reached at the depth limit whose own edges were never followed
        truncated = [n for n in frontier if edge_fn(n)] if depth >= max_depth else []
        return results, truncated

    def all_nodes(self) -> list[Node]:
        return list(self._nodes.values())
