"""
Connector interface (Section 33) and the raw, platform-shaped data classes
it returns. These are deliberately NOT the canonical graph model — that
normalization step happens in app/engine/normalization.py. Keeping the two
separate is what lets PowerBIConnector, TableauConnector, and QlikConnector
all feed the same investigation engine without it knowing which platform
produced the data.

Every connector method that touches the network should be prepared to
report CAPABLE / PARTIALLY_CAPABLE / UNSUPPORTED / PERMISSION_DENIED /
NOT_AVAILABLE (Section 34) rather than silently returning an empty list —
silent emptiness is exactly the "false completeness" Section 35 forbids.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.graph import CapabilityStatus, Confidence
from app.models.snapshot import CapturedValue, FilterContext, VisualSnapshot


class ConnectorError(RuntimeError):
    """The platform answered with an error (auth, permissions, bad query).
    Carries the platform's own message so the user can act on it."""


class CapabilityError(NotImplementedError):
    """Raised when a connector genuinely can't do something (yet). The API
    turns this into a 501 with the message, instead of a silent empty
    result (Section 35)."""


class Workspace(BaseModel):
    id: str
    name: str


class Report(BaseModel):
    id: str
    name: str
    workspace_id: str
    model_id: str


class Page(BaseModel):
    id: str
    report_id: str
    name: str


class Visual(BaseModel):
    id: str
    report_id: str
    page_id: str
    title: str
    visual_type: str
    measure_ids: list[str] = Field(default_factory=list)
    default_filters: list[FilterContext] = Field(default_factory=list)
    # fields used in visual / page / report-level filters (they change the number too)
    filter_ids: list[str] = Field(default_factory=list)
    # report filters that can't be reproduced outside the platform (Top N, measure-value...)
    filter_notes: list[str] = Field(default_factory=list)


class SemanticModel(BaseModel):
    id: str
    name: str
    workspace_id: str


class Measure(BaseModel):
    id: str
    model_id: str
    name: str
    dax_expression: str
    table_id: str
    depends_on_measure_ids: list[str] = Field(default_factory=list)
    depends_on_column_ids: list[str] = Field(default_factory=list)
    depends_on_table_ids: list[str] = Field(default_factory=list)   # e.g. COUNTROWS(Sales)
    display_folder: Optional[str] = None
    description: Optional[str] = None
    # How the dependencies were obtained — becomes the lineage edge's
    # confidence: "declared" (metadata said so) | "engine" (the model's own
    # dependency view, e.g. INFO.CALCDEPENDENCY) | "parsed" (read out of the
    # expression text by us).
    dependency_source: str = "declared"


class Table(BaseModel):
    id: str
    model_id: str
    name: str
    source_ref: Optional[str] = None
    kind: str = "table"  # import | calculated | auto_date | measures | parameter | table
    hidden: bool = False
    expression: Optional[str] = None           # Power Query M or calculated-table DAX
    source_detail: dict[str, Any] = Field(default_factory=dict)
    depends_on_table_ids: list[str] = Field(default_factory=list)  # other queries / tables it's built from
    depends_on_column_ids: list[str] = Field(default_factory=list)


class Column(BaseModel):
    id: str
    table_id: str
    name: str
    data_type: str = "string"
    hidden: bool = False
    expression: Optional[str] = None           # calculated column DAX
    depends_on_column_ids: list[str] = Field(default_factory=list)
    depends_on_measure_ids: list[str] = Field(default_factory=list)


class Relationship(BaseModel):
    id: str
    model_id: str
    from_table_id: str        # the "many" side (or either side of a one-to-one)
    from_column_id: str
    to_table_id: str          # the "one" side
    to_column_id: str
    cross_filter_direction: str = "single"   # single | both
    is_active: bool = True
    cardinality: Optional[str] = None         # many-to-one | one-to-one | many-to-many

    @property
    def from_column(self) -> str:
        return _column_part(self.from_column_id)

    @property
    def to_column(self) -> str:
        return _column_part(self.to_column_id)


def _column_part(column_id: str) -> str:
    return column_id[column_id.rindex("[") + 1:-1] if column_id.endswith("]") and "[" in column_id else column_id


def _cardinality_word(value: Any, default: str) -> str:
    """'M' / 'Many' / 2 -> 'many'; '1' / 'One' / 1 -> 'one'."""
    if value is None or (isinstance(value, float) and value != value):
        return default
    text = str(value).strip().lower()
    if text in ("m", "many", "2", "*"):
        return "many"
    if text in ("1", "one", "none"):
        return "one"
    return default


def _truthy(value: Any, default: bool = True) -> bool:
    """IsActive arrives as bool, 0/1, numpy ints or 'true'/'false' strings
    depending on the file format — bool('false') would be True."""
    if value is None or (isinstance(value, float) and value != value):
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        return default if not text else text in ("true", "1", "yes")
    return bool(value)


def make_relationship(
    rel_id: str,
    model_id: str,
    from_table: str,
    from_column: str,
    to_table: str,
    to_column: str,
    *,
    from_cardinality: Any = None,
    to_cardinality: Any = None,
    cardinality: Any = None,
    cross_filter: Any = None,
    is_active: Any = True,
) -> Relationship:
    """Build a Relationship in one canonical shape whatever the source
    (pbixray 'M:1', TMSL fromCardinality/toCardinality, TMDL, INFO.VIEW):
    the many side is always 'from', cardinality reads 'many-to-one' /
    'one-to-one' / 'many-to-many', direction is 'single' or 'both'."""
    if cardinality and ":" in str(cardinality):
        from_cardinality, to_cardinality = str(cardinality).split(":", 1)
    elif cardinality and "-to-" in str(cardinality):
        from_cardinality, to_cardinality = str(cardinality).split("-to-", 1)
    fc = _cardinality_word(from_cardinality, "many")
    tc = _cardinality_word(to_cardinality, "one")
    if fc == "one" and tc == "many":
        # stored the other way round: keep "from" as the many side
        from_table, from_column, to_table, to_column = to_table, to_column, from_table, from_column
        fc, tc = tc, fc
    cross = str(cross_filter if cross_filter is not None else "").lower()
    return Relationship(
        id=str(rel_id), model_id=model_id,
        from_table_id=from_table, from_column_id=f"{from_table}[{from_column}]",
        to_table_id=to_table, to_column_id=f"{to_table}[{to_column}]",
        cross_filter_direction="both" if "both" in cross or cross == "2" else "single",
        is_active=_truthy(is_active),
        cardinality=f"{fc}-to-{tc}",
    )


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    warnings: list[str] = Field(default_factory=list)  # e.g. filters that didn't apply to this data


class ConnectorCapabilities(BaseModel):
    metadata_api: CapabilityStatus = CapabilityStatus.NOT_AVAILABLE
    execute_query: CapabilityStatus = CapabilityStatus.NOT_AVAILABLE
    pbip_tmdl: CapabilityStatus = CapabilityStatus.NOT_AVAILABLE
    visual_export: CapabilityStatus = CapabilityStatus.NOT_AVAILABLE
    visual_data: CapabilityStatus = CapabilityStatus.NOT_AVAILABLE  # tabular data behind a visual / tab
    notes: list[str] = Field(default_factory=list)


class BIConnector(ABC):
    """Every BI platform adapter implements this. The investigation engine
    only ever talks to this interface, never to a specific platform SDK."""

    platform_name: str = "generic"

    @abstractmethod
    def capabilities(self) -> ConnectorCapabilities: ...

    @abstractmethod
    def get_workspaces(self) -> list[Workspace]: ...

    @abstractmethod
    def get_reports(self, workspace_id: str) -> list[Report]: ...

    @abstractmethod
    def get_pages(self, report_id: str) -> list[Page]: ...

    @abstractmethod
    def get_visuals(self, report_id: str, page_id: str) -> list[Visual]: ...

    @abstractmethod
    def get_semantic_model(self, model_id: str) -> SemanticModel: ...

    @abstractmethod
    def get_measures(self, model_id: str) -> list[Measure]: ...

    @abstractmethod
    def get_tables(self, model_id: str) -> list[Table]: ...

    @abstractmethod
    def get_columns(self, model_id: str, table_id: str) -> list[Column]: ...

    @abstractmethod
    def get_relationships(self, model_id: str) -> list[Relationship]: ...

    @abstractmethod
    def execute_query(self, model_id: str, dax_query: str) -> QueryResult: ...

    def get_visual_data(
        self,
        report_id: str,
        page_id: str,
        visual_id: str,
        filters: list[FilterContext],
    ) -> QueryResult:
        """The table of data behind one visual (Power BI) / view (Tableau) /
        sheet (file export), with filters applied. This is what gets
        reconciled against Excel."""
        raise CapabilityError(f"{self.platform_name} connector cannot extract visual data yet.")

    def get_visual_snapshot(
        self,
        report_id: str,
        page_id: str,
        visual_id: str,
        filters: list[FilterContext],
        snapshot_id: str,
    ) -> VisualSnapshot:
        """Default: total every numeric column of the visual's data. Card
        visuals return one row, so this is exactly the displayed value."""
        visual = next((v for v in self.get_visuals(report_id, page_id) if v.id == visual_id), None)
        if visual is None:
            raise KeyError(f"visual {visual_id} not found on page {page_id}")
        data = self.get_visual_data(report_id, page_id, visual_id, filters)
        from app.engine.values import parse_number

        values = []
        for j, col in enumerate(data.columns):
            numbers = [parse_number(row[j]) for row in data.rows if j < len(row)]
            parsed = [n for n in numbers if n is not None]
            if parsed and len(parsed) >= 0.9 * len(numbers):
                values.append(CapturedValue(metric_name=_short_name(col), value=sum(parsed)))
        return VisualSnapshot(
            snapshot_id=snapshot_id,
            platform=self.platform_name,
            report_id=report_id,
            page_id=page_id,
            visual_id=visual_id,
            visual_title=visual.title,
            measure_id=visual.measure_ids[0] if visual.measure_ids else None,
            filters=filters,
            values=values,
            coverage={"rows": len(data.rows), "method": "sum of visual data"},
            confidence=Confidence.VERIFIED if values else Confidence.UNAVAILABLE,
        )


def _short_name(name: str) -> str:
    name = str(name)
    return name[name.rindex("[") + 1:-1] if name.endswith("]") and "[" in name else name
