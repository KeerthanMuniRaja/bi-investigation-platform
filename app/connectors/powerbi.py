"""
Live Power BI connector.

Auth: Azure AD service principal, client-credentials flow (MSAL), scope
https://analysis.windows.net/powerbi/api/.default. The service principal
needs to be a member of the target workspace, and the tenant setting
"Dataset Execute Queries REST API" must be enabled for anything that runs
DAX (model metadata via INFO.* functions, visual data, snapshots).

What comes from where (Section 35, "No False Completeness", applied to the
connector itself):

  Workspaces / reports / pages / datasets  -> REST API.
  Tables / columns / measures / relationships
                                           -> Execute Queries running
                                              EVALUATE INFO.VIEW.*() — the
                                              technique DAX Studio and
                                              Tabular Editor use.
  Measure dependencies                     -> INFO.CALCDEPENDENCY() when the
                                              model allows it (VERIFIED);
                                              otherwise parsed from the DAX
                                              text (INFERRED).
  Visual bindings (which measures/columns  -> NOT in the REST API. Read from
  a visual shows)                             a PBIP export (pbip_dir) of
                                              the same report.
  Visual data / snapshots                  -> SUMMARIZECOLUMNS built from the
                                              PBIP bindings + filters, run via
                                              Execute Queries. Or skip PBIP
                                              and reconcile a custom DAX
                                              query ("bi_query" dataset).

Field names returned by INFO.VIEW.* are read tolerantly ("[Name]", "Name"),
but verify against your tenant before relying on this in production — it
has not been run against a live tenant from the environment it was built
in.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

from app.connectors.base import (
    BIConnector,
    CapabilityError,
    Column,
    ConnectorCapabilities,
    ConnectorError,
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
from app.connectors.pbip import PbipConnector, VisualDef, column_id, measure_id
from app.engine.dax import build_summarize_query, extract_references
from app.models.graph import CapabilityStatus, Confidence
from app.models.snapshot import CapturedValue, FilterContext, VisualSnapshot

BASE_URL = "https://api.powerbi.com/v1.0/myorg"
AUTH_SCOPE = ["https://analysis.windows.net/powerbi/api/.default"]


class PowerBIAuthError(ConnectorError):
    pass


def _field(rec: dict[str, Any], name: str, default: Any = None) -> Any:
    """INFO.* results come back keyed '[Name]'; be tolerant of variants."""
    for key in (f"[{name}]", name, name.upper(), f"[{name.upper()}]"):
        if key in rec and rec[key] is not None:
            return rec[key]
    for key, value in rec.items():
        if key.lower().endswith(f"[{name.lower()}]") and value is not None:
            return value
    return default


class PowerBIConnector(BIConnector):
    platform_name = "powerbi"

    def __init__(
        self,
        tenant_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        default_workspace_id: Optional[str] = None,
        pbip_export_dir: Optional[Path] = None,
        session: Optional[requests.Session] = None,
    ):
        # Falls back to env vars so this can run in a container/CI job
        # without code changes: AZURE_TENANT_ID, AZURE_CLIENT_ID,
        # AZURE_CLIENT_SECRET, POWERBI_WORKSPACE_ID, POWERBI_PBIP_DIR.
        self.tenant_id = tenant_id or os.environ.get("AZURE_TENANT_ID")
        self.client_id = client_id or os.environ.get("AZURE_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("AZURE_CLIENT_SECRET")
        self.default_workspace_id = default_workspace_id or os.environ.get("POWERBI_WORKSPACE_ID")
        pbip = pbip_export_dir or os.environ.get("POWERBI_PBIP_DIR")
        self.pbip = PbipConnector(Path(pbip)) if pbip else None
        self.session = session or requests.Session()
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._report_workspace: dict[str, str] = {}
        self._report_names: dict[str, str] = {}
        self._report_model: dict[str, str] = {}
        self._model_workspace: dict[str, str] = {}
        self._info_cache: dict[tuple[str, str], QueryResult] = {}

    # ---------------------------------------------------------------- auth
    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        try:
            import msal
        except ImportError as e:
            raise PowerBIAuthError("msal is required for live Power BI auth: pip install msal") from e
        if not (self.tenant_id and self.client_id and self.client_secret):
            raise PowerBIAuthError(
                "Missing tenant id / client id / client secret. Provide them on the connection or set "
                "AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET. The service principal must also be a "
                "member of the target workspace."
            )
        app = msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
        )
        result = app.acquire_token_for_client(scopes=AUTH_SCOPE)
        if "access_token" not in result:
            raise PowerBIAuthError(f"Token acquisition failed: {result.get('error')}: {result.get('error_description')}")
        self._token = result["access_token"]
        self._token_expires_at = time.time() + result.get("expires_in", 3600)
        return self._token

    def _request(self, method: str, path: str, body: Optional[dict] = None, timeout: int = 60) -> Any:
        headers = {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}
        resp = self.session.request(method, f"{BASE_URL}{path}", headers=headers, json=body, timeout=timeout)
        if resp.status_code >= 400:
            detail = resp.text[:500]
            hint = ""
            if resp.status_code in (401, 403):
                hint = " Check the service principal is a workspace member and the tenant allows service principals."
            if "executeQueries" in path and resp.status_code in (400, 403):
                hint = " Check 'Dataset Execute Queries REST API' is enabled in the tenant settings, and the DAX is valid."
            raise ConnectorError(f"Power BI API {method} {path} failed ({resp.status_code}): {detail}{hint}")
        return resp.json() if resp.content else {}

    def _get(self, path: str) -> Any:
        return self._request("GET", path, timeout=30)

    def _post(self, path: str, body: dict) -> Any:
        return self._request("POST", path, body=body)

    def _workspace_for_report(self, report_id: str) -> str:
        ws = self._report_workspace.get(report_id) or self.default_workspace_id
        if not ws:
            raise ConnectorError("No workspace known for this report — sync the workspace first or set workspace_id.")
        return ws

    def _workspace_for_model(self, model_id: str) -> str:
        ws = self._model_workspace.get(model_id) or self.default_workspace_id
        if not ws:
            raise ConnectorError("No workspace known for this dataset — sync the workspace first or set workspace_id.")
        return ws

    # ------------------------------------------------------------ metadata
    def capabilities(self) -> ConnectorCapabilities:
        with_pbip = CapabilityStatus.CAPABLE if self.pbip else CapabilityStatus.UNSUPPORTED
        notes = []
        if not self.pbip:
            notes.append("Visual bindings need a PBIP export (pbip_dir). Without it, reconcile custom DAX queries "
                         "or exported visual data instead.")
        return ConnectorCapabilities(
            metadata_api=CapabilityStatus.CAPABLE,
            execute_query=CapabilityStatus.CAPABLE,
            pbip_tmdl=CapabilityStatus.CAPABLE if self.pbip else CapabilityStatus.NOT_AVAILABLE,
            visual_export=with_pbip,
            visual_data=with_pbip,
            notes=notes,
        )

    def get_workspaces(self) -> list[Workspace]:
        data = self._get("/groups")
        return [Workspace(id=g["id"], name=g["name"]) for g in data.get("value", [])]

    def get_reports(self, workspace_id: str) -> list[Report]:
        data = self._get(f"/groups/{workspace_id}/reports")
        reports = []
        for r in data.get("value", []):
            model_id = r.get("datasetId", "")
            self._report_workspace[r["id"]] = workspace_id
            self._report_names[r["id"]] = r["name"]
            self._report_model[r["id"]] = model_id
            if model_id:
                self._model_workspace.setdefault(model_id, r.get("datasetWorkspaceId") or workspace_id)
            reports.append(Report(id=r["id"], name=r["name"], workspace_id=workspace_id, model_id=model_id))
        return reports

    def get_pages(self, report_id: str) -> list[Page]:
        workspace_id = self._workspace_for_report(report_id)
        data = self._get(f"/groups/{workspace_id}/reports/{report_id}/pages")
        return [Page(id=p["name"], report_id=report_id, name=p.get("displayName", p["name"])) for p in data.get("value", [])]

    def get_semantic_model(self, model_id: str) -> SemanticModel:
        workspace_id = self._workspace_for_model(model_id)
        data = self._get(f"/groups/{workspace_id}/datasets/{model_id}")
        return SemanticModel(id=data["id"], name=data["name"], workspace_id=workspace_id)

    # -------------------------------------------------- Execute DAX (INFO.*)
    def execute_query(self, model_id: str, dax_query: str) -> QueryResult:
        workspace_id = self._workspace_for_model(model_id)
        body = {"queries": [{"query": dax_query}], "serializerSettings": {"includeNulls": True}}
        data = self._post(f"/groups/{workspace_id}/datasets/{model_id}/executeQueries", body)
        result = data.get("results", [{}])[0]
        if "error" in result:
            raise ConnectorError(f"DAX query failed: {result['error']}")
        table = result.get("tables", [{}])[0]
        rows = table.get("rows", [])
        columns: list[str] = []
        for r in rows:
            for k in r:
                if k not in columns:
                    columns.append(k)
        return QueryResult(columns=columns, rows=[[r.get(c) for c in columns] for r in rows])

    def _info(self, model_id: str, function: str) -> list[dict[str, Any]]:
        key = (model_id, function)
        if key not in self._info_cache:
            self._info_cache[key] = self.execute_query(model_id, f"EVALUATE {function}()")
        qr = self._info_cache[key]
        return [dict(zip(qr.columns, row)) for row in qr.rows]

    def get_tables(self, model_id: str) -> list[Table]:
        out = []
        for rec in self._info(model_id, "INFO.VIEW.TABLES"):
            name = str(_field(rec, "Name"))
            if str(_field(rec, "IsHidden", False)).lower() == "true" and name.startswith(("DateTableTemplate", "LocalDateTable")):
                continue  # auto date/time tables add noise, not lineage
            out.append(Table(id=name, model_id=model_id, name=name, source_ref=None))
        return out

    def get_columns(self, model_id: str, table_id: str) -> list[Column]:
        out = []
        for rec in self._info(model_id, "INFO.VIEW.COLUMNS"):
            if str(_field(rec, "Table")) != table_id:
                continue
            name = str(_field(rec, "Name"))
            if name.startswith("RowNumber-"):
                continue
            out.append(Column(id=column_id(table_id, name), table_id=table_id, name=name,
                              data_type=str(_field(rec, "DataType", "string"))))
        return out

    def _column_types(self, model_id: str) -> dict[tuple[str, str], str]:
        try:
            return {(str(_field(r, "Table")), str(_field(r, "Name"))): str(_field(r, "DataType", ""))
                    for r in self._info(model_id, "INFO.VIEW.COLUMNS")}
        except ConnectorError:
            return {}

    def get_measures(self, model_id: str) -> list[Measure]:
        raw = [(str(_field(r, "Name")), str(_field(r, "Table", "")), str(_field(r, "Expression", "") or ""))
               for r in self._info(model_id, "INFO.VIEW.MEASURES")]
        engine_deps = self._calc_dependencies(model_id)
        columns_by_table: dict[str, set[str]] = {}
        for (table, column) in self._column_types(model_id):
            columns_by_table.setdefault(table, set()).add(column)
        names = [n for n, _, _ in raw]

        measures = []
        for name, table, expression in raw:
            # engine dependencies only when the engine actually reported this
            # measure: an empty/renamed CALCDEPENDENCY result must not turn
            # into "no dependencies, VERIFIED"
            if engine_deps and name in engine_deps:
                deps = engine_deps[name]
                m_ids = sorted(measure_id(n) for n in deps["measures"])
                c_ids = sorted(column_id(t, c) for t, c in deps["columns"])
                t_ids = sorted(deps["tables"])
                source = "engine"
            else:
                refs = extract_references(expression, names, columns_by_table, home_table=table)
                m_ids = sorted(measure_id(n) for n in refs.measures if n != name)
                c_ids = sorted(column_id(t, c) for t, c in refs.columns)
                t_ids = sorted(refs.tables)
                source = "parsed"
            measures.append(Measure(id=measure_id(name), model_id=model_id, name=name, dax_expression=expression,
                                    table_id=table, depends_on_measure_ids=m_ids, depends_on_column_ids=c_ids,
                                    depends_on_table_ids=t_ids, dependency_source=source))
        return measures

    def _calc_dependencies(self, model_id: str) -> Optional[dict[str, dict[str, set]]]:
        try:
            rows = self._info(model_id, "INFO.CALCDEPENDENCY")
        except ConnectorError:
            return None  # not permitted on this model/tenant -> fall back to parsing
        deps: dict[str, dict[str, set]] = {}
        for rec in rows:
            if str(_field(rec, "OBJECT_TYPE", "")).upper() != "MEASURE":
                continue
            entry = deps.setdefault(str(_field(rec, "OBJECT")), {"measures": set(), "columns": set(), "tables": set()})
            ref_type = str(_field(rec, "REFERENCED_OBJECT_TYPE", "")).upper()
            ref_obj = str(_field(rec, "REFERENCED_OBJECT", ""))
            if ref_type == "MEASURE":
                entry["measures"].add(ref_obj)
            elif ref_type in ("COLUMN", "CALC_COLUMN"):
                entry["columns"].add((str(_field(rec, "REFERENCED_TABLE", "")), ref_obj))
            elif ref_type in ("TABLE", "CALC_TABLE"):  # COUNTROWS(Sales), FILTER(Sales, ...)
                entry["tables"].add(ref_obj)
        return deps

    def get_relationships(self, model_id: str) -> list[Relationship]:
        out = []
        for i, rec in enumerate(self._info(model_id, "INFO.VIEW.RELATIONSHIPS")):
            ft, fc = str(_field(rec, "FromTable")), str(_field(rec, "FromColumn"))
            tt, tc = str(_field(rec, "ToTable")), str(_field(rec, "ToColumn"))
            out.append(make_relationship(
                str(_field(rec, "ID", f"REL{i:03d}")), model_id, ft, fc, tt, tc,
                from_cardinality=_field(rec, "FromCardinality", None), to_cardinality=_field(rec, "ToCardinality", None),
                cross_filter=_field(rec, "CrossFilteringBehavior", "OneDirection"), is_active=_field(rec, "IsActive", True),
            ))
        return out

    # ----------------------------------------------------- visuals + data
    def _pbip_report_id(self, report_id: str) -> str:
        if self.pbip is None:
            raise CapabilityError(
                "Visual-level metadata isn't available from the Power BI REST API. Configure pbip_dir with a PBIP "
                "export of this report, or reconcile a custom DAX query / exported visual data instead."
            )
        reports = {r.id for r in self.pbip.get_reports("pbip")}
        name = self._report_names.get(report_id, report_id)
        for candidate in (report_id, name):
            if candidate in reports:
                return candidate
        if len(reports) == 1:
            return next(iter(reports))
        raise CapabilityError(f"No report named '{name}' in the PBIP export ({', '.join(sorted(reports))}).")

    def _visual_def(self, report_id: str, page_id: str, visual_id: str) -> VisualDef:
        pbip_report = self._pbip_report_id(report_id)  # raises the explanatory CapabilityError when no PBIP is set
        for v in self.pbip.visual_definitions(pbip_report, page_id):
            if v.id == visual_id:
                return v
        raise KeyError(f"visual {visual_id} not found on page {page_id} of the PBIP export")

    def get_visuals(self, report_id: str, page_id: str) -> list[Visual]:
        pbip_report = self._pbip_report_id(report_id)
        return [v.model_copy(update={"report_id": report_id}) for v in self.pbip.get_visuals(pbip_report, page_id)]

    def _model_for_report(self, report_id: str) -> str:
        model_id = self._report_model.get(report_id)
        if not model_id:
            workspace_id = self._workspace_for_report(report_id)
            model_id = self._get(f"/groups/{workspace_id}/reports/{report_id}").get("datasetId", "")
            self._report_model[report_id] = model_id
        return model_id

    def build_visual_query(self, report_id: str, page_id: str, visual_id: str, filters: list[FilterContext],
                           totals_only: bool = False) -> tuple[str, str]:
        """(model_id, DAX) that reproduces a visual's data."""
        visual = self._visual_def(report_id, page_id, visual_id)
        if not visual.measures and not visual.group_by:
            raise CapabilityError(f"Visual '{visual.title}' has no data fields (it may be a shape, image or text box).")
        model_id = self._model_for_report(report_id)
        group_by = [] if totals_only else [(f.entity, f.property) for f in visual.group_by]
        measures = [(f.output_name(), f.dax()) for f in visual.measures]
        return model_id, build_summarize_query(group_by, measures, filters, self._column_types(model_id))

    def get_visual_data(self, report_id: str, page_id: str, visual_id: str, filters: list[FilterContext]) -> QueryResult:
        model_id, query = self.build_visual_query(report_id, page_id, visual_id, filters)
        return self.execute_query(model_id, query)

    def get_visual_snapshot(self, report_id: str, page_id: str, visual_id: str, filters: list[FilterContext],
                            snapshot_id: str) -> VisualSnapshot:
        """Measures evaluated at the visual's total level — the exact card/total
        value, correct for non-additive measures (averages, ratios) too."""
        visual = self._visual_def(report_id, page_id, visual_id)
        model_id, query = self.build_visual_query(report_id, page_id, visual_id, filters, totals_only=True)
        result = self.execute_query(model_id, query)
        values = []
        if result.rows:
            for col, value in zip(result.columns, result.rows[0]):
                if isinstance(value, (int, float)):
                    values.append(CapturedValue(metric_name=col.strip("[]"), value=float(value)))
        return VisualSnapshot(
            snapshot_id=snapshot_id, platform=self.platform_name, report_id=report_id, page_id=page_id,
            visual_id=visual_id, visual_title=visual.title,
            measure_id=measure_id(visual.measures[0].property) if visual.measures and visual.measures[0].kind == "measure" else None,
            filters=filters, values=values, coverage={"query": query},
            confidence=Confidence.VERIFIED if values else Confidence.UNAVAILABLE,
        )
