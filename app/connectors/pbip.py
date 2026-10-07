"""
Power BI Project (PBIP) reader and offline connector — Section 34's
"PBIP / TMDL" extraction mode.

A PBIP folder (File > Save as > Power BI project in Desktop, or a Fabric
git integration checkout) contains everything the REST API won't give you:

  <Name>.Report/definition/pages/<page>/page.json
  <Name>.Report/definition/pages/<page>/visuals/<visual>/visual.json   (PBIR)
  <Name>.SemanticModel/definition/tables/*.tmdl                       (TMDL)
  <Name>.SemanticModel/definition/relationships.tmdl

So with only files — no tenant, no service principal — this gives full
visual -> measure -> column -> table -> source lineage and impact analysis.
It can't return *data* (nothing is computed offline): pair it with
exported files, or with the live PowerBIConnector, which uses this reader
for visual bindings.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

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
from app.engine.dax import extract_references
from app.models.graph import CapabilityStatus
from app.models.snapshot import FilterContext

_NAME = r"'(?:[^']|'')+'|[^\s=]+(?:\s+[^\s=]+)*?"
_OBJECT = re.compile(rf"^(table|measure|column|partition|calculationGroup|calculationItem|hierarchy)\s+({_NAME})\s*(=\s*(.*))?$")
_PROP = re.compile(r"^([A-Za-z][\w]*)\s*:\s*(.*)$")
_M_SOURCE = re.compile(
    r'(Sql\.Databases?\s*\([^)]*\)|Snowflake\.Databases\s*\([^)]*\)|Excel\.Workbook\s*\(\s*File\.Contents\s*\([^)]*\)'
    r'|Csv\.Document\s*\(\s*File\.Contents\s*\([^)]*\)|Web\.Contents\s*\([^)]*\)|SharePoint\.\w+\s*\([^)]*\)'
    r'|Odbc\.\w+\s*\([^)]*\)|Oracle\.Database\s*\([^)]*\)|PostgreSQL\.Database\s*\([^)]*\)'
    r'|GoogleBigQuery\.Database\s*\([^)]*\)|Databricks\.\w+\s*\([^)]*\)|AzureStorage\.\w+\s*\([^)]*\))'
)
# Power BI's QueryAggregateFunction enum -> the DAX function it stands for.
_AGG_FUNCTIONS = {0: "SUM", 1: "AVERAGE", 2: "DISTINCTCOUNT", 3: "MIN", 4: "MAX", 5: "COUNTA", 6: "MEDIAN",
                  7: "STDEV.P", 8: "VAR.P"}
_AGG_LABELS = {"SUM": "Sum", "AVERAGE": "Average", "DISTINCTCOUNT": "Count", "MIN": "Min", "MAX": "Max",
               "COUNTA": "Count", "MEDIAN": "Median", "STDEV.P": "Standard deviation", "VAR.P": "Variance"}
# QueryComparisonKind
_COMPARISONS = {0: "=", 1: ">", 2: ">=", 3: "<", 4: "<="}


def _unquote(name: str) -> str:
    name = name.strip()
    if name.startswith("'") and name.endswith("'"):
        return name[1:-1].replace("''", "'")
    return name


def _indent(line: str) -> int:
    raw = line[: len(line) - len(line.lstrip())]
    return raw.count("\t") + raw.count(" ") // 4


# ------------------------------------------------------------------- TMDL
@dataclass
class TmdlColumn:
    name: str
    data_type: str = "string"
    expression: Optional[str] = None  # calculated column


@dataclass
class TmdlMeasure:
    name: str
    expression: str


@dataclass
class TmdlTable:
    name: str
    columns: list[TmdlColumn] = field(default_factory=list)
    measures: list[TmdlMeasure] = field(default_factory=list)
    source: Optional[str] = None


@dataclass
class TmdlRelationship:
    id: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cross_filter: str = "single"
    is_active: bool = True
    from_cardinality: str = "many"
    to_cardinality: str = "one"

    def to_relationship(self, model_id: str) -> Relationship:
        return make_relationship(self.id, model_id, self.from_table, self.from_column, self.to_table, self.to_column,
                                 from_cardinality=self.from_cardinality, to_cardinality=self.to_cardinality,
                                 cross_filter=self.cross_filter, is_active=self.is_active)


def parse_tmdl_table(text: str) -> Optional[TmdlTable]:
    lines = text.replace("\r\n", "\n").split("\n")
    table: Optional[TmdlTable] = None
    current: Any = None
    current_kind = None
    current_indent = 0
    expr_lines: list[str] = []
    in_fence = False

    def flush() -> None:
        nonlocal expr_lines
        if current is None or not expr_lines:
            expr_lines = []
            return
        expr = "\n".join(l.strip() for l in expr_lines).strip().strip("`").strip()
        if current_kind == "measure":
            current.expression = (current.expression + "\n" + expr).strip() if current.expression else expr
        elif current_kind == "column":
            current.expression = (current.expression or "") + expr
        elif current_kind == "partition" and table is not None:
            m = _M_SOURCE.search(expr)
            table.source = (m.group(1) if m else None) or table.source or "power_query"
        expr_lines = []

    for line in lines:
        stripped = line.strip()
        if in_fence:
            expr_lines.append(line)
            if stripped.endswith("```"):
                in_fence = False
            continue
        if not stripped or stripped.startswith("///") or stripped.startswith("//"):
            continue
        ind = _indent(line)
        m = _OBJECT.match(stripped)
        if m and (m.group(1) == "table" and ind == 0 or m.group(1) != "table" and ind == 1):
            flush()
            kind, name, rest = m.group(1), _unquote(m.group(2)), (m.group(4) or "").strip()
            if kind == "table":
                table = TmdlTable(name=name)
                current, current_kind = table, "table"
            elif table is not None and kind == "measure":
                current = TmdlMeasure(name=name, expression=rest.strip("`") if rest and rest != "```" else "")
                table.measures.append(current)
                current_kind = "measure"
            elif table is not None and kind == "column":
                current = TmdlColumn(name=name, expression=rest or None)
                table.columns.append(current)
                current_kind = "column"
            elif table is not None and kind == "partition":
                current, current_kind = object(), "partition"
            else:
                current, current_kind = None, None
            current_indent = ind
            if rest.startswith("```"):
                in_fence = not rest.endswith("```") or rest == "```"
            continue
        if current is None:
            continue
        prop = _PROP.match(stripped)
        if ind == current_indent + 1 and prop and current_kind == "column" and prop.group(1) == "dataType":
            flush()
            current.data_type = prop.group(2).strip()
            continue
        if ind == current_indent + 1 and (prop or stripped.startswith("source =") or stripped.startswith("annotation ")):
            flush()
            if stripped.startswith("source =") and current_kind == "partition":
                rest = stripped[len("source ="):].strip()
                if rest:
                    expr_lines.append(rest)
            continue
        if ind >= current_indent + 2:
            expr_lines.append(line)
            if stripped.startswith("```") and not (len(stripped) > 3 and stripped.endswith("```")):
                in_fence = True
    flush()
    return table


def parse_tmdl_relationships(text: str) -> list[TmdlRelationship]:
    rels: list[TmdlRelationship] = []
    current: dict[str, str] = {}

    def done() -> None:
        if current.get("fromColumn") and current.get("toColumn"):
            ft, fc = _split_column(current["fromColumn"])
            tt, tc = _split_column(current["toColumn"])
            cf = current.get("crossFilteringBehavior", "oneDirection")
            rels.append(TmdlRelationship(id=_unquote(current["id"]), from_table=ft, from_column=fc, to_table=tt, to_column=tc,
                                         cross_filter="both" if "both" in cf.lower() else "single",
                                         is_active=current.get("isActive", "true").lower() != "false",
                                         from_cardinality=current.get("fromCardinality", "many"),
                                         to_cardinality=current.get("toCardinality", "one")))

    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith("relationship "):
            done()
            current = {"id": stripped.split(None, 1)[1].strip()}
            continue
        prop = _PROP.match(stripped)
        if prop and current:
            current[prop.group(1)] = prop.group(2).strip()
    done()
    return rels


def _split_column(ref: str) -> tuple[str, str]:
    m = re.match(r"^('(?:[^']|'')+'|[^.]+)\.(.+)$", ref.strip())
    if not m:
        return ref, ref
    return _unquote(m.group(1)), _unquote(m.group(2))


# ------------------------------------------------------------------- PBIR
@dataclass
class FieldRef:
    entity: str
    property: str
    kind: str  # measure | column | aggregation
    aggregation: Optional[str] = None

    def dax(self) -> str:
        from app.engine.dax import column_ref, measure_ref

        if self.kind == "measure":
            return measure_ref(self.property)
        if self.kind == "aggregation":
            return f"{self.aggregation or 'SUM'}({column_ref(self.entity, self.property)})"
        return column_ref(self.entity, self.property)

    def output_name(self) -> str:
        if self.kind == "aggregation":
            agg = self.aggregation or "SUM"
            return f"{_AGG_LABELS.get(agg, agg.title())} of {self.property}"
        return self.property

    def label(self) -> str:
        return measure_id(self.property) if self.kind == "measure" else column_id(self.entity, self.property)


@dataclass
class FilterCondition:
    """A filter's selection, read from the report: field + operator + values.
    Operators match FilterContext: in | not_in | > | >= | < | <=."""
    field: FieldRef
    operator: str
    values: list[Any]


@dataclass
class VisualDef:
    id: str
    title: str
    visual_type: str
    measures: list[FieldRef] = field(default_factory=list)   # values (measures or aggregated columns)
    group_by: list[FieldRef] = field(default_factory=list)   # category / rows / axis columns
    filters: list[FieldRef] = field(default_factory=list)    # visual-level filters
    conditions: list[FilterCondition] = field(default_factory=list)  # what those filters select
    filter_notes: list[str] = field(default_factory=list)    # filters that can't be applied outside Power BI


def _entity(expr: dict, aliases: Optional[dict[str, str]] = None) -> str:
    ref = expr.get("SourceRef", {})
    if "Entity" in ref:
        return ref["Entity"]
    return (aliases or {}).get(ref.get("Source", ""), ref.get("Source", ""))


def _field_ref(field_json: dict, aliases: Optional[dict[str, str]] = None) -> Optional[FieldRef]:
    """A field reference from a PBIR projection or a legacy prototypeQuery
    select (which uses a short alias -> entity map)."""
    for kind in ("Measure", "Column"):
        if kind in field_json:
            node = field_json[kind]
            return FieldRef(entity=_entity(node.get("Expression", {}), aliases), property=node.get("Property", ""), kind=kind.lower())
    if "Aggregation" in field_json:
        inner = _field_ref(field_json["Aggregation"].get("Expression", {}), aliases)
        if inner:
            inner.kind = "aggregation"
            inner.aggregation = _AGG_FUNCTIONS.get(field_json["Aggregation"].get("Function", 0), "SUM")
            return inner
    if "HierarchyLevel" in field_json:
        # date hierarchy (Year/Quarter/Month...) -> the underlying date column
        hier = field_json["HierarchyLevel"].get("Expression", {}).get("Hierarchy", {})
        variation = hier.get("Expression", {}).get("PropertyVariationSource")
        if variation:
            return FieldRef(entity=_entity(variation.get("Expression", {}), aliases), property=variation.get("Property", ""), kind="column")
        entity = _entity(hier.get("Expression", {}), aliases)
        level = field_json["HierarchyLevel"].get("Level", "")
        return FieldRef(entity=entity, property=level, kind="column")
    return None


def filter_fields(filters: list, aliases: Optional[dict[str, str]] = None) -> list[FieldRef]:
    out = []
    for f in filters or []:
        expr = f.get("field") or f.get("expression") or {}
        ref = _field_ref(expr, aliases)
        if ref and ref.property:
            out.append(ref)
    return out


class _Unsupported(ValueError):
    """A filter whose selection can't be reproduced outside Power BI."""


def parse_literal(raw: Any) -> Any:
    """Power BI query literals: 'text', 12L, 3.5D, 1.2M, true, null, datetime'2024-01-01T00:00:00'."""
    if not isinstance(raw, str):
        return raw
    if raw == "null":
        return None
    if raw in ("true", "false"):
        return raw == "true"
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    m = re.match(r"^datetime'(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}:\d{2}))?[^']*'$", raw)
    if m:
        return m.group(1) if not m.group(2) or m.group(2) == "00:00:00" else f"{m.group(1)}T{m.group(2)}"
    m = re.match(r"^(-?\d+)L$", raw)
    if m:
        return int(m.group(1))
    m = re.match(r"^(-?\d+(?:\.\d+)?(?:E[+-]?\d+)?)[DM]$", raw, re.I)
    if m:
        return float(m.group(1))
    return raw


def _literal(node: Any) -> Any:
    if isinstance(node, dict) and "Literal" in node:
        return parse_literal(node["Literal"].get("Value"))
    kind = next(iter(node), "expression") if isinstance(node, dict) else "expression"
    raise _Unsupported("relative date filter" if kind in ("DateSpan", "DateAdd", "Now") else f"{kind} value")


def _column(node: dict, aliases: dict[str, str]) -> FieldRef:
    ref = _field_ref(node, aliases)
    if ref is None:
        raise _Unsupported("filter on an expression")
    if ref.kind == "measure":
        raise _Unsupported(f"filter on the value of measure [{ref.property}]")
    if ref.kind == "aggregation":
        raise _Unsupported(f"filter on an aggregated value ({ref.output_name()})")
    return ref


def _conditions(cond: dict, aliases: dict[str, str]) -> list[FilterCondition]:
    if "In" in cond:
        node = cond["In"]
        if "Table" in node:
            raise _Unsupported("Top N filter (needs the Power BI engine to rank)")
        if len(node.get("Expressions", [])) != 1:
            raise _Unsupported("multi-column filter")
        ref = _column(node["Expressions"][0], aliases)
        return [FilterCondition(ref, "in", [_literal(row[0]) for row in node.get("Values", []) if row])]
    if "Not" in cond:
        inner = _conditions(cond["Not"].get("Expression", {}), aliases)
        if len(inner) == 1 and inner[0].operator == "in":
            return [FilterCondition(inner[0].field, "not_in", inner[0].values)]
        raise _Unsupported("negated condition")
    if "Comparison" in cond:
        node = cond["Comparison"]
        ref = _column(node.get("Left", {}), aliases)
        op = _COMPARISONS.get(node.get("ComparisonKind", 0), "=")
        value = _literal(node.get("Right", {}))
        return [FilterCondition(ref, "in" if op == "=" else op, [value])]
    if "And" in cond:
        return _conditions(cond["And"].get("Left", {}), aliases) + _conditions(cond["And"].get("Right", {}), aliases)
    if "Or" in cond:
        parts = _conditions(cond["Or"].get("Left", {}), aliases) + _conditions(cond["Or"].get("Right", {}), aliases)
        if parts and all(p.operator == "in" and p.field.label() == parts[0].field.label() for p in parts):
            return [FilterCondition(parts[0].field, "in", [v for p in parts for v in p.values])]
        raise _Unsupported("OR across different conditions")
    raise _Unsupported(f"{next(iter(cond), 'unknown')} condition")


def filter_conditions(filters: list, aliases: Optional[dict[str, str]] = None) -> tuple[list[FilterCondition], list[str]]:
    """The selections behind a filter pane (visual, page or report level).
    Returns the conditions that can be applied to the data, plus a note for
    each filter that can't (Top N, measure-value, relative date...), so a
    number pulled from the file is never silently less filtered than the
    dashboard."""
    conditions: list[FilterCondition] = []
    notes: list[str] = []
    for f in filters or []:
        body = f.get("filter")
        if not body or not body.get("Where"):
            continue  # a field in the filter pane with nothing selected changes nothing
        local = {**(aliases or {}), **{x.get("Name"): x.get("Entity") for x in body.get("From", []) if x.get("Entity")}}
        ref = _field_ref(f.get("field") or f.get("expression") or {}, aliases)
        label = ref.label() if ref else f.get("name", "filter")
        try:
            found = [c for w in body["Where"] for c in _conditions(w.get("Condition", {}), local)]
        except _Unsupported as e:
            notes.append(f"{label}: {e} — not applied")
            continue
        conditions += found
    return conditions, notes


def parse_visual_json(visual_id: str, raw: dict) -> VisualDef:
    visual = raw.get("visual", {})
    title = visual_id
    try:
        title_value = visual["visualContainerObjects"]["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"]
        title = title_value.strip("'")
    except (KeyError, IndexError, TypeError):
        title = visual.get("title", {}).get("text", visual_id) if isinstance(visual.get("title"), dict) else visual_id
    definition = VisualDef(id=visual_id, title=title, visual_type=visual.get("visualType", "unknown"))
    for role, bucket in visual.get("query", {}).get("queryState", {}).items():
        for projection in bucket.get("projections", []):
            ref = _field_ref(projection.get("field", {}))
            if ref is None:
                continue
            if ref.kind in ("measure", "aggregation"):
                definition.measures.append(ref)
            else:
                definition.group_by.append(ref)
    raw_filters = raw.get("filterConfig", {}).get("filters", [])
    definition.filters = filter_fields(raw_filters)
    definition.conditions, definition.filter_notes = filter_conditions(raw_filters)
    if definition.title == visual_id and definition.measures:
        definition.title = ", ".join(m.output_name() for m in definition.measures)
    return definition


class PbipProject:
    def __init__(self, root: Path):
        self.root = Path(root)
        if not self.root.exists():
            raise FileNotFoundError(f"PBIP folder not found: {self.root}")
        if self.root.name.endswith(".Report"):
            self.report_dirs = [self.root]
            self.model_dirs = sorted(self.root.parent.glob("*.SemanticModel"))
        else:
            self.report_dirs = sorted(self.root.glob("*.Report"))
            self.model_dirs = sorted(self.root.glob("*.SemanticModel"))
        self._models: dict[str, tuple[list[TmdlTable], list[TmdlRelationship]]] = {}

    @staticmethod
    def stem(path: Path) -> str:
        return path.name.rsplit(".", 1)[0]

    def model_for_report(self, report_dir: Path) -> Optional[Path]:
        pbir = report_dir / "definition.pbir"
        if pbir.exists():
            try:
                ref = json.loads(pbir.read_text(encoding="utf-8")).get("datasetReference", {}).get("byPath", {}).get("path")
                if ref:
                    candidate = (report_dir / ref).resolve()
                    if candidate.exists():
                        return candidate
            except json.JSONDecodeError:
                pass
        same = [m for m in self.model_dirs if self.stem(m) == self.stem(report_dir)]
        return (same or self.model_dirs or [None])[0]

    def pages(self, report_dir: Path) -> list[tuple[str, str]]:
        pages_dir = report_dir / "definition" / "pages"
        if not pages_dir.exists():
            return []
        order: list[str] = []
        meta = pages_dir / "pages.json"
        if meta.exists():
            order = json.loads(meta.read_text(encoding="utf-8")).get("pageOrder", [])
        found = []
        for page_dir in sorted(p for p in pages_dir.iterdir() if p.is_dir()):
            page_json = page_dir / "page.json"
            name = page_dir.name
            if page_json.exists():
                name = json.loads(page_json.read_text(encoding="utf-8")).get("displayName", name)
            found.append((page_dir.name, name))
        found.sort(key=lambda p: order.index(p[0]) if p[0] in order else len(order))
        return found

    def visuals(self, report_dir: Path, page_id: str) -> list[VisualDef]:
        visuals_dir = report_dir / "definition" / "pages" / page_id / "visuals"
        out = []
        if not visuals_dir.exists():
            return out
        for vdir in sorted(p for p in visuals_dir.iterdir() if p.is_dir()):
            vjson = vdir / "visual.json"
            if vjson.exists():
                out.append(parse_visual_json(vdir.name, json.loads(vjson.read_text(encoding="utf-8"))))
        return out

    def model(self, model_dir: Path) -> tuple[list[TmdlTable], list[TmdlRelationship]]:
        key = str(model_dir)
        if key not in self._models:
            definition = model_dir / "definition"
            tables = []
            for tmdl in sorted((definition / "tables").glob("*.tmdl")):
                t = parse_tmdl_table(tmdl.read_text(encoding="utf-8"))
                if t:
                    tables.append(t)
            rel_file = definition / "relationships.tmdl"
            rels = parse_tmdl_relationships(rel_file.read_text(encoding="utf-8")) if rel_file.exists() else []
            self._models[key] = (tables, rels)
        return self._models[key]


# -------------------------------------------------------------- connector
def column_id(table: str, column: str) -> str:
    return f"{table}[{column}]"


def measure_id(name: str) -> str:
    return f"[{name}]"


class PbipConnector(BIConnector):
    """Offline Power BI connector over a PBIP folder: metadata + lineage,
    no data values."""

    platform_name = "powerbi"

    def __init__(self, project_dir: Path):
        self.project = PbipProject(Path(project_dir))
        self._report_dirs = {self.project.stem(r): r for r in self.project.report_dirs}
        self._model_dirs = {self.project.stem(m): m for m in self.project.model_dirs}

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            metadata_api=CapabilityStatus.NOT_AVAILABLE,
            execute_query=CapabilityStatus.UNSUPPORTED,
            pbip_tmdl=CapabilityStatus.CAPABLE,
            visual_export=CapabilityStatus.CAPABLE,
            visual_data=CapabilityStatus.UNSUPPORTED,
            notes=["Offline PBIP project: full metadata and lineage, but no data values. Reconcile values using "
                   "exported files or a live Power BI connection."],
        )

    def get_workspaces(self) -> list[Workspace]:
        return [Workspace(id="pbip", name=self.project.root.name)]

    def get_reports(self, workspace_id: str) -> list[Report]:
        out = []
        for stem, rdir in self._report_dirs.items():
            model_dir = self.project.model_for_report(rdir)
            out.append(Report(id=stem, name=stem, workspace_id=workspace_id,
                              model_id=self.project.stem(model_dir) if model_dir else ""))
        return out

    def get_pages(self, report_id: str) -> list[Page]:
        return [Page(id=pid, report_id=report_id, name=name) for pid, name in self.project.pages(self._report(report_id))]

    def visual_definitions(self, report_id: str, page_id: str) -> list[VisualDef]:
        return self.project.visuals(self._report(report_id), page_id)

    def get_visuals(self, report_id: str, page_id: str) -> list[Visual]:
        return [
            Visual(
                id=v.id, report_id=report_id, page_id=page_id, title=v.title, visual_type=v.visual_type,
                measure_ids=[measure_id(f.property) if f.kind == "measure" else column_id(f.entity, f.property)
                             for f in v.measures] + [column_id(f.entity, f.property) for f in v.group_by],
            )
            for v in self.visual_definitions(report_id, page_id)
        ]

    def get_semantic_model(self, model_id: str) -> SemanticModel:
        self._model(model_id)
        return SemanticModel(id=model_id, name=model_id, workspace_id="pbip")

    def _tables(self, model_id: str) -> tuple[list[TmdlTable], list[TmdlRelationship]]:
        return self.project.model(self._model(model_id))

    def get_tables(self, model_id: str) -> list[Table]:
        tables, _ = self._tables(model_id)
        return [Table(id=t.name, model_id=model_id, name=t.name, source_ref=t.source) for t in tables]

    def get_columns(self, model_id: str, table_id: str) -> list[Column]:
        tables, _ = self._tables(model_id)
        for t in tables:
            if t.name == table_id:
                return [Column(id=column_id(t.name, c.name), table_id=t.name, name=c.name, data_type=c.data_type) for c in t.columns]
        return []

    def get_measures(self, model_id: str) -> list[Measure]:
        tables, _ = self._tables(model_id)
        names = [m.name for t in tables for m in t.measures]
        columns_by_table = {t.name: {c.name for c in t.columns} for t in tables}
        out = []
        for t in tables:
            for m in t.measures:
                refs = extract_references(m.expression, names, columns_by_table, home_table=t.name)
                out.append(Measure(
                    id=measure_id(m.name), model_id=model_id, name=m.name, dax_expression=m.expression, table_id=t.name,
                    depends_on_measure_ids=sorted(measure_id(n) for n in refs.measures if n != m.name),
                    depends_on_column_ids=sorted(column_id(tb, c) for tb, c in refs.columns),
                    dependency_source="parsed",
                ))
        return out

    def get_relationships(self, model_id: str) -> list[Relationship]:
        _, rels = self._tables(model_id)
        return [r.to_relationship(model_id) for r in rels]

    def execute_query(self, model_id: str, dax_query: str) -> QueryResult:
        raise CapabilityError("An offline PBIP project can't run queries — use a live Power BI connection or exported files.")

    def get_visual_data(self, report_id: str, page_id: str, visual_id: str, filters: list[FilterContext]) -> QueryResult:
        raise CapabilityError(
            "An offline PBIP project has no data values. Export the visual's data (… > Export data) and upload it, "
            "or connect live to the published report."
        )

    def _report(self, report_id: str) -> Path:
        if report_id not in self._report_dirs:
            raise KeyError(f"report {report_id} not found in PBIP project")
        return self._report_dirs[report_id]

    def _model(self, model_id: str) -> Path:
        if model_id not in self._model_dirs:
            raise KeyError(f"semantic model {model_id} not found in PBIP project")
        return self._model_dirs[model_id]
