"""
Extraction orchestrator. This is the "Metadata Extraction -> Normalization
-> Graph Builder" pipeline from the Section 32.1 architecture diagram,
connector-agnostic by construction (it only calls BIConnector methods).
"""
from __future__ import annotations

from app.connectors.base import BIConnector
from app.engine import normalization as norm
from app.engine.graph_store import GraphStore
from app.models.graph import Edge, EdgeType, Node, NodeType


def build_graph_for_model(store: GraphStore, connector: BIConnector, model_id: str) -> None:
    tables = connector.get_tables(model_id)
    for table in tables:
        nodes, edges = norm.normalize_table(table)
        store.add_nodes(nodes)
        store.add_edges(edges)
        for column in connector.get_columns(model_id, table.id):
            col_node, col_edges = norm.normalize_column(column)
            store.add_node(col_node)
            store.add_edges(col_edges)

    for measure in connector.get_measures(model_id):
        node, edges = norm.normalize_measure(measure)
        store.add_node(node)
        store.add_edges(edges)

    for rel in connector.get_relationships(model_id):
        node, edges = norm.normalize_relationship(rel)
        store.add_node(node)
        store.add_edges(edges)


def build_graph_for_report(store: GraphStore, connector: BIConnector, report) -> None:
    store.add_node(norm.normalize_report(report))
    for page in connector.get_pages(report.id):
        store.add_node(Node(id=page.id, type=NodeType.PAGE, name=page.name, parent_id=report.id))
        store.add_edge(Edge(source_id=page.id, target_id=report.id, type=EdgeType.CONTAINED_IN))
        for visual in connector.get_visuals(report.id, page.id):
            node, edges = norm.normalize_visual(visual)
            node.properties["page_name"] = page.name
            store.add_node(node)
            store.add_edges(edges)


def build_full_graph(store: GraphStore, connector: BIConnector, workspace_id: str) -> list[str]:
    """Extract every report + its semantic model into one graph store.
    Returns warnings for parts that couldn't be extracted: a report whose
    visuals aren't available still contributes its model, and the gap is
    reported rather than failing the whole sync."""
    warnings: list[str] = []
    models_done: set[str] = set()
    for report in connector.get_reports(workspace_id):
        try:
            build_graph_for_report(store, connector, report)
        except NotImplementedError as e:
            store.add_node(norm.normalize_report(report))
            warnings.append(f"{report.name}: visuals not extracted - {e}")
        if report.model_id and report.model_id not in models_done:
            models_done.add(report.model_id)
            try:
                build_graph_for_model(store, connector, report.model_id)
            except NotImplementedError as e:
                warnings.append(f"{report.name}: semantic model not extracted - {e}")
    return warnings
