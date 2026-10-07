"""
Universal "exported files" connector — works for any BI tool today.

Every BI platform can export what's on screen: Power BI "Export data",
Tableau "Download > Crosstab/Data", Qlik "Export data", Looker, Excel
pivot tables, SSRS, MicroStrategy, Sigma... This connector treats a set
of uploaded exports as a workspace:

  each file   -> a report
  each tab    -> a page
  each tab    -> one visual (the table on that tab)
  columns     -> columns (numeric columns act as the visual's measures)

So the full reconcile / investigate loop is available on day one for
platforms without an API connector, and for users without API access.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
from app.engine.values import infer_dtype, normalize_key
from app.models.graph import CapabilityStatus
from app.models.snapshot import FilterContext
from app.sources.files import ParsedTable, list_sheets, read_table

WORKSPACE_ID = "files"
VISUAL_ID = "table"  # pre-namespacing visual id; still accepted from saved templates


@dataclass
class ExportFile:
    file_id: str
    name: str
    path: Path


class FileExportConnector(BIConnector):
    def __init__(self, files: list[ExportFile], source_platform: str = "generic"):
        self.files = {f.file_id: f for f in files}
        self.platform_name = source_platform or "generic"
        self._tables: dict[tuple[str, str], ParsedTable] = {}

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            metadata_api=CapabilityStatus.PARTIALLY_CAPABLE,
            execute_query=CapabilityStatus.UNSUPPORTED,
            pbip_tmdl=CapabilityStatus.NOT_AVAILABLE,
            visual_export=CapabilityStatus.CAPABLE,
            visual_data=CapabilityStatus.CAPABLE,
            notes=["Exported files carry the data but not the calculation logic — lineage stops at the exported "
                   "columns. Connect the platform's API (or a PBIP project) for measure-level lineage."],
        )

    def _file(self, file_id: str) -> ExportFile:
        if file_id not in self.files:
            raise KeyError(f"file {file_id} is not part of this connection")
        return self.files[file_id]

    def _table(self, file_id: str, sheet: str) -> ParsedTable:
        key = (file_id, sheet)
        if key not in self._tables:
            self._tables[key] = read_table(self._file(file_id).path, sheet)
        return self._tables[key]

    def get_workspaces(self) -> list[Workspace]:
        return [Workspace(id=WORKSPACE_ID, name="Uploaded exports")]

    def get_reports(self, workspace_id: str) -> list[Report]:
        return [Report(id=f.file_id, name=f.name, workspace_id=WORKSPACE_ID, model_id=f.file_id) for f in self.files.values()]

    def get_pages(self, report_id: str) -> list[Page]:
        return [Page(id=self._page_id(report_id, s.name), report_id=report_id, name=s.name)
                for s in list_sheets(self._file(report_id).path)]

    def get_visuals(self, report_id: str, page_id: str) -> list[Visual]:
        sheet = self._sheet(report_id, page_id)
        table = self._table(report_id, sheet)
        numeric = [c for c in table.columns if infer_dtype([r.get(c) for r in table.rows[:500]]) == "number"]
        return [Visual(
            id=self._visual_id(report_id, sheet), report_id=report_id, page_id=self._page_id(report_id, sheet),
            title=sheet, visual_type="table", measure_ids=[self._column_id(report_id, sheet, c) for c in numeric],
        )]

    def get_semantic_model(self, model_id: str) -> SemanticModel:
        return SemanticModel(id=model_id, name=self._file(model_id).name, workspace_id=WORKSPACE_ID)

    def get_tables(self, model_id: str) -> list[Table]:
        f = self._file(model_id)
        return [Table(id=self._table_id(model_id, s.name), model_id=model_id, name=s.name, source_ref=f"file://{f.name}#{s.name}")
                for s in list_sheets(f.path)]

    def get_columns(self, model_id: str, table_id: str) -> list[Column]:
        sheet = table_id.split("::", 1)[1]
        table = self._table(model_id, sheet)
        return [Column(id=self._column_id(model_id, sheet, c), table_id=table_id, name=c,
                       data_type=infer_dtype([r.get(c) for r in table.rows[:500]])) for c in table.columns]

    def get_measures(self, model_id: str) -> list[Measure]:
        return []  # exports contain results, not calculation logic

    def get_relationships(self, model_id: str) -> list[Relationship]:
        return []

    def execute_query(self, model_id: str, dax_query: str) -> QueryResult:
        raise CapabilityError("Exported files can't be queried — pick a tab instead.")

    def get_visual_data(self, report_id: str, page_id: str, visual_id: str, filters: list[FilterContext]) -> QueryResult:
        page_id = self._sheet(report_id, page_id)
        table = self._table(report_id, page_id)
        rows = table.rows
        for f in filters:
            if not f.values:
                continue
            column = _match_column(f.field, table.columns)
            if column is None:
                raise ValueError(f"Filter field '{f.field}' doesn't match any column on tab '{page_id}'")
            wanted = {normalize_key(v) for v in f.values}
            rows = [r for r in rows if normalize_key(r.get(column)) in wanted]
        return QueryResult(columns=table.columns, rows=[[r.get(c) for c in table.columns] for r in rows])

    @staticmethod
    def _table_id(file_id: str, sheet: str) -> str:
        return f"{file_id}::{sheet}"

    # Page/visual ids are graph node ids, so they must be unique across files
    # (every file used to have a visual called "table") and distinct from the
    # table id of the same tab.
    @staticmethod
    def _page_id(file_id: str, sheet: str) -> str:
        return f"{file_id}::{sheet}::page"

    @staticmethod
    def _visual_id(file_id: str, sheet: str) -> str:
        return f"{file_id}::{sheet}::table"

    @staticmethod
    def _sheet(file_id: str, page_id: str) -> str:
        """Page id -> tab name; a bare tab name (ids saved before namespacing) passes through."""
        prefix = f"{file_id}::"
        for suffix in ("::page", "::table"):
            if page_id.startswith(prefix) and page_id.endswith(suffix) and len(page_id) > len(prefix) + len(suffix):
                return page_id[len(prefix):-len(suffix)]
        return page_id

    @classmethod
    def _column_id(cls, file_id: str, sheet: str, column: str) -> str:
        return f"{cls._table_id(file_id, sheet)}[{column}]"


def _match_column(field: str, columns: list[str]) -> Optional[str]:
    """'Calendar[Year]' or 'Year' -> the column called Year (case-insensitive)."""
    name = field[field.rindex("[") + 1:-1] if field.endswith("]") and "[" in field else field
    for c in columns:
        if c.lower() == name.lower():
            return c
    return None
