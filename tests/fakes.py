"""
Test double: MockConnector implements the exact same interface as
PowerBIConnector, using a local fixture file instead of live API calls.

Because the investigation engine only ever depends on BIConnector (Section
33's "the core investigation engine should not know which connector
generated the data"), swapping MockConnector for PowerBIConnector later is
a one-line change in wherever the connector is instantiated — nothing in
app/engine or app/api needs to change.
"""
from __future__ import annotations

import json
from pathlib import Path

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
)
from app.models.graph import CapabilityStatus, Confidence
from app.models.snapshot import CapturedValue, FilterContext, VisualSnapshot

DEFAULT_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_workspace.json"


class MockConnector(BIConnector):
    platform_name = "powerbi"

    def __init__(self, fixture_path: Path = DEFAULT_FIXTURE):
        with open(fixture_path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            metadata_api=CapabilityStatus.CAPABLE,
            execute_query=CapabilityStatus.CAPABLE,
            pbip_tmdl=CapabilityStatus.NOT_AVAILABLE,
            visual_export=CapabilityStatus.PARTIALLY_CAPABLE,
            visual_data=CapabilityStatus.CAPABLE,
            notes=["Mock workspace from fixtures/sample_workspace.json — no network or credentials used."],
        )

    def get_workspaces(self) -> list[Workspace]:
        return [Workspace(**w) for w in self._data["workspaces"]]

    def get_reports(self, workspace_id: str) -> list[Report]:
        return [Report(**r) for r in self._data["reports"] if r["workspace_id"] == workspace_id]

    def get_pages(self, report_id: str) -> list[Page]:
        return [Page(**p) for p in self._data["pages"] if p["report_id"] == report_id]

    def get_visuals(self, report_id: str, page_id: str) -> list[Visual]:
        return [
            Visual(**v)
            for v in self._data["visuals"]
            if v["report_id"] == report_id and v["page_id"] == page_id
        ]

    def get_semantic_model(self, model_id: str) -> SemanticModel:
        for m in self._data["semantic_models"]:
            if m["id"] == model_id:
                return SemanticModel(**m)
        raise KeyError(f"semantic model {model_id} not found")

    def get_measures(self, model_id: str) -> list[Measure]:
        return [Measure(**m) for m in self._data["measures"] if m["model_id"] == model_id]

    def get_tables(self, model_id: str) -> list[Table]:
        return [Table(**t) for t in self._data["tables"] if t["model_id"] == model_id]

    def get_columns(self, model_id: str, table_id: str) -> list[Column]:
        return [Column(**c) for c in self._data["columns"] if c["table_id"] == table_id]

    def get_relationships(self, model_id: str) -> list[Relationship]:
        return [Relationship(**r) for r in self._data["relationships"] if r["model_id"] == model_id]

    def execute_query(self, model_id: str, dax_query: str) -> QueryResult:
        # A real connector sends this to the Execute Queries In Dataset
        # endpoint. The mock just proves the contract shape.
        return QueryResult(columns=["[Result]"], rows=[[None]])

    def get_visual_data(
        self,
        report_id: str,
        page_id: str,
        visual_id: str,
        filters: list[FilterContext],
    ) -> QueryResult:
        self._find_visual(report_id, page_id, visual_id)
        table = self._data.get("visual_data", {}).get(visual_id)
        if table is None:
            raise CapabilityError(f"The mock workspace has no data behind visual {visual_id}.")
        columns = table["columns"]
        wanted = {f.field: {str(v) for v in f.values} for f in filters if f.values}
        index = {col: j for j, col in enumerate(columns)}
        rows = [
            row for row in table["rows"]
            if all(str(row[index[field]]) in values for field, values in wanted.items() if field in index)
        ]
        return QueryResult(columns=columns, rows=rows)

    def get_visual_snapshot(
        self,
        report_id: str,
        page_id: str,
        visual_id: str,
        filters: list[FilterContext],
        snapshot_id: str,
    ) -> VisualSnapshot:
        visual = self._find_visual(report_id, page_id, visual_id)
        measure_id = visual["measure_ids"][0] if visual["measure_ids"] else None
        measure_name = next(
            (m["name"] for m in self._data["measures"] if m["id"] == measure_id), visual["title"]
        )
        value = self._resolve_scripted_value(measure_id, filters)
        if value is None:
            value = self._value_from_visual_data(visual_id, measure_name, filters)

        snapshot = VisualSnapshot(
            snapshot_id=snapshot_id,
            platform=self.platform_name,
            report_id=report_id,
            page_id=page_id,
            visual_id=visual_id,
            visual_title=visual["title"],
            measure_id=measure_id,
            filters=filters,
            values=[CapturedValue(metric_name=measure_name, value=value, unit="GBP")] if value is not None else [],
            coverage={"metadata": "full", "query": "scripted" if value is not None else "unavailable"},
            confidence=Confidence.VERIFIED if value is not None else Confidence.UNAVAILABLE,
        )
        return snapshot

    def _find_visual(self, report_id: str, page_id: str, visual_id: str) -> dict:
        for v in self._data["visuals"]:
            if v["id"] == visual_id and v["report_id"] == report_id and v["page_id"] == page_id:
                return v
        raise KeyError(f"visual {visual_id} not found on report {report_id} page {page_id}")

    def _resolve_scripted_value(self, measure_id: str | None, filters: list[FilterContext]) -> float | None:
        filter_map = {f.field: (f.values[0] if f.values else None) for f in filters}
        scripted = self._data.get("scripted_values", {}).get(measure_id or "", [])
        for entry in scripted:
            if all(str(filter_map.get(k)) == str(v) for k, v in entry["match"].items()):
                return entry["value"]
        return None

    def _value_from_visual_data(self, visual_id: str, measure_name: str, filters: list[FilterContext]) -> float | None:
        """Unscripted context: total the measure column of the visual's data.
        If there's no data for this context, return None — the snapshot is
        then marked UNAVAILABLE rather than showing an invented number."""
        table = self._data.get("visual_data", {}).get(visual_id)
        if not table:
            return None
        column = f"[{measure_name}]"
        if column not in table["columns"]:
            return None
        data = self.get_visual_data_unchecked(visual_id, filters)
        j = data.columns.index(column)
        values = [row[j] for row in data.rows if row[j] is not None]
        return float(sum(values)) if values else None

    def get_visual_data_unchecked(self, visual_id: str, filters: list[FilterContext]) -> QueryResult:
        visual = next(v for v in self._data["visuals"] if v["id"] == visual_id)
        return self.get_visual_data(visual["report_id"], visual["page_id"], visual_id, filters)
