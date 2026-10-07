from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.graph import Confidence, EdgeType


class LineageStep(BaseModel):
    from_id: str
    from_name: str
    to_id: str
    to_name: str
    edge_type: EdgeType
    confidence: Confidence
    depth: int


class LineagePath(BaseModel):
    root_id: str
    steps: list[LineageStep] = Field(default_factory=list)
    coverage_pct: float = 100.0   # share of dependency edges that resolve to a known node
    gaps: list[str] = Field(default_factory=list)
    overall_confidence: Confidence = Confidence.VERIFIED
    untraced_sources: list[str] = Field(default_factory=list)  # data sources whose own upstream (ETL) isn't traced
    truncated: bool = False       # the walk hit max_depth before running out of edges

    def as_chain_strings(self) -> list[str]:
        """'Visual -> Measure -> {Column A, Column B} -> Table', one entry per
        depth level. Steps are a tree, not a line, so siblings are grouped
        rather than chained (chaining them would invent dependencies)."""
        if not self.steps:
            return []
        root = next((s.from_name for s in self.steps if s.from_id == self.root_id), self.steps[0].from_name)
        placed: set[str] = {self.root_id}
        levels: dict[int, list[str]] = {}
        for step in sorted(self.steps, key=lambda s: s.depth):
            if step.to_id in placed:
                continue
            placed.add(step.to_id)
            levels.setdefault(step.depth, []).append(step.to_name)
        chain = [root]
        for depth in sorted(levels):
            names = levels[depth]
            shown = ", ".join(names[:6]) + (f", +{len(names) - 6} more" if len(names) > 6 else "")
            chain.append(shown if len(names) == 1 else "{" + shown + "}")
        return chain

    def as_paths(self, limit: int = 25) -> list[str]:
        """Root-to-leaf paths, e.g. 'Visual -> Measure -> Column -> Table'."""
        children: dict[str, list[LineageStep]] = {}
        for step in self.steps:
            children.setdefault(step.from_id, []).append(step)
        if not children:
            return []
        root_name = next((s.from_name for s in self.steps if s.from_id == self.root_id), self.root_id)
        paths: list[str] = []
        stack: list[tuple[str, list[str], frozenset[str]]] = [(self.root_id, [root_name], frozenset({self.root_id}))]
        while stack and len(paths) < limit:
            node_id, names, seen = stack.pop()
            nxt = [s for s in children.get(node_id, []) if s.to_id not in seen]
            if not nxt:
                paths.append(" -> ".join(names))
                continue
            for step in reversed(nxt):
                stack.append((step.to_id, names + [step.to_name], seen | {step.to_id}))
        return paths
