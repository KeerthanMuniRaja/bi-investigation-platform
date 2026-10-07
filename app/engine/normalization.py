"""
Normalization layer (Section 32.1 architecture diagram: Metadata Extraction
-> Normalization -> Graph Builder).

This is the only place in the codebase that knows how to turn a specific
connector's raw shapes into the canonical Node/Edge graph. The engine
(lineage, impact, reconciliation) never sees a Measure or a Column object —
only Nodes and Edges.
"""
from __future__ import annotations

from app.connectors.base import (
    Column,
    Measure,
    Relationship,
    Report,
    Table,
    Visual,
)
from app.models.graph import Confidence, Edge, EdgeType, Node, NodeType

# How a measure's dependencies were obtained -> how much the edge is trusted.
_DEPENDENCY_CONFIDENCE = {
    "declared": Confidence.KNOWN,
    "engine": Confidence.VERIFIED,
    "parsed": Confidence.INFERRED,
}


def data_source_id(source_ref: str) -> str:
    return f"SRC::{source_ref}"


def normalize_report(report: Report) -> Node:
    return Node(id=report.id, type=NodeType.REPORT, name=report.name, properties={"workspace_id": report.workspace_id})


def normalize_visual(visual: Visual) -> tuple[Node, list[Edge]]:
    node = Node(
        id=visual.id,
        type=NodeType.VISUAL,
        name=visual.title,
        parent_id=visual.page_id,
        properties={"visual_type": visual.visual_type, "page_id": visual.page_id},
    )
    edges = [Edge(source_id=visual.id, target_id=visual.page_id, type=EdgeType.CONTAINED_IN)]
    edges += [
        Edge(source_id=visual.id, target_id=measure_id, type=EdgeType.USES)
        for measure_id in visual.measure_ids
    ]
    edges += [Edge(source_id=visual.id, target_id=fid, type=EdgeType.FILTERED_BY) for fid in visual.filter_ids]
    return node, edges


def normalize_measure(measure: Measure) -> tuple[Node, list[Edge]]:
    node = Node(
        id=measure.id,
        type=NodeType.MEASURE,
        name=measure.name,
        parent_id=measure.table_id,
        properties={"dax_expression": measure.dax_expression, "dependency_source": measure.dependency_source,
                    "display_folder": measure.display_folder, "description": measure.description},
    )
    confidence = _DEPENDENCY_CONFIDENCE.get(measure.dependency_source, Confidence.INFERRED)
    origin = f"{measure.dependency_source}_dependencies"
    # The home table is where the measure is stored, not where its data
    # comes from, so it's containment, not lineage.
    edges = [Edge(source_id=measure.id, target_id=measure.table_id, type=EdgeType.CONTAINED_IN)]
    edges += [
        Edge(source_id=measure.id, target_id=dep_id, type=EdgeType.DEPENDS_ON, confidence=confidence, origin=origin)
        for dep_id in measure.depends_on_measure_ids
    ]
    edges += [
        Edge(source_id=measure.id, target_id=col_id, type=EdgeType.DERIVED_FROM, confidence=confidence, origin=origin)
        for col_id in measure.depends_on_column_ids
    ]
    edges += [
        Edge(source_id=measure.id, target_id=table_id, type=EdgeType.DERIVED_FROM, confidence=confidence, origin=origin)
        for table_id in measure.depends_on_table_ids
    ]
    return node, edges


def normalize_table(table: Table) -> tuple[list[Node], list[Edge]]:
    nodes = [Node(id=table.id, type=NodeType.TABLE, name=table.name, properties={
        "source_ref": table.source_ref, "kind": table.kind, "hidden": table.hidden,
        "expression": table.expression, "source_detail": table.source_detail,
    })]
    edges = [
        Edge(source_id=table.id, target_id=dep, type=EdgeType.DERIVED_FROM, confidence=Confidence.INFERRED, origin="query_reference")
        for dep in table.depends_on_table_ids
    ]
    edges += [
        Edge(source_id=table.id, target_id=dep, type=EdgeType.DERIVED_FROM, confidence=Confidence.INFERRED, origin="parsed_dependencies")
        for dep in table.depends_on_column_ids
    ]
    if table.source_ref:
        source_id = data_source_id(table.source_ref)
        # The connector reported where the table loads from, but nothing
        # upstream of that (ETL, views, stored procedures) has been traced.
        nodes.append(Node(
            id=source_id, type=NodeType.DATA_SOURCE, name=table.source_ref,
            properties={"upstream_traced": False, **({"sources": table.source_detail.get("sources")} if table.source_detail.get("sources") else {})},
            confidence=Confidence.PARTIALLY_VERIFIED,
        ))
        edges.append(Edge(source_id=table.id, target_id=source_id, type=EdgeType.SOURCED_FROM))
    return nodes, edges


def normalize_column(column: Column) -> tuple[Node, list[Edge]]:
    node = Node(id=column.id, type=NodeType.COLUMN, name=column.name, parent_id=column.table_id, properties={
        "data_type": column.data_type, "hidden": column.hidden, "expression": column.expression,
        "calculated": bool(column.expression),
    })
    if column.expression:
        # a calculated column's values come from its formula, not the source table load
        edges = [Edge(source_id=column.id, target_id=column.table_id, type=EdgeType.CONTAINED_IN)]
        edges += [Edge(source_id=column.id, target_id=d, type=EdgeType.DERIVED_FROM, confidence=Confidence.INFERRED,
                       origin="parsed_dependencies") for d in column.depends_on_column_ids]
        edges += [Edge(source_id=column.id, target_id=d, type=EdgeType.DEPENDS_ON, confidence=Confidence.INFERRED,
                       origin="parsed_dependencies") for d in column.depends_on_measure_ids]
    else:
        edges = [Edge(source_id=column.id, target_id=column.table_id, type=EdgeType.BELONGS_TO)]
    return node, edges


def normalize_relationship(rel: Relationship) -> tuple[Node, list[Edge]]:
    node = Node(
        id=rel.id,
        type=NodeType.RELATIONSHIP,
        name=f"{rel.from_column_id} -> {rel.to_column_id}",
        properties={"cross_filter_direction": rel.cross_filter_direction, "is_active": rel.is_active,
                    "cardinality": rel.cardinality, "from_column_id": rel.from_column_id, "to_column_id": rel.to_column_id},
    )
    edges = [
        Edge(source_id=rel.from_table_id, target_id=rel.to_table_id, type=EdgeType.RELATES_TO, origin=rel.id),
    ]
    return node, edges
