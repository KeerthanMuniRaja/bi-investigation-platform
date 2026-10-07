"""
Tableau connector (Tableau Server / Tableau Cloud) — Sections 30-31,
cross-platform expansion.

  Projects                 -> "workspaces"
  Workbooks                -> "reports" (and the semantic model: embedded
                              data sources live inside the workbook)
  Views (sheets/dashboards)-> "pages" — each view is also its one "visual"
  View data                -> GET /views/{id}/data (CSV of the view, with
                              vf_<field>=value filters). This is the data
                              reconciled against Excel.
  Lineage                  -> Metadata API (GraphQL): calculated field
                              formulas, the fields they reference, upstream
                              tables/columns and which sheets use them.

Auth: Personal Access Token (Settings > Personal Access Tokens). The
Metadata API must be enabled on Tableau Server (it always is on Cloud).
Like the Power BI connector, the GraphQL shapes follow the documented
schema but should be verified against your server version.
"""
from __future__ import annotations

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
)
from app.engine.dax import parse_field
from app.models.graph import CapabilityStatus
from app.models.snapshot import FilterContext
from app.sources.files import parse_csv_text, parse_table

METADATA_QUERY = """
query workbookLineage($luid: String!) {
  workbooks(filter: {luid: $luid}) {
    luid
    name
    sheets { id luid name sheetFieldInstances { id name } }
    embeddedDatasources {
      id
      name
      fields {
        id
        name
        __typename
        ... on CalculatedField { formula fields { id name } }
        ... on ColumnField { upstreamColumns { id name table { id name } } }
      }
      upstreamTables { id name fullName connectionType columns { id name remoteType } }
    }
  }
}
"""


class TableauConnector(BIConnector):
    platform_name = "tableau"

    def __init__(
        self,
        server_url: str,
        site_content_url: str = "",
        pat_name: Optional[str] = None,
        pat_secret: Optional[str] = None,
        api_version: str = "3.21",
        session: Optional[requests.Session] = None,
    ):
        if not server_url:
            raise ConnectorError("Tableau server URL is required (e.g. https://prod-uk-a.online.tableau.com).")
        self.server_url = server_url.rstrip("/")
        self.site_content_url = site_content_url or ""
        self.pat_name = pat_name
        self.pat_secret = pat_secret
        self.api_version = api_version
        self.session = session or requests.Session()
        self._token: Optional[str] = None
        self._site_id: Optional[str] = None
        self._metadata: dict[str, dict[str, Any]] = {}
        self._workbook_views: dict[str, list[dict]] = {}

    # ---------------------------------------------------------------- auth
    @property
    def _api(self) -> str:
        return f"{self.server_url}/api/{self.api_version}"

    def _signin(self) -> None:
        if not (self.pat_name and self.pat_secret):
            raise ConnectorError("Tableau personal access token name and secret are required.")
        body = {"credentials": {
            "personalAccessTokenName": self.pat_name,
            "personalAccessTokenSecret": self.pat_secret,
            "site": {"contentUrl": self.site_content_url},
        }}
        resp = self.session.post(f"{self._api}/auth/signin", json=body, headers={"Accept": "application/json"}, timeout=30)
        if resp.status_code >= 400:
            raise ConnectorError(f"Tableau sign-in failed ({resp.status_code}): {resp.text[:300]}")
        creds = resp.json()["credentials"]
        self._token = creds["token"]
        self._site_id = creds["site"]["id"]

    def _headers(self, accept: str = "application/json") -> dict[str, str]:
        if not self._token:
            self._signin()
        return {"X-Tableau-Auth": self._token or "", "Accept": accept}

    def _get(self, path: str, params: Optional[dict] = None, accept: str = "application/json") -> requests.Response:
        for attempt in (1, 2):
            resp = self.session.get(f"{self._api}/sites/{self._site()}{path}", headers=self._headers(accept),
                                    params=params, timeout=60)
            if resp.status_code == 401 and attempt == 1:
                self._token = None  # token expired -> sign in again once
                continue
            if resp.status_code >= 400:
                raise ConnectorError(f"Tableau API GET {path} failed ({resp.status_code}): {resp.text[:300]}")
            return resp
        raise ConnectorError("Tableau authentication failed twice.")

    def _site(self) -> str:
        if not self._site_id:
            self._signin()
        return self._site_id or ""

    def _paged(self, path: str, outer: str, inner: str) -> list[dict]:
        items, page = [], 1
        while True:
            data = self._get(path, params={"pageSize": 1000, "pageNumber": page}).json()
            batch = data.get(outer, {}).get(inner, [])
            items.extend(batch)
            total = int(data.get("pagination", {}).get("totalAvailable", len(items)))
            if len(items) >= total or not batch:
                return items
            page += 1

    # ------------------------------------------------------------ metadata
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            metadata_api=CapabilityStatus.CAPABLE,
            execute_query=CapabilityStatus.UNSUPPORTED,
            pbip_tmdl=CapabilityStatus.NOT_AVAILABLE,
            visual_export=CapabilityStatus.CAPABLE,
            visual_data=CapabilityStatus.CAPABLE,
            notes=["Lineage uses the Tableau Metadata API; it must be enabled on Tableau Server."],
        )

    def get_workspaces(self) -> list[Workspace]:
        return [Workspace(id=p["id"], name=p["name"]) for p in self._paged("/projects", "projects", "project")]

    def get_reports(self, workspace_id: str) -> list[Report]:
        workbooks = self._paged("/workbooks", "workbooks", "workbook")
        return [
            Report(id=w["id"], name=w["name"], workspace_id=workspace_id, model_id=w["id"])
            for w in workbooks if w.get("project", {}).get("id") == workspace_id
        ]

    def _views(self, workbook_id: str) -> list[dict]:
        if workbook_id not in self._workbook_views:
            data = self._get(f"/workbooks/{workbook_id}/views").json()
            self._workbook_views[workbook_id] = data.get("views", {}).get("view", [])
        return self._workbook_views[workbook_id]

    def get_pages(self, report_id: str) -> list[Page]:
        return [Page(id=v["id"], report_id=report_id, name=v["name"]) for v in self._views(report_id)]

    def get_visuals(self, report_id: str, page_id: str) -> list[Visual]:
        view = next((v for v in self._views(report_id) if v["id"] == page_id), None)
        if view is None:
            return []
        measure_ids: list[str] = []
        try:
            meta = self._workbook_metadata(report_id)
            calc_by_name = {f["name"]: f["id"] for ds in meta.get("embeddedDatasources", []) for f in ds.get("fields", [])
                            if f.get("__typename") == "CalculatedField"}
            for sheet in meta.get("sheets", []):
                if sheet.get("luid") == page_id:
                    measure_ids = [calc_by_name[i["name"]] for i in sheet.get("sheetFieldInstances", []) if i["name"] in calc_by_name]
        except ConnectorError:
            pass  # lineage is optional for listing visuals
        # its own id: reusing the page id made the visual node overwrite the page node in the graph
        return [Visual(id=f"{page_id}::view", report_id=report_id, page_id=page_id, title=view["name"],
                       visual_type="tableau_view", measure_ids=measure_ids)]

    def get_semantic_model(self, model_id: str) -> SemanticModel:
        wb = self._get(f"/workbooks/{model_id}").json().get("workbook", {})
        return SemanticModel(id=model_id, name=wb.get("name", model_id), workspace_id=wb.get("project", {}).get("id", ""))

    def _workbook_metadata(self, workbook_id: str) -> dict[str, Any]:
        if workbook_id not in self._metadata:
            resp = self.session.post(
                f"{self.server_url}/api/metadata/graphql",
                json={"query": METADATA_QUERY, "variables": {"luid": workbook_id}},
                headers={**self._headers(), "Content-Type": "application/json"}, timeout=60,
            )
            if resp.status_code >= 400:
                raise ConnectorError(f"Tableau Metadata API failed ({resp.status_code}): {resp.text[:300]}")
            payload = resp.json()
            if payload.get("errors"):
                raise ConnectorError(f"Tableau Metadata API error: {payload['errors'][0].get('message')}")
            workbooks = payload.get("data", {}).get("workbooks", [])
            self._metadata[workbook_id] = workbooks[0] if workbooks else {}
        return self._metadata[workbook_id]

    def get_tables(self, model_id: str) -> list[Table]:
        tables: dict[str, Table] = {}
        for ds in self._workbook_metadata(model_id).get("embeddedDatasources", []):
            for t in ds.get("upstreamTables", []):
                tables[t["id"]] = Table(id=t["id"], model_id=model_id, name=t["name"],
                                        source_ref=f"{t.get('connectionType') or 'source'}://{t.get('fullName') or t['name']}")
        return list(tables.values())

    def get_columns(self, model_id: str, table_id: str) -> list[Column]:
        for ds in self._workbook_metadata(model_id).get("embeddedDatasources", []):
            for t in ds.get("upstreamTables", []):
                if t["id"] == table_id:
                    return [Column(id=c["id"], table_id=table_id, name=c["name"], data_type=(c.get("remoteType") or "string").lower())
                            for c in t.get("columns", [])]
        return []

    def get_measures(self, model_id: str) -> list[Measure]:
        measures = []
        for ds in self._workbook_metadata(model_id).get("embeddedDatasources", []):
            fields = ds.get("fields", [])
            upstream = {f["id"]: [c["id"] for c in f.get("upstreamColumns", []) or []] for f in fields
                        if f.get("__typename") == "ColumnField"}
            calcs = {f["id"] for f in fields if f.get("__typename") == "CalculatedField"}
            for f in fields:
                if f.get("__typename") != "CalculatedField":
                    continue
                refs = [r["id"] for r in f.get("fields", []) or []]
                measures.append(Measure(
                    id=f["id"], model_id=model_id, name=f["name"], dax_expression=f.get("formula") or "",
                    table_id=(ds.get("upstreamTables") or [{"id": ds["id"]}])[0]["id"],
                    depends_on_measure_ids=[r for r in refs if r in calcs],
                    depends_on_column_ids=sorted({c for r in refs for c in upstream.get(r, [])}),
                    dependency_source="engine",
                ))
        return measures

    def get_relationships(self, model_id: str) -> list[Relationship]:
        return []  # joins aren't exposed as relationships in the Metadata API summary used here

    def execute_query(self, model_id: str, dax_query: str) -> QueryResult:
        raise CapabilityError("Tableau has no ad-hoc query endpoint — reconcile view data instead.")

    def get_visual_data(self, report_id: str, page_id: str, visual_id: str, filters: list[FilterContext]) -> QueryResult:
        params = {}
        for f in filters:
            if not f.values:
                continue
            if (f.operator or "in").lower() not in ("in", "equals", "=", "eq"):
                raise CapabilityError("Tableau view filters only support equality / in-list values.")
            try:
                _, name = parse_field(f.field)
            except ValueError:
                name = f.field
            params[f"vf_{name}"] = ",".join(str(v) for v in f.values)
        resp = self._get(f"/views/{page_id}/data", params=params or None, accept="text/csv")
        table = parse_table(parse_csv_text(resp.text), header_row=0, sheet="view")
        return QueryResult(columns=table.columns, rows=[[r.get(c) for c in table.columns] for r in table.rows])
