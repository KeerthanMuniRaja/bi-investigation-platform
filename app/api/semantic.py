"""
Semantic model metadata, lineage and impact over the synced graph.
Node ids can contain brackets, spaces and slashes (Stock[Buying Price],
SRC::sql://...), so they are passed as query parameters, not path parts.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.connectors.base import Column, Measure, Relationship, SemanticModel, Table
from app.engine.impact import analyze_impact
from app.engine.lineage import trace_upstream
from app.engine.neighborhood import neighborhood
from app.models.graph import Edge, Node, NodeType
from app.models.lineage import LineagePath
from app.models.reconciliation import ImpactResult
from app.store.database import Store, get_store

router = APIRouter(tags=["semantic-models & lineage"])


@router.get("/models/{model_id}", response_model=SemanticModel)
def get_model(model_id: str, connection_id: str, store: Store = Depends(get_store)) -> SemanticModel:
    return store.connector(connection_id).get_semantic_model(model_id)


@router.get("/models/{model_id}/measures", response_model=list[Measure])
def get_measures(model_id: str, connection_id: str, store: Store = Depends(get_store)) -> list[Measure]:
    return store.connector(connection_id).get_measures(model_id)


@router.get("/models/{model_id}/tables", response_model=list[Table])
def get_tables(model_id: str, connection_id: str, store: Store = Depends(get_store)) -> list[Table]:
    return store.connector(connection_id).get_tables(model_id)


@router.get("/models/{model_id}/columns", response_model=list[Column])
def get_columns(model_id: str, table_id: str, connection_id: str, store: Store = Depends(get_store)) -> list[Column]:
    return store.connector(connection_id).get_columns(model_id, table_id)


@router.get("/models/{model_id}/relationships", response_model=list[Relationship])
def get_relationships(model_id: str, connection_id: str, store: Store = Depends(get_store)) -> list[Relationship]:
    return store.connector(connection_id).get_relationships(model_id)


def _graph(store: Store, connection_id: str):
    store.connection_records[connection_id]  # 404 if unknown
    graph = store.graph_for(connection_id)
    if not graph.all_nodes():
        raise HTTPException(409, f"Connection {connection_id} has not been synced yet — POST /connections/{connection_id}/sync first.")
    return graph


@router.get("/connections/{connection_id}/nodes", response_model=list[Node])
def search_nodes(connection_id: str, search: str = "", type: Optional[NodeType] = None, limit: int = 100,
                 include_auto_date: bool = False, store: Store = Depends(get_store)) -> list[Node]:
    graph = _graph(store, connection_id)
    nodes = graph.search(search, limit=100_000) if search else graph.all_nodes()
    if type:
        nodes = [n for n in nodes if n.type == type]
    if not include_auto_date:
        auto = {n.id for n in graph.all_nodes() if n.type == NodeType.TABLE and n.properties.get("kind") == "auto_date"}
        nodes = [n for n in nodes if n.id not in auto and n.parent_id not in auto]
    nodes = [n for n in nodes if n.type not in (NodeType.PAGE, NodeType.RELATIONSHIP, NodeType.REPORT) or type]
    order = {NodeType.VISUAL: 0, NodeType.MEASURE: 1, NodeType.COLUMN: 2, NodeType.TABLE: 3, NodeType.DATA_SOURCE: 4}
    return sorted(nodes, key=lambda n: (order.get(n.type, 9), n.name.lower()))[:limit]


@router.get("/connections/{connection_id}/graph")
def get_graph(connection_id: str, store: Store = Depends(get_store)) -> dict:
    graph = _graph(store, connection_id)
    return {"nodes": [n.model_dump() for n in graph.all_nodes()], "edges": [e.model_dump() for e in graph.all_edges()]}


@router.get("/connections/{connection_id}/lineage", response_model=LineagePath)
def lineage(connection_id: str, node_id: str, include_relationships: bool = False, store: Store = Depends(get_store)) -> LineagePath:
    return trace_upstream(_graph(store, connection_id), node_id, include_relationships=include_relationships)


@router.get("/connections/{connection_id}/lineage-graph")
def lineage_graph(connection_id: str, node_id: str, up: int = Query(2, ge=0, le=12), down: int = Query(2, ge=0, le=12),
                  hide_auto_date: bool = True, store: Store = Depends(get_store)) -> dict:
    """Interactive lineage: the object plus `up` steps upstream and `down`
    steps downstream, with per-node neighbour counts for expanding further."""
    return neighborhood(_graph(store, connection_id), node_id, up, down, hide_auto_date)


@router.get("/connections/{connection_id}/lineage-graph/expand")
def lineage_graph_expand(connection_id: str, node_id: str, direction: str = Query("up", pattern="^(up|down)$"),
                         steps: int = Query(1, ge=1, le=12), level: int = 0, hide_auto_date: bool = True,
                         store: Store = Depends(get_store)) -> dict:
    """One node's next `steps` upstream or downstream, placed relative to its current level."""
    return neighborhood(_graph(store, connection_id), node_id, steps if direction == "up" else 0,
                        steps if direction == "down" else 0, hide_auto_date, base_level=level, directions=(direction,))


@router.get("/connections/{connection_id}/impact", response_model=ImpactResult)
def impact(connection_id: str, node_id: str, store: Store = Depends(get_store)) -> ImpactResult:
    return analyze_impact(_graph(store, connection_id), node_id)


@router.get("/connections/{connection_id}/edges", response_model=list[Edge])
def node_edges(connection_id: str, node_id: str, store: Store = Depends(get_store)) -> list[Edge]:
    graph = _graph(store, connection_id)
    return graph.outgoing(node_id) + graph.incoming(node_id)
