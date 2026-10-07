"""
Model understanding: turn a semantic model + report into a documented,
reviewed picture a BI developer or analyst can read.

  overview      counts, data sources, tables, measures (with plain-English
                explanations and where they're used), relationships, pages
                and visuals
  health check  broken references, unused measures/columns, auto date
                tables, relationship risks, disconnected tables, hard-coded
                values, complexity — each with the objects involved

Works from any connector (PBIX/PBIT/PBIP file, live Power BI, Tableau): it
reads the connector's model lists plus the synced lineage graph. File-only
extras (row counts, hidden pages, unresolved DAX refs) are used when the
connector provides them.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from app.connectors.base import BIConnector
from app.engine.dax_explain import category, explain
from app.engine.graph_store import GraphStore
from app.models.graph import DATA_FLOW_EDGES, EdgeType, NodeType

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
_HARDCODED_YEAR = re.compile(r"(?<![\w\[])(20[1-3]\d)(?![\w\]])")


def _dependents(graph: GraphStore, node_id: str) -> set[str]:
    return {e.source_id for e, _ in graph.downstream(node_id, max_depth=12, edge_types=DATA_FLOW_EDGES)}


def build_overview(connector: BIConnector, graph: GraphStore, model_id: str, name: str) -> dict[str, Any]:
    loaded = getattr(connector, "model", None) if hasattr(connector, "path") else None
    tables = connector.get_tables(model_id)
    columns = {t.id: connector.get_columns(model_id, t.id) for t in tables}
    measures = connector.get_measures(model_id)
    relationships = connector.get_relationships(model_id)
    row_counts: dict[str, int] = getattr(loaded, "row_counts", {}) or {}
    unresolved: dict[str, list[str]] = getattr(loaded, "unresolved", {}) or {}

    nodes = {n.id: n for n in graph.all_nodes()}
    visuals = [n for n in nodes.values() if n.type == NodeType.VISUAL]
    pages = {n.id: n for n in nodes.values() if n.type == NodeType.PAGE}
    rel_columns = {r.from_column_id for r in relationships} | {r.to_column_id for r in relationships}
    rel_tables = {r.from_table_id for r in relationships} | {r.to_table_id for r in relationships}

    def names(ids: list[str]) -> list[str]:
        return [nodes[i].name if i in nodes else i for i in ids]

    # ----------------------------------------------------------- measures
    measure_rows = []
    for m in measures:
        deps = _dependents(graph, m.id)
        used_visuals = sorted(d for d in deps if d in nodes and nodes[d].type == NodeType.VISUAL)
        used_measures = sorted(d for d in deps if d in nodes and nodes[d].type == NodeType.MEASURE)
        measure_rows.append({
            "id": m.id, "name": m.name, "table": m.table_id, "folder": m.display_folder, "description": m.description,
            "expression": m.dax_expression, "explanation": explain(m.dax_expression), "category": category(m.dax_expression),
            "depends_on_measures": names(m.depends_on_measure_ids), "depends_on_columns": m.depends_on_column_ids,
            "depends_on_tables": m.depends_on_table_ids,
            "used_by_visuals": len(used_visuals), "used_by_measures": len(used_measures),
            "visual_ids": used_visuals[:50], "unused": not used_visuals, "unresolved": unresolved.get(m.id, []),
        })

    # ------------------------------------------------------------- tables
    table_rows, unused_columns, hidden_unused = [], [], []
    for t in tables:
        cols = columns.get(t.id, [])
        col_rows = []
        for c in cols:
            deps = _dependents(graph, c.id)
            used = bool(deps) or c.id in rel_columns
            col_rows.append({"id": c.id, "name": c.name, "data_type": c.data_type, "hidden": c.hidden,
                             "calculated": bool(c.expression), "expression": c.expression, "used": used,
                             "explanation": explain(c.expression) if c.expression else None})
            if not used and t.kind not in ("auto_date", "parameter", "measures"):
                (hidden_unused if c.hidden else unused_columns).append(c.id)
        table_used = any(r["used"] for r in col_rows) or any(m.table_id == t.id and m.id for m in measures) or t.id in rel_tables
        table_rows.append({
            "id": t.id, "name": t.name, "kind": t.kind, "hidden": t.hidden, "rows": row_counts.get(t.name),
            "columns": col_rows, "column_count": len(cols), "calculated_columns": sum(1 for c in cols if c.expression),
            "measure_count": sum(1 for m in measures if m.table_id == t.id), "source": t.source_ref,
            "summary": t.source_detail.get("summary") if t.source_detail else None,
            "steps": t.source_detail.get("steps", []) if t.source_detail else [],
            "expression": t.expression, "depends_on_tables": t.depends_on_table_ids, "used": table_used,
            "has_relationships": t.id in rel_tables,
        })

    # --------------------------------------------------------- data sources
    sources: dict[str, dict[str, Any]] = {}
    for t in tables:
        for s in (t.source_detail or {}).get("sources", []) or ([{"display": t.source_ref, "kind": "source"}] if t.source_ref else []):
            key = "/".join(str(s.get(k) or "") for k in ("kind", "server", "database", "path")) or s["display"]
            entry = sources.setdefault(key, {"kind": s.get("kind"), "server": s.get("server"), "database": s.get("database"),
                                             "path": s.get("path"), "tables": []})
            obj = ".".join(x for x in (s.get("schema"), s.get("item")) if x)
            entry["tables"].append({"model_table": t.name, "source_object": obj or None, "native_query": bool(s.get("native_query"))})

    # ------------------------------------------------------ report / pages
    page_rows: dict[str, dict[str, Any]] = {}
    broken_by_field: dict[str, list[str]] = defaultdict(list)  # missing field -> visuals referencing it
    for v in visuals:
        page_id = v.parent_id or ""
        page = page_rows.setdefault(page_id, {"id": page_id, "name": pages[page_id].name if page_id in pages else page_id, "visuals": []})
        uses = [e.target_id for e in graph.outgoing(v.id) if e.type == EdgeType.USES]
        filters = [e.target_id for e in graph.outgoing(v.id) if e.type == EdgeType.FILTERED_BY]
        broken = [i for i in uses + filters if i not in nodes]
        for b in broken:
            broken_by_field[b].append(f"{page['name']} › {v.name}")
        page["visuals"].append({"id": v.id, "title": v.name, "type": v.properties.get("visual_type"),
                                "fields": [{"id": i, "name": nodes[i].name if i in nodes else i, "type": nodes[i].type.value if i in nodes else "MISSING"} for i in uses],
                                "filters": [{"id": i, "name": nodes[i].name if i in nodes else i, "missing": i not in nodes} for i in filters],
                                "broken": broken})
    loaded_pages = getattr(loaded, "pages", []) or []
    order = [p.id for p in loaded_pages]
    extra = {p.id: p for p in loaded_pages}
    page_list = sorted(page_rows.values(), key=lambda p: order.index(p["id"]) if p["id"] in order else len(order))
    for p in page_list:
        lp = extra.get(p["id"])
        p["hidden"] = bool(lp and lp.hidden)
        p["other_visuals"] = lp.other_visuals if lp else 0
    for lp in loaded_pages:
        if lp.id not in page_rows:
            page_list.append({"id": lp.id, "name": lp.name, "visuals": [], "hidden": lp.hidden, "other_visuals": lp.other_visuals})

    # ------------------------------------------------------ relationships
    column_types = {c.id: c.data_type for cols in columns.values() for c in cols}
    table_kind = {t.id: t.kind for t in tables}
    relationship_rows = []
    for r in relationships:
        missing = [cid for cid in (r.from_column_id, r.to_column_id) if cid not in column_types]
        types = (column_types.get(r.from_column_id), column_types.get(r.to_column_id))
        relationship_rows.append({
            "id": r.id, "from": r.from_column_id, "to": r.to_column_id,
            "from_table": r.from_table_id, "to_table": r.to_table_id,
            "from_column": r.from_column, "to_column": r.to_column,
            "from_kind": table_kind.get(r.from_table_id), "to_kind": table_kind.get(r.to_table_id),
            "direction": r.cross_filter_direction, "active": r.is_active, "cardinality": r.cardinality,
            "from_type": types[0], "to_type": types[1],
            "type_mismatch": bool(all(types) and _type_family(types[0]) != _type_family(types[1])),
            "missing": missing,
        })

    # ---------------------------------------------------------- insights
    insights = _insights(tables, measure_rows, table_rows, relationships, relationship_rows, unresolved, broken_by_field,
                         len(visuals), unused_columns, hidden_unused, page_list, sources)
    kinds: dict[str, int] = defaultdict(int)
    for t in tables:
        kinds[t.kind] += 1
    return {
        "name": name,
        "format": getattr(loaded, "file_format", None),
        "warnings": getattr(loaded, "warnings", []) or [],
        "counts": {
            "pages": len(page_list), "visuals": len(visuals),
            "tables": sum(v for k, v in kinds.items() if k != "auto_date"), "tables_by_kind": dict(kinds),
            "columns": sum(len(c) for t, c in columns.items() if not _is_auto(tables, t)),
            "calculated_columns": sum(1 for t, cs in columns.items() if not _is_auto(tables, t) for c in cs if c.expression),
            "measures": len(measures), "relationships": len(relationships), "data_sources": len(sources),
            "unused_measures": sum(1 for m in measure_rows if m["unused"]),
            "issues": sum(1 for i in insights if i["severity"] in ("high", "medium")),
        },
        "data_sources": list(sources.values()),
        "tables": sorted(table_rows, key=lambda t: (t["kind"] == "auto_date", t["name"].lower())),
        "measures": sorted(measure_rows, key=lambda m: (m["folder"] or "", m["name"].lower())),
        "relationships": relationship_rows,
        "pages": page_list,
        "insights": insights,
    }


def _is_auto(tables, table_id: str) -> bool:
    return any(t.id == table_id and t.kind == "auto_date" for t in tables)


def _type_family(data_type: str) -> str:
    t = (data_type or "").lower()
    if t in ("int64", "integer", "double", "decimal", "currency", "number", "int", "float"):
        return "number"
    if "date" in t or "time" in t:
        return "date"
    return t or "string"


def _insights(tables, measure_rows, table_rows, relationships, relationship_rows, unresolved, broken_by_field,
              visual_count, unused_columns, hidden_unused, page_list, sources) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(severity: str, title: str, detail: str, items: Optional[list[str]] = None, category_: str = "general") -> None:
        out.append({"severity": severity, "title": title, "detail": detail, "items": (items or [])[:200],
                    "count": len(items or []), "category": category_})

    if broken_by_field:
        affected = {v for refs in broken_by_field.values() for v in refs}
        items = []
        for field_id, refs in sorted(broken_by_field.items(), key=lambda kv: -len(kv[1])):
            scope = (f"every visual ({len(refs)}) — likely a page/report-level filter" if len(refs) == visual_count and visual_count > 1
                     else f"{len(refs)} visual(s): {', '.join(refs[:3])}{' …' if len(refs) > 3 else ''}")
            items.append(f"{field_id} — used by {scope}")
        add("high", f"{len(broken_by_field)} missing field(s) referenced by {len(affected)} visual(s)",
            "These visuals or filters reference a column, measure or table that has been renamed or removed from the model. "
            "In Power BI they show as broken ('Fix this') or silently stop filtering.", items, "broken")
    broken_rels = [f"{r['from']} → {r['to']}: missing {', '.join(r['missing'])}" for r in relationship_rows if r["missing"]]
    if broken_rels:
        add("high", f"{len(broken_rels)} relationship(s) use a column that isn't in the model",
            "The key column was renamed or removed, so the relationship can't filter. Re-create it on the right column.",
            broken_rels, "modelling")
    mismatched = [f"{r['from']} ({r['from_type']}) → {r['to']} ({r['to_type']})" for r in relationship_rows if r["type_mismatch"]]
    if mismatched:
        add("medium", f"{len(mismatched)} relationship(s) join columns of different data types",
            "Keys of different types (e.g. text vs whole number) match poorly or not at all, so rows fall into (Blank). "
            "Give both key columns the same type in Power Query.", mismatched, "modelling")
    if unresolved:
        items = [f"{obj}: {', '.join(refs)}" for obj, refs in unresolved.items()]
        add("high", f"{len(unresolved)} measure/column formula(s) reference unknown fields",
            "The DAX refers to a column or measure name that isn't in the model (renamed, removed, or a typo). "
            "Some may be variables or report-level fields — check each one.", items, "broken")

    auto = [t.name for t in tables if t.kind == "auto_date"]
    if auto:
        add("medium", f"Auto date/time is on ({len(auto)} hidden date tables)",
            "Power BI creates a hidden date table for every date column. They bloat the model and make time "
            "intelligence inconsistent. Use one marked Date table and turn off File › Options › Auto date/time.",
            [], "performance")
    both = [f"{r.from_column_id} ↔ {r.to_column_id}" for r in relationships if r.cross_filter_direction == "both"]
    if both:
        add("medium", f"{len(both)} bi-directional relationship(s)",
            "Two-way filtering can cause ambiguous paths and unexpected totals. Keep single direction unless required.", both, "modelling")
    m2m = [f"{r.from_column_id} → {r.to_column_id}" for r in relationships if r.cardinality == "many-to-many"]
    if m2m:
        add("medium", f"{len(m2m)} many-to-many relationship(s)", "Many-to-many joins can double count; confirm the grain.", m2m, "modelling")
    inactive = [f"{r.from_column_id} → {r.to_column_id}" for r in relationships if not r.is_active]
    if inactive:
        add("info", f"{len(inactive)} inactive relationship(s)", "Only used where a measure calls USERELATIONSHIP.", inactive, "modelling")

    isolated = [t["name"] for t in table_rows if t["kind"] in ("import", "calculated") and not t["has_relationships"] and t["used"]]
    if isolated:
        add("medium", f"{len(isolated)} table(s) with no relationships",
            "Visuals combining these with other tables won't filter correctly unless measures handle it explicitly.", isolated, "modelling")

    unused_m = [m["name"] for m in measure_rows if m["unused"] and m["folder"] != "Report-level measures"]
    if unused_m:
        add("low", f"{len(unused_m)} measure(s) not used by any visual in this report",
            "Candidates for clean-up — but check other reports built on the same dataset before deleting.", unused_m, "cleanup")
    if unused_columns:
        add("low", f"{len(unused_columns)} visible column(s) not used anywhere",
            "Not in any visual, filter, measure, calculated column or relationship. Hiding or removing them simplifies the field list and model size.",
            unused_columns, "cleanup")
    if hidden_unused:
        add("info", f"{len(hidden_unused)} hidden column(s) not used anywhere",
            "Removing them in Power Query reduces model size and refresh time.", hidden_unused, "performance")

    hardcoded = [f"{m['name']}: {', '.join(sorted(set(_HARDCODED_YEAR.findall(m['expression'] or ''))))}"
                 for m in measure_rows if _HARDCODED_YEAR.search(m["expression"] or "")]
    if hardcoded:
        add("medium", f"{len(hardcoded)} measure(s) with hard-coded years",
            "Literal years go stale when the calendar rolls over. Prefer relative logic (e.g. TODAY(), MAX(Date)) or a parameter.",
            hardcoded, "maintainability")
    complex_m = [m["name"] for m in measure_rows if len(m["expression"] or "") > 1500 or (m["expression"] or "").upper().count("CALCULATE(") > 6]
    if complex_m:
        add("low", f"{len(complex_m)} very complex measure(s)", "Long measures are hard to verify; consider splitting into base measures.", complex_m, "maintainability")
    iferror = [m["name"] for m in measure_rows if "IFERROR(" in (m["expression"] or "").upper()]
    if iferror:
        add("low", f"{len(iferror)} measure(s) use IFERROR", "IFERROR is slow and can hide real errors; DIVIDE or explicit checks are safer.", iferror, "performance")
    no_desc = [m["name"] for m in measure_rows if not m["description"]]
    if measure_rows and len(no_desc) == len(measure_rows):
        add("info", "No measures have descriptions", "Descriptions appear as tooltips in the field list and help report users.", [], "documentation")
    elif no_desc:
        add("info", f"{len(no_desc)} measure(s) without a description", "Descriptions appear as tooltips in the field list.", no_desc, "documentation")

    entered = [t["name"] for t in table_rows if t["kind"] == "entered_data"]
    if entered:
        add("info", f"{len(entered)} table(s) typed directly into Power BI",
            "'Enter data' tables live only in this file — changes need a new publish and aren't governed by the source system.", entered, "governance")
    calc_cols = sum(t["calculated_columns"] for t in table_rows if t["kind"] not in ("auto_date",))
    if calc_cols > 20:
        add("info", f"{calc_cols} calculated columns", "Calculated columns are stored in memory; many can often be moved to Power Query or the source.", [], "performance")
    params = sorted({s["server"] for s in sources.values() if s.get("server") and str(s["server"]).startswith("‹")})
    if params:
        add("info", "Server/database come from Power Query parameters",
            "Good practice for Dev/Test/Prod switching. The actual values are set in the Power BI service.", params, "governance")
    hidden_pages = [p["name"] for p in page_list if p.get("hidden")]
    if hidden_pages:
        add("info", f"{len(hidden_pages)} hidden page(s)", "Often tooltip or drill-through pages — or forgotten ones.", hidden_pages, "report")
    return sorted(out, key=lambda i: _SEVERITY_ORDER[i["severity"]])
