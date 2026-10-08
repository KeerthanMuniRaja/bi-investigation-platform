"""
Power BI file connector: understand a report from the file alone.

Give it a .pbix (or .pbit template, .bim model, or a zipped/unzipped PBIP
project) and it reads, with no Power BI service, credentials or exports:

  - the semantic model: tables, columns (incl. hidden & calculated),
    measures with their DAX, calculated tables, relationships,
  - every table's Power Query (M): the real source system (server,
    database, schema.table, file, SharePoint, ...) and the steps applied,
  - the report: pages, every visual, the fields it shows and the filters
    applied at visual / page / report level (both the legacy Report/Layout
    format and the newer PBIR format are supported),
  - for .pbix, the data stored in the model (row counts, table contents).

From that the platform builds the lineage graph visual -> measure ->
column -> table -> Power Query source, so lineage, impact analysis and the
model health review work straight from the file.
"""
from __future__ import annotations

import json
import re
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.connectors.base import (
    BIConnector,
    CapabilityError,
    Column,
    ConnectorCapabilities,
    Measure,
    Page,
    QueryResult,
    Relationship,
    Report,
    SemanticModel,
    Table,
    Visual,
    Workspace,
    make_relationship,
)
from app.connectors.pbip import (
    FieldRef,
    FilterCondition,
    PbipProject,
    VisualDef,
    _field_ref,
    column_id,
    filter_conditions,
    filter_fields,
    measure_id,
    parse_visual_json,
)
from app.engine.dax import extract_references
from app.engine.powerquery import parse_m
from app.models.graph import CapabilityStatus
from app.models.snapshot import FilterContext

MODEL_FILE_EXTENSIONS = {".pbix", ".pbit", ".bim", ".zip"}
MAX_PBIP_UNZIPPED = 2 * 1024 * 1024 * 1024
WORKSPACE_ID = "file"
REPORT_ID = "report"
MODEL_ID = "model"

_DATA_TYPES = {2: "string", 6: "int64", 8: "double", 9: "dateTime", 10: "decimal", 11: "boolean", 17: "binary", 19: "variant"}
_AUTO_DATE = re.compile(r"^(LocalDateTable_|DateTableTemplate_)")


# ------------------------------------------------------------------ model
@dataclass
class FilterSet:
    """One filter pane (visual, page or report): the fields it uses (for
    lineage) and what it selects (for pulling the same numbers)."""
    fields: list[FieldRef] = field(default_factory=list)
    conditions: list[FilterCondition] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, raw: list, aliases: Optional[dict[str, str]] = None) -> "FilterSet":
        conditions, notes = filter_conditions(raw, aliases)
        return cls(fields=filter_fields(raw, aliases), conditions=conditions, notes=notes)


@dataclass
class LoadedPage:
    id: str
    name: str
    visuals: list[VisualDef] = field(default_factory=list)
    filters: FilterSet = field(default_factory=FilterSet)
    hidden: bool = False
    other_visuals: int = 0  # shapes, text boxes, images, buttons


@dataclass
class LoadedModel:
    name: str
    file_format: str
    tables: list[Table] = field(default_factory=list)
    columns: dict[str, list[Column]] = field(default_factory=dict)
    measures: list[Measure] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    pages: list[LoadedPage] = field(default_factory=list)
    report_filters: FilterSet = field(default_factory=FilterSet)
    row_counts: dict[str, int] = field(default_factory=dict)
    unresolved: dict[str, list[str]] = field(default_factory=dict)  # object -> DAX refs that don't resolve
    warnings: list[str] = field(default_factory=list)
    has_data: bool = False


@dataclass
class _RawTable:
    name: str
    hidden: bool = False
    m_expression: Optional[str] = None
    dax_expression: Optional[str] = None
    description: Optional[str] = None


@dataclass
class _RawColumn:
    table: str
    name: str
    data_type: str = "string"
    hidden: bool = False
    expression: Optional[str] = None


@dataclass
class _RawMeasure:
    table: str
    name: str
    expression: str
    folder: Optional[str] = None
    description: Optional[str] = None


@dataclass
class _Raw:
    tables: list[_RawTable] = field(default_factory=list)
    columns: list[_RawColumn] = field(default_factory=list)
    measures: list[_RawMeasure] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)


def _assemble(name: str, file_format: str, raw: _Raw, pages: list[LoadedPage], report_filters: FilterSet,
              warnings: list[str]) -> LoadedModel:
    model = LoadedModel(name=name, file_format=file_format, pages=pages, report_filters=report_filters,
                        warnings=warnings, row_counts=raw.row_counts)
    table_names = [t.name for t in raw.tables]
    columns_by_table: dict[str, set[str]] = {}
    for c in raw.columns:
        columns_by_table.setdefault(c.table, set()).add(c.name)
    measure_names = [m.name for m in raw.measures]
    measures_by_table: dict[str, int] = {}
    for m in raw.measures:
        measures_by_table[m.table] = measures_by_table.get(m.table, 0) + 1

    for t in raw.tables:
        kind, source_ref, detail, deps_t, deps_c = "import", None, {}, [], []
        visible_cols = [c for c in raw.columns if c.table == t.name and not c.hidden]
        if _AUTO_DATE.match(t.name):
            kind = "auto_date"
        elif t.dax_expression:
            expr = t.dax_expression
            kind = "parameter" if re.search(r"\b(NAMEOF|GENERATESERIES)\s*\(", expr, re.I) else "calculated"
            refs = extract_references(expr, measure_names, columns_by_table)
            deps_t = sorted(refs.tables - {t.name})
            deps_c = sorted(column_id(tb, c) for tb, c in refs.columns if tb != t.name)
            # field parameters switch between measures (NAMEOF([Revenue])): keep that lineage
            deps_c += sorted(measure_id(n) for n in refs.measures)
            detail = {"summary": "calculated table (DAX)"}
        elif t.m_expression:
            info = parse_m(t.m_expression, table_names)
            deps_t = [q for q in info.referenced_queries if q != t.name]
            detail = {
                "summary": info.summary(),
                "sources": [s.__dict__ | {"display": s.display()} for s in info.sources],
                "steps": [s.__dict__ for s in info.steps],
                "entered_data": info.entered_data,
            }
            if info.sources:
                source_ref = info.sources[0].display()
            elif info.entered_data:
                kind = "entered_data"
        if kind in ("import", "entered_data") and measures_by_table.get(t.name) and len(visible_cols) == 0:
            kind = "measures"
        model.tables.append(Table(
            id=t.name, model_id=MODEL_ID, name=t.name, source_ref=source_ref, kind=kind, hidden=t.hidden,
            expression=t.dax_expression or t.m_expression, source_detail=detail,
            depends_on_table_ids=deps_t, depends_on_column_ids=deps_c,
        ))

    for c in raw.columns:
        col = Column(id=column_id(c.table, c.name), table_id=c.table, name=c.name, data_type=c.data_type,
                     hidden=c.hidden, expression=c.expression)
        if c.expression:
            refs = extract_references(c.expression, measure_names, columns_by_table, home_table=c.table)
            col.depends_on_column_ids = sorted(column_id(tb, n) for tb, n in refs.columns if (tb, n) != (c.table, c.name))
            col.depends_on_measure_ids = sorted(measure_id(n) for n in refs.measures)
            if refs.unresolved:
                model.unresolved[col.id] = sorted(refs.unresolved)
        model.columns.setdefault(c.table, []).append(col)

    for m in raw.measures:
        refs = extract_references(m.expression, measure_names, columns_by_table, home_table=m.table)
        mid = measure_id(m.name)
        model.measures.append(Measure(
            id=mid, model_id=MODEL_ID, name=m.name, dax_expression=m.expression, table_id=m.table,
            depends_on_measure_ids=sorted(measure_id(n) for n in refs.measures if n != m.name),
            depends_on_column_ids=sorted(column_id(tb, n) for tb, n in refs.columns),
            depends_on_table_ids=sorted(refs.tables),
            display_folder=m.folder, description=m.description, dependency_source="parsed",
        ))
        if refs.unresolved:
            model.unresolved[mid] = sorted(refs.unresolved)
    model.relationships = raw.relationships
    return model


# ----------------------------------------------------------------- report
def _pbir_report(z: zipfile.ZipFile, prefix: str = "Report/definition/") -> tuple[list[LoadedPage], FilterSet]:
    names = z.namelist()
    order: list[str] = []
    if f"{prefix}pages/pages.json" in names:
        order = json.loads(z.read(f"{prefix}pages/pages.json")).get("pageOrder", [])
    report_filters = FilterSet()
    if f"{prefix}report.json" in names:
        report_filters = FilterSet.parse(json.loads(z.read(f"{prefix}report.json")).get("filterConfig", {}).get("filters", []))
    pages: dict[str, LoadedPage] = {}
    for n in names:
        m = re.match(rf"{re.escape(prefix)}pages/([^/]+)/page\.json$", n)
        if m:
            meta = json.loads(z.read(n))
            pages[m.group(1)] = LoadedPage(
                id=m.group(1), name=meta.get("displayName", m.group(1)),
                filters=FilterSet.parse(meta.get("filterConfig", {}).get("filters", [])),
                hidden=meta.get("visibility") == "HiddenInViewMode",
            )
    for n in names:
        m = re.match(rf"{re.escape(prefix)}pages/([^/]+)/visuals/([^/]+)/visual\.json$", n)
        if not m or m.group(1) not in pages:
            continue
        raw = json.loads(z.read(n))
        visual = parse_visual_json(m.group(2), raw)
        if visual.measures or visual.group_by:
            pages[m.group(1)].visuals.append(visual)
        else:
            pages[m.group(1)].other_visuals += 1
    ordered = sorted(pages.values(), key=lambda p: order.index(p.id) if p.id in order else len(order))
    return ordered, report_filters


def _legacy_title(single: dict) -> Optional[str]:
    try:
        title = single["vcObjects"]["title"][0]["properties"]
        text = title["text"]["expr"]["Literal"]["Value"]
        return text.strip("'")
    except (KeyError, IndexError, TypeError):
        return None


def _legacy_report(z: zipfile.ZipFile) -> tuple[list[LoadedPage], FilterSet]:
    return _legacy_layout(json.loads(z.read("Report/Layout").decode("utf-16-le")))


def _legacy_layout(layout: dict) -> tuple[list[LoadedPage], FilterSet]:

    def filters_of(obj: dict) -> FilterSet:
        raw = obj.get("filters")
        if not raw:
            return FilterSet()
        try:
            return FilterSet.parse(json.loads(raw) if isinstance(raw, str) else raw)
        except json.JSONDecodeError:
            return FilterSet()

    pages = []
    for section in layout.get("sections", []):
        page = LoadedPage(id=section.get("name", ""), name=section.get("displayName", section.get("name", "")),
                          filters=filters_of(section), hidden=section.get("visibility") == 1)
        for vc in section.get("visualContainers", []):
            try:
                config = json.loads(vc.get("config", "{}"))
            except json.JSONDecodeError:
                continue
            single = config.get("singleVisual")
            query = (single or {}).get("prototypeQuery") or {}
            if not single or not query.get("Select"):
                page.other_visuals += 1
                continue
            aliases = {f.get("Name"): f.get("Entity") for f in query.get("From", [])}
            visual = VisualDef(id=config.get("name", ""), title="", visual_type=single.get("visualType", "unknown"))
            for select in query.get("Select", []):
                ref = _field_ref(select, aliases)
                if ref is None:
                    continue
                (visual.measures if ref.kind in ("measure", "aggregation") else visual.group_by).append(ref)
            own = filters_of(vc)
            visual.filters, visual.conditions, visual.filter_notes = own.fields, own.conditions, own.notes
            visual.title = _legacy_title(single) or ""
            page.visuals.append(visual)
        pages.append(page)
    return pages, filters_of(layout)


def _finish_titles(pages: list[LoadedPage]) -> None:
    for page in pages:
        for v in page.visuals:
            if not v.title or v.title == v.id:
                fields = [f.output_name() for f in (v.measures or v.group_by)][:3]
                v.title = f"{v.visual_type}: {', '.join(fields)}" if fields else v.visual_type


def _report_from_zip(z: zipfile.ZipFile, warnings: list[str]) -> tuple[list[LoadedPage], FilterSet]:
    names = set(z.namelist())
    try:
        if "Report/Layout" in names:
            pages, filters = _legacy_report(z)
        elif any(n.startswith("Report/definition/pages/") for n in names):
            pages, filters = _pbir_report(z)
        else:
            warnings.append("No report layout found in the file — only the semantic model was read.")
            return [], FilterSet()
    except Exception as e:  # noqa: BLE001 — a broken layout shouldn't hide the model
        warnings.append(f"The report layout could not be fully read ({e}); the semantic model is still available.")
        return [], FilterSet()
    _finish_titles(pages)
    return pages, filters


# ---------------------------------------------------------------- loaders
def _open_pbix(path: Path):
    from pbixray import PBIXRay

    # on_disk: the decompressed model goes to a temp file, not memory — a 40 MB
    # .pbix peaks at ~125 MB instead of ~400 MB (matters on 512 MB servers)
    return PBIXRay(str(path), on_disk=True)


def _load_pbix(path: Path) -> tuple[LoadedModel, Any]:
    try:
        from pbixray import PBIXRay
    except ImportError as e:  # pragma: no cover
        raise CapabilityError("Reading .pbix files needs the 'pbixray' package: pip install pbixray") from e
    warnings: list[str] = []
    try:
        px = _open_pbix(path)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Could not read the semantic model inside this .pbix ({e}). If the report uses a live "
                         "connection to a published dataset, the model isn't inside the file — upload that dataset's "
                         ".pbix, or connect to Power BI live.") from e
    raw = _Raw()
    m_by_table = {r.TableName: r.Expression for r in px.power_query.itertuples()} if len(px.power_query) else {}
    dax_by_table = {r.TableName: r.Expression for r in px.dax_tables.itertuples()} if len(px.dax_tables) else {}
    hidden_tables: set[str] = set()
    table_names: list[str] = list(px.tables)
    try:
        tt = px.tmschema_tables
        hidden_tables = {r.Name for r in tt.itertuples() if bool(r.IsHidden)}
        table_names += list(tt.Name)
    except Exception:  # noqa: BLE001
        pass
    table_names += list(px.dax_measures.TableName) + list(dax_by_table) + list(m_by_table)
    for name in dict.fromkeys(n for n in table_names if isinstance(n, str)):
        raw.tables.append(_RawTable(name=name, hidden=name in hidden_tables, m_expression=m_by_table.get(name),
                                    dax_expression=dax_by_table.get(name)))

    # Columns: pbixray's views each cover part of the model, so merge them.
    detail: dict[tuple[str, str], Any] = {}
    try:
        for r in px.tmschema_columns.itertuples():
            if r.Type != 3 and not str(r.Name).startswith("RowNumber-"):
                detail[(r.TableName, r.Name)] = r
    except Exception:  # noqa: BLE001
        pass
    calc = {(r.TableName, r.ColumnName): r.Expression for r in px.dax_columns.itertuples()}
    seen: set[tuple[str, str]] = set()
    for r in px.schema.itertuples():
        key = (r.TableName, r.ColumnName)
        if str(r.ColumnName).startswith("RowNumber-") or key in seen:
            continue
        seen.add(key)
        d = detail.get(key)
        expr = calc.get(key) or (d.Expression if d is not None and isinstance(d.Expression, str) else None)
        raw.columns.append(_RawColumn(
            table=r.TableName, name=r.ColumnName,
            data_type=_DATA_TYPES.get(int(d.DataType or 0), "string") if d is not None else _pandas_type(str(r.PandasDataType)),
            hidden=bool(d.IsHidden) if d is not None else False,
            expression=expr if isinstance(expr, str) and expr.strip() else None,
        ))
    for key, d in detail.items():
        if key not in seen:
            seen.add(key)
            raw.columns.append(_RawColumn(table=key[0], name=key[1], data_type=_DATA_TYPES.get(int(d.DataType or 0), "string"),
                                          hidden=bool(d.IsHidden), expression=d.Expression if isinstance(d.Expression, str) and d.Expression.strip() else None))
    for key, expr in calc.items():
        if key not in seen and isinstance(expr, str):
            raw.columns.append(_RawColumn(table=key[0], name=key[1], expression=expr))
    for r in px.dax_measures.itertuples():
        raw.measures.append(_RawMeasure(table=r.TableName, name=r.Name, expression=r.Expression if isinstance(r.Expression, str) else "",
                                        folder=r.DisplayFolder if isinstance(r.DisplayFolder, str) else None,
                                        description=r.Description if isinstance(r.Description, str) else None))
    raw.relationships.extend(_pbix_relationships(px, warnings))
    try:
        stats = px.statistics
        for r in stats.itertuples():
            if str(r.ColumnName).startswith("RowNumber-"):
                raw.row_counts[r.TableName] = int(r.Cardinality)
    except Exception:  # noqa: BLE001
        pass
    with zipfile.ZipFile(path) as z:
        pages, report_filters = _report_from_zip(z, warnings)
        _add_report_measures(raw, _report_measures(z))
    model = _assemble(path.stem, "pbix", raw, pages, report_filters, warnings)
    model.has_data = True
    return model, px


_AUTO_DATE_PREFIXES = ("LocalDateTable_", "DateTableTemplate_")


def _pbix_relationships(px: Any, warnings: list[str]) -> list[Relationship]:
    """Relationships read straight from the model metadata.

    pbixray's own `relationships` view drops every relationship touching a
    table with SystemFlags != 0 — which includes calculated tables (a DAX
    calendar, a region lookup built with DISTINCT, field parameters…) — and
    names columns by ExplicitName only, while calculated-table columns keep
    their name in InferredName. Both silently lose most of a typical model's
    relationships, so query the Relationship table directly and fall back to
    pbixray's view only if the metadata isn't the SQLite kind.
    Relationships to Power BI's hidden auto date tables are left out, as the
    Power BI model view does."""
    rows: list[tuple] = []
    try:
        db = px._metadata.source._db
        cols = set(db.query("PRAGMA table_info([Relationship])")["name"])
        # legacy schemas spell the endpoints FromEndColumnID etc.
        rc = (lambda n: n) if "FromColumnID" in cols else (lambda n: n.replace("From", "FromEnd", 1).replace("To", "ToEnd", 1))
        df = db.query(f"""
            SELECT ft.Name AS ft, COALESCE(fc.ExplicitName, fc.InferredName) AS fcol,
                   tt.Name AS tt, COALESCE(tc.ExplicitName, tc.InferredName) AS tcol,
                   rel.IsActive AS active, rel.{rc('FromCardinality')} AS fcard, rel.{rc('ToCardinality')} AS tcard,
                   rel.CrossFilteringBehavior AS cross
            FROM Relationship rel
                LEFT JOIN [Table] ft ON rel.{rc('FromTableID')} = ft.ID
                LEFT JOIN [Column] fc ON rel.{rc('FromColumnID')} = fc.ID
                LEFT JOIN [Table] tt ON rel.{rc('ToTableID')} = tt.ID
                LEFT JOIN [Column] tc ON rel.{rc('ToColumnID')} = tc.ID
            ORDER BY rel.ID""")
        # CrossFilteringBehavior: 1 single, 2 both, 3 automatic (= single for a plain relationship)
        rows = [(r.ft, r.fcol, r.tt, r.tcol, dict(from_cardinality=r.fcard, to_cardinality=r.tcard,
                                                   cross_filter="both" if r.cross == 2 else "single", is_active=r.active))
                for r in df.itertuples()]
    except Exception:  # noqa: BLE001 - older XML metadata, or a pbixray without this internal
        rows = [(r.FromTableName, r.FromColumnName, r.ToTableName, r.ToColumnName,
                 dict(cardinality=r.Cardinality, cross_filter=r.CrossFilteringBehavior, is_active=r.IsActive))
                for r in px.relationships.itertuples()]
    out: list[Relationship] = []
    skipped = 0
    for ft, fcol, tt, tcol, kw in rows:
        if any(isinstance(t, str) and t.startswith(_AUTO_DATE_PREFIXES) for t in (ft, tt)):
            continue
        if not all(isinstance(x, str) and x for x in (ft, fcol, tt, tcol)):
            skipped += 1
            continue
        out.append(make_relationship(f"REL{len(out) + 1:03d}", MODEL_ID, ft, fcol, tt, tcol, **kw))
    if skipped:
        warnings.append(f"{skipped} relationship(s) point at a table or column that no longer exists — skipped.")
    return out


def _pandas_type(t: str) -> str:
    return {"datetime64[ns]": "dateTime", "Int64": "int64", "int64": "int64", "float64": "double", "boolean": "boolean",
            "bool": "boolean"}.get(t, "string")


def _report_measures(z: zipfile.ZipFile) -> list[_RawMeasure]:
    """Measures defined in the report itself (thin reports over a published
    dataset keep them in reportExtensions / modelExtensions)."""
    out: list[_RawMeasure] = []
    names = set(z.namelist())
    try:
        if "Report/definition/reportExtensions.json" in names:
            ext = json.loads(z.read("Report/definition/reportExtensions.json"))
            for entity in ext.get("entities", []):
                for m in entity.get("measures", []):
                    out.append(_RawMeasure(table=entity.get("name", ""), name=m["name"], expression=m.get("expression", ""),
                                           folder="Report-level measures"))
        elif "Report/Layout" in names:
            layout = json.loads(z.read("Report/Layout").decode("utf-16-le"))
            config = json.loads(layout.get("config", "{}"))
            for ext in config.get("modelExtensions", []):
                for entity in ext.get("entities", []):
                    for m in entity.get("measures", []):
                        out.append(_RawMeasure(table=entity.get("name", ""), name=m["name"], expression=m.get("expression", ""),
                                               folder="Report-level measures"))
    except (json.JSONDecodeError, KeyError):
        pass
    return out


def _schema_json_to_raw(schema: dict) -> _Raw:
    raw = _Raw()
    model = schema.get("model", schema)
    for t in model.get("tables", []):
        m_expr = dax_expr = None
        for part in t.get("partitions", []):
            src = part.get("source", {})
            expr = src.get("expression")
            expr = "\n".join(expr) if isinstance(expr, list) else expr
            if src.get("type") == "calculated":
                dax_expr = expr
            elif expr:
                m_expr = expr
        raw.tables.append(_RawTable(name=t["name"], hidden=bool(t.get("isHidden")), m_expression=m_expr,
                                    dax_expression=dax_expr, description=t.get("description")))
        for c in t.get("columns", []):
            if c.get("type") == "rowNumber":
                continue
            expr = c.get("expression")
            raw.columns.append(_RawColumn(table=t["name"], name=c["name"], data_type=c.get("dataType", "string"),
                                          hidden=bool(c.get("isHidden")),
                                          expression="\n".join(expr) if isinstance(expr, list) else expr))
        for m in t.get("measures", []):
            expr = m.get("expression", "")
            raw.measures.append(_RawMeasure(table=t["name"], name=m["name"],
                                            expression="\n".join(expr) if isinstance(expr, list) else expr,
                                            folder=m.get("displayFolder"), description=m.get("description")))
    for i, r in enumerate(model.get("relationships", [])):
        if not all(r.get(k) for k in ("fromTable", "fromColumn", "toTable", "toColumn")):
            continue
        raw.relationships.append(make_relationship(
            r.get("name", f"REL{i + 1:03d}"), MODEL_ID, r["fromTable"], r["fromColumn"], r["toTable"], r["toColumn"],
            from_cardinality=r.get("fromCardinality", "many"), to_cardinality=r.get("toCardinality", "one"),
            cross_filter=r.get("crossFilteringBehavior"), is_active=r.get("isActive", True),
        ))
    return raw


def _add_report_measures(raw: _Raw, measures: list[_RawMeasure]) -> None:
    existing = {m.name for m in raw.measures}
    tables = {t.name for t in raw.tables}
    for m in measures:
        if m.name not in existing:
            raw.measures.append(m)
            if m.table and m.table not in tables:
                raw.tables.append(_RawTable(name=m.table))
                tables.add(m.table)


def _load_pbit(path: Path) -> LoadedModel:
    warnings: list[str] = []
    with zipfile.ZipFile(path) as z:
        if "DataModelSchema" not in z.namelist():
            raise ValueError("This .pbit has no DataModelSchema — it may use a live connection to a published dataset.")
        schema = json.loads(z.read("DataModelSchema").decode("utf-16-le"))
        pages, filters = _report_from_zip(z, warnings)
        raw = _schema_json_to_raw(schema)
        _add_report_measures(raw, _report_measures(z))
    return _assemble(path.stem, "pbit", raw, pages, filters, warnings)


def _load_bim(path: Path) -> LoadedModel:
    text = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            schema = json.loads(text.decode(enc))
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    else:
        raise ValueError("Could not read the .bim file as JSON.")
    return _assemble(path.stem, "bim", _schema_json_to_raw(schema), [], FilterSet(),
                     ["A .bim file holds the model only — no report pages or visuals."])


def _load_pbip_dir(root: Path, name: str) -> LoadedModel:
    """PBIP project folder: TMDL (definition/tables/*.tmdl) or TMSL
    (model.bim) semantic model, and PBIR (definition/pages) or legacy
    (report.json) report."""
    project = PbipProject(root)
    warnings: list[str] = []
    if not project.model_dirs:
        raise ValueError("No *.SemanticModel folder found in the PBIP project.")
    model_dir = project.model_dirs[0]
    bim = model_dir / "model.bim"
    if bim.exists() and not (model_dir / "definition" / "tables").exists():
        raw = _schema_json_to_raw(json.loads(bim.read_text(encoding="utf-8-sig")))
    else:
        tables, rels = project.model(model_dir)
        raw = _Raw()
        for t in tables:
            raw.tables.append(_RawTable(name=t.name, m_expression=t.source if t.source and t.source != "power_query" else None))
            for c in t.columns:
                raw.columns.append(_RawColumn(table=t.name, name=c.name, data_type=c.data_type, expression=c.expression))
            for m in t.measures:
                raw.measures.append(_RawMeasure(table=t.name, name=m.name, expression=m.expression))
        raw.relationships = [r.to_relationship(MODEL_ID) for r in rels]
    pages: list[LoadedPage] = []
    report_filters = FilterSet()
    if project.report_dirs:
        rdir = project.report_dirs[0]
        legacy = rdir / "report.json"
        if (rdir / "definition" / "pages").exists():
            for page_id, page_name in project.pages(rdir):
                page = LoadedPage(id=page_id, name=page_name)
                for v in project.visuals(rdir, page_id):
                    if v.measures or v.group_by:
                        page.visuals.append(v)
                    else:
                        page.other_visuals += 1
                pages.append(page)
        elif legacy.exists():
            pages, report_filters = _legacy_layout(json.loads(legacy.read_text(encoding="utf-8-sig")))
        _finish_titles(pages)
    else:
        warnings.append("No *.Report folder found — only the semantic model was read.")
    return _assemble(name, "pbip", raw, pages, report_filters, warnings)


def load_model_file(path: Path, extract_dir: Optional[Path] = None) -> tuple[LoadedModel, Any]:
    path = Path(path)
    if path.is_dir():
        return _load_pbip_dir(path, path.name), None
    ext = path.suffix.lower()
    if ext == ".pbix":
        return _load_pbix(path)
    if ext == ".pbit":
        return _load_pbit(path), None
    if ext == ".bim":
        return _load_bim(path), None
    if ext == ".zip":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if not any(".SemanticModel/" in n for n in names):
                raise ValueError("This zip doesn't contain a PBIP project (no *.SemanticModel folder).")
            if sum(i.file_size for i in z.infolist()) > MAX_PBIP_UNZIPPED:
                raise ValueError("This zip expands to more than 2 GB — that isn't a PBIP project.")
            target = extract_dir or path.with_suffix("")
            target.mkdir(parents=True, exist_ok=True)
            root = target.resolve()
            for member in names:
                if not (target / member).resolve().is_relative_to(root):
                    raise ValueError("Unsafe path in zip file.")
            z.extractall(target)
        roots = {p.parent for p in target.rglob("*.SemanticModel")}
        return _load_pbip_dir(sorted(roots)[0], path.stem), None
    raise ValueError(f"Unsupported model file '{ext}'. Upload a .pbix, .pbit, .bim or a zipped PBIP project.")


# --------------------------------------------------------------- snapshots
# What was read from a file, saved as JSON: the model, the report and the
# first rows of each table. Once saved, the app works from it — the original
# file isn't needed again (except to compute a visual's values from all its
# rows), so it doesn't have to be kept.
SNAPSHOT_VERSION = 1
FILE_NOT_KEPT = ("This needs every row of the data, which only the original .pbix has — and it isn't kept (to save storage). "
                 "Upload the file again to use this.")
PREVIEW_ROWS = 50
PREVIEW_MAX_DICTIONARY_BYTES = 150 * 1024 * 1024  # a table's decoded dictionaries above this: no preview (memory)
PREVIEW_TIME_BUDGET = 45.0          # seconds for all previews together (serverless time limits)
PREVIEW_MEMORY_SHARE = 0.75         # previews stop before the process passes this share of its memory limit


def _preview_cost(data_size: int, dictionary: int) -> int:
    """Memory a table's preview briefly needs: pbixray decodes a whole
    segment and every dictionary string (measured: ~6x the stored data plus
    ~10x the dictionaries)."""
    return 6 * data_size + 10 * dictionary


def memory_limit() -> Optional[int]:
    """The server's memory limit in bytes: BIP_MEMORY_LIMIT_MB, else the
    container's (cgroup) limit, else None (no limit known: a laptop)."""
    import os

    mb = os.environ.get("BIP_MEMORY_LIMIT_MB", "").strip()
    if mb:
        return int(float(mb) * 1024 * 1024)
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text().strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < 1 << 50:  # "max" / a huge number mean no limit
            return int(raw)
    return None


def memory_used() -> Optional[int]:
    """This process's resident memory in bytes (None if it can't be read)."""
    import os

    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, AttributeError):
        pass
    try:
        import psutil

        return psutil.Process().memory_info().rss
    except Exception:  # noqa: BLE001
        return None
PREVIEW_KINDS = ("import", "entered_data", "calculated")


def model_to_snapshot(model: LoadedModel, previews: Optional[dict] = None) -> dict:
    from dataclasses import asdict

    return {
        "version": SNAPSHOT_VERSION, "name": model.name, "file_format": model.file_format,
        "tables": [t.model_dump(mode="json") for t in model.tables],
        "columns": {k: [c.model_dump(mode="json") for c in v] for k, v in model.columns.items()},
        "measures": [m.model_dump(mode="json") for m in model.measures],
        "relationships": [r.model_dump(mode="json") for r in model.relationships],
        "pages": [asdict(p) for p in model.pages],
        "report_filters": asdict(model.report_filters),
        "row_counts": model.row_counts, "unresolved": model.unresolved, "warnings": model.warnings,
        "previews": previews or {},
    }


def model_from_snapshot(data: dict) -> LoadedModel:
    def ref(d: dict) -> FieldRef:
        return FieldRef(**d)

    def cond(d: dict) -> FilterCondition:
        return FilterCondition(field=ref(d["field"]), operator=d["operator"], values=d.get("values", []))

    def fset(d: dict) -> FilterSet:
        return FilterSet(fields=[ref(x) for x in d.get("fields", [])], conditions=[cond(x) for x in d.get("conditions", [])], notes=d.get("notes", []))

    def visual(d: dict) -> VisualDef:
        return VisualDef(id=d["id"], title=d["title"], visual_type=d["visual_type"], measures=[ref(x) for x in d.get("measures", [])],
                         group_by=[ref(x) for x in d.get("group_by", [])], filters=[ref(x) for x in d.get("filters", [])],
                         conditions=[cond(x) for x in d.get("conditions", [])], filter_notes=d.get("filter_notes", []))

    return LoadedModel(
        name=data["name"], file_format=data["file_format"],
        tables=[Table.model_validate(t) for t in data.get("tables", [])],
        columns={k: [Column.model_validate(c) for c in v] for k, v in data.get("columns", {}).items()},
        measures=[Measure.model_validate(m) for m in data.get("measures", [])],
        relationships=[Relationship.model_validate(r) for r in data.get("relationships", [])],
        pages=[LoadedPage(id=p["id"], name=p["name"], visuals=[visual(v) for v in p.get("visuals", [])], filters=fset(p.get("filters", {})),
                          hidden=p.get("hidden", False), other_visuals=p.get("other_visuals", 0)) for p in data.get("pages", [])],
        report_filters=fset(data.get("report_filters", {})),
        row_counts=data.get("row_counts", {}), unresolved=data.get("unresolved", {}), warnings=data.get("warnings", []),
        has_data=data.get("file_format") == "pbix",
    )


def _frame_rows(frame) -> QueryResult:
    frame = frame.astype(object).where(frame.notna(), None)
    rows = json.loads(json.dumps(frame.values.tolist(), default=str))  # dates, decimals... as JSON-safe values
    return QueryResult(columns=[str(c) for c in frame.columns], rows=rows)


# -------------------------------------------------------------- connector
class ModelFileConnector(BIConnector):
    """A Power BI file. Works from the file itself, or from a saved snapshot
    of it (then the file is only opened — fetched first if need be — to
    compute a visual's values from all its rows)."""
    platform_name = "powerbi"

    def __init__(self, path: Optional[Path], display_name: Optional[str] = None, snapshot: Optional[dict] = None,
                 file_resolver: Optional[Callable[[], Path]] = None):
        self.path = Path(path) if path else None
        self.display_name = display_name or (self.path.stem if self.path else "model")
        self._snapshot = snapshot
        self._file_resolver = file_resolver
        self._model: Optional[LoadedModel] = None
        self._pbix = None
        self._load_lock = threading.Lock()

    @property
    def model(self) -> LoadedModel:
        if self._model is None:
            with self._load_lock:  # concurrent first requests read the file once
                if self._model is None:
                    if self._snapshot is not None:
                        model = model_from_snapshot(self._snapshot)
                    else:
                        model, self._pbix = load_model_file(self.path, self.path.parent / f"{self.path.stem}_pbip")
                    model.name = Path(self.display_name).stem
                    self._model = model
        return self._model

    def snapshot(self, previews: bool = True) -> dict:
        """Everything the app needs later, as JSON (plus the first rows of each table)."""
        return model_to_snapshot(self.model, self._previews() if previews else None)

    def _previews(self) -> dict:
        if self.model.file_format != "pbix":
            return {}
        import time

        import gc

        out: dict = {}
        started = time.monotonic()
        try:
            px = self._file()
        except CapabilityError:
            return {}
        # decoded dictionaries are what costs memory: skip tables whose dictionaries are huge
        dict_bytes: dict[str, int] = {}
        cost: dict[str, int] = {}
        try:
            for r in px.statistics.itertuples():
                dict_bytes[r.TableName] = dict_bytes.get(r.TableName, 0) + int(r.Dictionary or 0)
                cost[r.TableName] = cost.get(r.TableName, 0) + _preview_cost(int(getattr(r, "DataSize", 0) or 0), int(r.Dictionary or 0))
        except Exception:  # noqa: BLE001
            pass
        limit = memory_limit()
        for t in self.model.tables:
            if t.kind not in PREVIEW_KINDS or dict_bytes.get(t.name, 0) > PREVIEW_MAX_DICTIONARY_BYTES:
                continue
            # small servers (Render's 512 MB plans): a preview decodes the table's dictionaries,
            # so skip it when that would take the process near its memory limit
            if limit and (used := memory_used()) is not None and used + cost.get(t.name, 0) > PREVIEW_MEMORY_SHARE * limit:
                continue
            if time.monotonic() - started > PREVIEW_TIME_BUDGET:
                break
            try:
                # only the first rows: reading whole tables can take GBs (serverless memory limits)
                chunks = px.iter_table(t.name, chunk_size=PREVIEW_ROWS, strings_as_categorical=False)
                first = next(iter(chunks), None)
                if first is not None:
                    out[t.name] = _frame_rows(first.head(PREVIEW_ROWS)).model_dump()
                del chunks, first
            except Exception:  # noqa: BLE001 - DirectQuery / not refreshed: no stored rows
                continue
            finally:
                gc.collect()
        return out

    def _file(self):
        """The parsed .pbix, for reading stored table rows."""
        if self._pbix is None:
            with self._load_lock:
                if self._pbix is None:
                    path = self.path
                    if (path is None or not path.exists()) and self._file_resolver is not None:
                        try:
                            path = self._file_resolver()
                        except (FileNotFoundError, KeyError) as e:
                            raise CapabilityError(FILE_NOT_KEPT) from e
                    if path is None or not path.exists():
                        raise CapabilityError(FILE_NOT_KEPT)
                    self._pbix = _open_pbix(path)
        return self._pbix

    def close(self) -> None:
        """Release the parsed file (its temp copy and memory map); the
        snapshot keeps working."""
        with self._load_lock:
            px, self._pbix = self._pbix, None
        if px is not None and hasattr(px, "close"):
            try:
                px.close()
            except Exception:  # noqa: BLE001
                pass

    def capabilities(self) -> ConnectorCapabilities:
        data = CapabilityStatus.PARTIALLY_CAPABLE if self.model.file_format == "pbix" else CapabilityStatus.UNSUPPORTED
        return ConnectorCapabilities(
            metadata_api=CapabilityStatus.NOT_AVAILABLE,
            execute_query=CapabilityStatus.UNSUPPORTED,
            pbip_tmdl=CapabilityStatus.CAPABLE,
            visual_export=CapabilityStatus.CAPABLE,
            visual_data=data,
            notes=["Read from the Power BI file itself: full model, Power Query sources, report pages and visuals. "
                   "Measure values need the Power BI engine; tables stored in a .pbix can be read directly."],
        )

    def get_workspaces(self) -> list[Workspace]:
        return [Workspace(id=WORKSPACE_ID, name=self.display_name)]

    def get_reports(self, workspace_id: str) -> list[Report]:
        return [Report(id=REPORT_ID, name=self.model.name, workspace_id=WORKSPACE_ID, model_id=MODEL_ID)]

    def get_pages(self, report_id: str) -> list[Page]:
        return [Page(id=p.id, report_id=REPORT_ID, name=p.name + (" (hidden)" if p.hidden else "")) for p in self.model.pages]

    def _page(self, page_id: str) -> LoadedPage:
        for p in self.model.pages:
            if p.id == page_id:
                return p
        raise KeyError(f"page {page_id} not found")

    @staticmethod
    def field_id(f: FieldRef) -> str:
        return measure_id(f.property) if f.kind == "measure" else column_id(f.entity, f.property)

    def get_visuals(self, report_id: str, page_id: str) -> list[Visual]:
        page = self._page(page_id)
        report = self.model.report_filters
        shared = [self.field_id(f) for f in page.filters.fields + report.fields]
        shared_notes = [f"Page filter · {n}" for n in page.filters.notes] + [f"Report filter · {n}" for n in report.notes]
        out = []
        for v in page.visuals:
            ids = list(dict.fromkeys(self.field_id(f) for f in v.measures + v.group_by))
            filters = list(dict.fromkeys([self.field_id(f) for f in v.filters] + shared))
            # what the dashboard actually shows: the visual's own selections plus page- and report-level ones
            conditions = v.conditions + page.filters.conditions + report.conditions
            out.append(Visual(
                id=v.id, report_id=REPORT_ID, page_id=page_id, title=v.title, visual_type=v.visual_type,
                measure_ids=ids, filter_ids=[f for f in filters if f not in ids],
                default_filters=[FilterContext(field=c.field.label(), operator=c.operator, values=c.values) for c in conditions],
                filter_notes=[f"Visual filter · {n}" for n in v.filter_notes] + shared_notes,
            ))
        return out

    def get_semantic_model(self, model_id: str) -> SemanticModel:
        return SemanticModel(id=MODEL_ID, name=self.model.name, workspace_id=WORKSPACE_ID)

    def get_tables(self, model_id: str) -> list[Table]:
        return self.model.tables

    def get_columns(self, model_id: str, table_id: str) -> list[Column]:
        return self.model.columns.get(table_id, [])

    def get_measures(self, model_id: str) -> list[Measure]:
        return self.model.measures

    def get_relationships(self, model_id: str) -> list[Relationship]:
        return self.model.relationships

    def execute_query(self, model_id: str, dax_query: str) -> QueryResult:
        raise CapabilityError("DAX queries need the Power BI engine — connect to the published dataset (Power BI live) to run them.")

    def get_table_data(self, table: str, limit: Optional[int] = None) -> QueryResult:
        if self.model.file_format != "pbix":
            raise CapabilityError("Only .pbix files contain data; .pbit/.bim/PBIP files hold the model definition only.")
        saved = (self._snapshot or {}).get("previews", {}).get(table)
        if saved is not None and limit is not None and limit <= PREVIEW_ROWS:
            return QueryResult(columns=saved["columns"], rows=saved["rows"][:max(limit, 0)])
        px = self._file()
        try:
            if limit is None:
                frame = px.get_table(table)
            else:
                # read in chunks and stop at `limit` rows: never materialise a huge table for a preview
                import pandas as pd

                parts, have = [], 0
                for chunk in px.iter_table(table, chunk_size=min(max(limit, 1), 10_000), strings_as_categorical=False):
                    parts.append(chunk)
                    have += len(chunk)
                    if have >= limit:
                        break
                frame = (pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()).head(max(limit, 0))
        except Exception as e:  # noqa: BLE001
            raise CapabilityError(f"Table '{table}' has no data stored in the file (DirectQuery or not yet refreshed?): {e}") from e
        return _frame_rows(frame)

    def get_visual_data(self, report_id: str, page_id: str, visual_id: str, filters: list[FilterContext]) -> QueryResult:
        """Visuals built from columns (plain aggregations such as Sum of
        Amount by Region) are computed from the data stored in the .pbix,
        following the model's relationships the way Power BI does: a fact
        table is grouped and filtered by columns of the dimension tables it
        relates to. Measures need the Power BI engine, so those visuals
        report why they can't be computed."""
        page = self._page(page_id)
        v = next((x for x in page.visuals if x.id == visual_id), None)
        if v is None:
            raise KeyError(f"visual {visual_id} not found")
        measures = [f.property for f in v.measures if f.kind == "measure"]
        if measures:
            raise CapabilityError(
                f"'{v.title}' uses measure(s) {', '.join('[' + m + ']' for m in measures[:3])}. Calculating measures needs the "
                "Power BI engine — connect to the published report (Power BI live) to pull the visual's values."
            )
        if not v.measures and not v.group_by:
            raise CapabilityError(f"'{v.title}' has no data fields to compute.")
        return _compute_visual(_RelatedRows(self, _base_table(v, self.model.relationships)), v, filters)


# ------------------------------------------------------- visual data from file
def _base_table(v: VisualDef, relationships: list[Relationship]) -> str:
    """The table a visual's rows come from: the one its values aggregate,
    or — for a visual of plain columns — the table every other field can be
    reached from (the fact side)."""
    value_tables = list(dict.fromkeys(f.entity for f in v.measures))
    if len(value_tables) > 1:
        raise CapabilityError(f"'{v.title}' aggregates columns from several tables ({', '.join(value_tables)}); "
                              "computing it needs the Power BI engine.")
    if value_tables:
        return value_tables[0]
    tables = list(dict.fromkeys(f.entity for f in v.group_by))
    for candidate in tables:
        if all(_path(candidate, t, relationships) is not None for t in tables):
            return candidate
    return tables[0]


def _path(start: str, target: str, relationships: list[Relationship]) -> Optional[list[Relationship]]:
    """Shortest chain of active relationships from start to target where
    every hop moves to the 'one' side — the direction a row of a fact
    table can look up exactly one row of a dimension. Many-to-many and
    inactive relationships are never followed, just as Power BI doesn't
    without USERELATIONSHIP / explicit DAX."""
    if start == target:
        return []
    hops: dict[str, list[tuple[Relationship, bool]]] = {}
    for r in relationships:
        if not r.is_active or r.cardinality == "many-to-many":
            continue
        hops.setdefault(r.from_table_id, []).append((r, False))
        if r.cardinality == "one-to-one":
            hops.setdefault(r.to_table_id, []).append((r, True))
    frontier: list[tuple[str, list[Relationship]]] = [(start, [])]
    seen = {start}
    while frontier:
        nxt = []
        for table, chain in frontier:
            for r, reverse in hops.get(table, []):
                other = r.from_table_id if reverse else r.to_table_id
                if other in seen:
                    continue
                step = r if not reverse else r.model_copy(update={
                    "from_table_id": r.to_table_id, "from_column_id": r.to_column_id,
                    "to_table_id": r.from_table_id, "to_column_id": r.from_column_id})
                if other == target:
                    return chain + [step]
                seen.add(other)
                nxt.append((other, chain + [step]))
        frontier = nxt
    return None


class _RelatedRows:
    """Rows of one base table, with any column of a related table looked
    up through the model's relationships (many -> one)."""

    def __init__(self, connector: "ModelFileConnector", base: str):
        self.connector = connector
        self.base = base
        self._data: dict[str, tuple[dict[str, int], list[list]]] = {}
        self._lookups: dict[tuple[str, str], dict[str, list]] = {}

    def data(self, table: str) -> tuple[dict[str, int], list[list]]:
        if table not in self._data:
            result = self.connector.get_table_data(table)
            self._data[table] = ({c: i for i, c in enumerate(result.columns)}, result.rows)
        return self._data[table]

    @property
    def rows(self) -> list[list]:
        return self.data(self.base)[1]

    def _index(self, table: str, column: str) -> int:
        idx = self.data(table)[0]
        if column not in idx:
            raise CapabilityError(f"{column_id(table, column)} isn't in the data stored in this file "
                                  "(renamed or removed from the model, or a DirectQuery table).")
        return idx[column]

    def _lookup(self, table: str, column: str) -> dict[str, list]:
        from app.engine.values import normalize_key

        key = (table, column)
        if key not in self._lookups:
            i = self._index(table, column)
            lookup: dict[str, list] = {}
            for row in self.data(table)[1]:
                lookup.setdefault(normalize_key(row[i]), row)
            self._lookups[key] = lookup
        return self._lookups[key]

    def getter(self, table: str, column: str):
        """A function row -> value of table[column] for a base-table row."""
        from app.engine.values import normalize_key

        chain = _path(self.base, table, self.connector.model.relationships)
        if chain is None:
            raise CapabilityError(
                f"No active relationship leads from '{self.base}' to '{table}', so {column_id(table, column)} can't be "
                "used on this visual's data. Check the relationship in the model (it may be inactive, many-to-many "
                "or missing), or remove that field/filter."
            )
        hops = [(self._index(r.from_table_id, r.from_column), self._lookup(r.to_table_id, r.to_column)) for r in chain]
        final = self._index(table, column)

        def get(row: list) -> Any:
            for i, lookup in hops:
                row = lookup.get(normalize_key(row[i]))
                if row is None:
                    return None  # no matching row on the one side: Power BI shows it as (Blank)
            return row[final]

        return get


def _split_field(name: str, base: str) -> tuple[str, str]:
    from app.engine.dax import parse_field

    if name.strip().endswith("]") and "[" in name:
        return parse_field(name)
    return base, name.strip()


def _ordinal(value: Any) -> Optional[tuple]:
    """A sortable form for >, <, between: numbers as numbers, dates as ISO text."""
    from app.engine.values import is_blank, normalize_key, parse_number

    if is_blank(value):
        return None
    if hasattr(value, "isoformat") or (isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value.strip())):
        return (1, normalize_key(value))
    number = parse_number(value)
    if number is not None:
        return (0, number)
    return (2, str(value).casefold())


def _matcher(f: FilterContext):
    from app.engine.values import normalize_key

    op = (f.operator or "in").lower()
    if op in ("in", "equals", "=", "eq", "not_in", "not in", "!=", "<>"):
        wanted = {normalize_key(v) for v in f.values}
        negate = op in ("not_in", "not in", "!=", "<>")
        return lambda x: (normalize_key(x) in wanted) != negate
    bounds = [_ordinal(v) for v in f.values]
    tests = {">": lambda o: o > bounds[0], ">=": lambda o: o >= bounds[0], "<": lambda o: o < bounds[0],
             "<=": lambda o: o <= bounds[0], "between": lambda o: bounds[0] <= o <= bounds[-1]}
    if op not in tests or any(b is None for b in bounds):
        raise CapabilityError(f"Filter {f.field}: operator '{f.operator}' with values {f.values} isn't supported.")

    def match(x: Any) -> bool:
        o = _ordinal(x)
        if o is None or o[0] != bounds[0][0]:
            return False
        return tests[op](o)

    return match


def _aggregate_values(agg: str, raw: list[Any]) -> Any:
    import statistics

    from app.engine.values import is_blank, normalize_key, parse_number

    if agg == "DISTINCTCOUNT":  # DAX counts BLANK as one of the distinct values
        return len({normalize_key(x) for x in raw})
    if agg in ("COUNTA", "COUNT"):
        n = sum(1 for x in raw if not is_blank(x))
        return n or None
    nums = [n for n in (parse_number(x) for x in raw) if n is not None]
    if not nums:
        return None
    if agg == "AVERAGE":
        return sum(nums) / len(nums)
    if agg == "MIN":
        return min(nums)
    if agg == "MAX":
        return max(nums)
    if agg == "MEDIAN":
        return statistics.median(nums)
    if agg == "STDEV.P":
        return statistics.pstdev(nums)
    if agg == "VAR.P":
        return statistics.pvariance(nums)
    return sum(nums)


def _compute_visual(data: _RelatedRows, v: VisualDef, filters: list[FilterContext]) -> QueryResult:
    relationships = data.connector.model.relationships
    tests, warnings = [], []
    for f in filters:
        if not f.field.strip() or not f.values:
            continue  # an empty filter selects everything
        table, column = _split_field(f.field, data.base)
        if _path(data.base, table, relationships) is None:
            # Power BI filters only flow along relationships: a filter on an
            # unrelated table doesn't change this visual's numbers.
            both = any(r.cross_filter_direction == "both" and table in (r.from_table_id, r.to_table_id) for r in relationships)
            warnings.append(f"Filter on {column_id(table, column)} ignored: '{table}' isn't related to '{data.base}' "
                            "(many → one), so it doesn't filter this visual"
                            + (" — unless through a bi-directional relationship, which isn't reproduced here." if both else "."))
            continue
        tests.append((data.getter(table, column), _matcher(f)))
    rows = [r for r in data.rows if all(match(get(r)) for get, match in tests)]

    keys = [data.getter(g.entity, g.property) for g in v.group_by]
    values = [(data.getter(m.entity, m.property), (m.aggregation or "SUM").upper()) for m in v.measures]
    groups: dict[tuple, list[list]] = {}
    for r in rows:
        groups.setdefault(tuple(get(r) for get in keys), []).append(r)
    if not keys:
        groups = {(): rows}  # a card or total: one row even when nothing matches
    out = []
    for key, members in groups.items():
        out.append(list(key) + [_aggregate_values(agg, [get(r) for r in members]) for get, agg in values])
    return QueryResult(columns=[g.property for g in v.group_by] + [m.output_name() for m in v.measures], rows=out,
                       warnings=warnings)
