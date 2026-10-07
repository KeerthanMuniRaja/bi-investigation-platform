"""Connectors (PBIP/TMDL, files, Tableau, Power BI query building), graph
fixes, persistence and the HTTP API used by the web app."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.connectors.files import ExportFile, FileExportConnector
from app.connectors.pbip import PbipConnector, parse_tmdl_table
from app.connectors.tableau import TableauConnector
from app.engine.dax import build_summarize_query, extract_references
from app.engine.extraction import build_full_graph
from app.engine.graph_store import GraphStore
from app.engine.impact import analyze_impact
from app.engine.lineage import trace_upstream
from app.main import app
from app.models.graph import Confidence
from app.models.snapshot import FilterContext

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


# --------------------------------------------------------------- DAX / TMDL
def test_dax_reference_extraction():
    refs = extract_references(
        "DIVIDE([Stock Value], SUMX(Stock, Stock[Quantity] * [Buying Price])) // [Ignored]",
        measure_names=["Stock Value"], columns_by_table={"Stock": {"Quantity", "Buying Price"}}, home_table="Stock",
    )
    assert refs.measures == {"Stock Value"}
    assert refs.columns == {("Stock", "Quantity"), ("Stock", "Buying Price")}


def test_summarize_query_uses_column_types():
    q = build_summarize_query([("Stock", "Product")], [("Stock Value", "[Stock Value]")],
                              [FilterContext(field="Calendar[Year]", values=["2026"])], {("Calendar", "Year"): "int64"})
    assert "TREATAS({2026}, 'Calendar'[Year])" in q and "'Stock'[Product]" in q and '"Stock Value", [Stock Value]' in q


def test_tmdl_multiline_measure():
    t = parse_tmdl_table("table Sales\n\n\tmeasure 'Margin %' =\n\t\t\tDIVIDE(\n\t\t\t\t[Margin],\n\t\t\t\t[Revenue])\n\t\tformatString: 0%\n\n\tcolumn Amount\n\t\tdataType: decimal\n")
    assert t.name == "Sales" and t.measures[0].name == "Margin %"
    assert "DIVIDE" in t.measures[0].expression and "formatString" not in t.measures[0].expression
    assert t.columns[0].data_type == "decimal"


def test_pbip_connector_gives_lineage_without_a_tenant():
    graph = GraphStore()
    build_full_graph(graph, PbipConnector(ROOT / "fixtures" / "sample_pbip"), "pbip")
    lineage = trace_upstream(graph, "stock_value_table")
    names = {s.to_name for s in lineage.steps}
    assert {"Stock Value", "Buying Price", "Quantity", "Stock"} <= names
    # parsed from formula text, so never claimed as verified
    assert lineage.overall_confidence in (Confidence.INFERRED, Confidence.PARTIALLY_VERIFIED)
    assert any(s.confidence == Confidence.INFERRED for s in lineage.steps)
    impact = analyze_impact(graph, "Stock[Quantity]")
    assert impact.affected_report_ids == ["Stock"]


# -------------------------------------------------------------------- graph
def test_resync_does_not_duplicate_edges_and_reports_roll_up():
    from tests.fakes import MockConnector

    g = GraphStore()
    build_full_graph(g, MockConnector(), "WS001")
    n = len(g.all_edges())
    build_full_graph(g, MockConnector(), "WS001")
    assert len(g.all_edges()) == n
    assert analyze_impact(g, "C_BUYPRICE").affected_report_ids == ["R001"]
    # containment is not lineage: the visual's page/report must not appear upstream
    assert all(s.to_id not in ("P001", "R001") for s in trace_upstream(g, "V001").steps)


# --------------------------------------------------------- file / tableau
def test_file_export_connector_treats_tabs_as_pages():
    c = FileExportConnector([ExportFile("F1", "ledger.xlsx", SAMPLES / "finance_stock_ledger_mar2026.xlsx")])
    pages = c.get_pages("F1")
    assert [p.name for p in pages] == ["Stock Ledger"]
    data = c.get_visual_data("F1", "Stock Ledger", "table", [FilterContext(field="Warehouse", values=["Aberdeen"])])
    assert len(data.rows) == 4


class _Resp:
    def __init__(self, status=200, json=None, text=""):
        self.status_code, self._json, self.text = status, json, text

    def json(self):
        return self._json


class _FakeTableau:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url))
        return _Resp(json={"credentials": {"token": "T", "site": {"id": "S1"}}})

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        if url.endswith("/views/V1/data"):
            return _Resp(text="Region,SUM(Sales)\nNorth,\"1,000.50\"\nSouth,200\n")
        if url.endswith("/workbooks/W1/views"):
            return _Resp(json={"views": {"view": [{"id": "V1", "name": "Sales by Region"}]}})
        raise AssertionError(url)


def test_tableau_view_data_with_filters():
    session = _FakeTableau()
    c = TableauConnector("https://tableau.example.com", "finance", "name", "secret", session=session)
    data = c.get_visual_data("W1", "V1", "V1", [FilterContext(field="Orders[Region]", values=["North", "South"])])
    assert data.columns == ["Region", "SUM(Sales)"] and len(data.rows) == 2
    get = [call for call in session.calls if call[0] == "GET"][0]
    assert get[2] == {"vf_Region": "North,South"}
    assert [p.name for p in c.get_pages("W1")] == ["Sales by Region"]


# ---------------------------------------------------------------------- API
@pytest.fixture()
def client():
    return TestClient(app)


def _upload_samples(client) -> dict[str, str]:
    out = {}
    for path in sorted(SAMPLES.iterdir()):
        r = client.post("/files", files={"files": (path.name, path.read_bytes())})
        assert r.status_code == 200, r.text
        out[path.name] = r.json()[0]["file_id"]
    return out


STOCK_VISUAL = {"report_id": "R001", "page_id": "P001", "visual_id": "V001",
                "filters": [{"field": "Calendar[Year]", "operator": "equals", "values": ["2026"]},
                            {"field": "Calendar[Month]", "operator": "equals", "values": ["March"]}]}


def test_full_bi_vs_excel_flow_through_api(client, mock_connection):
    files = _upload_samples(client)
    left = client.post("/datasets", json={"kind": "bi_visual", "connection_id": mock_connection, **STOCK_VISUAL}).json()
    right = client.post("/datasets", json={"kind": "file", "file_id": files["finance_stock_ledger_mar2026.xlsx"]}).json()
    assert right["header_row"] == 3

    run = client.post("/recon/run", json={"left_dataset_id": left["dataset_id"], "right_dataset_id": right["dataset_id"],
                                          "create_investigation": True}).json()
    rec = run["reconciliation"]
    assert rec["status"] == "DIFFERENCES_FOUND"
    assert rec["value_summaries"][0]["difference"] == pytest.approx(-280_378.0)
    assert rec["summary"]["only_left"] == 1 and rec["summary"]["only_right"] == 1 and rec["summary"]["mismatched"] == 1
    assert rec["hypotheses"][0]["category"] == "missing_rows"

    page = client.get(f"/recon/{rec['reconciliation_id']}", params={"status": "MISMATCH"}).json()
    assert page["row_total"] == 1 and page["reconciliation"]["rows"][0]["keys"]["Product / Product Name"] == "ROV Thruster"

    export = client.get(f"/recon/{rec['reconciliation_id']}/export")
    assert export.status_code == 200 and export.content[:2] == b"PK"

    inv = client.get(f"/investigations/{rec['investigation_id']}").json()
    assert inv["hypotheses"] and inv["data_reconciliation_ids"] == [rec["reconciliation_id"]]

    tpl = client.post(f"/recon/{rec['reconciliation_id']}/save-template", json={"name": "Month-end stock"}).json()
    rerun = client.post(f"/recon-templates/{tpl['definition_id']}/run", json={}).json()
    assert rerun["reconciliation"]["definition_id"] == tpl["definition_id"]
    assert client.get("/stats").json()["reconciliations"] == 2


def test_upload_rejects_unsupported_files(client):
    r = client.post("/files", files={"files": ("notes.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 400 and "Unsupported" in r.json()["detail"]


def test_capability_gaps_are_501_not_empty(client):
    pb = client.post("/connections", json={"platform": "pbip", "pbip_dir": str(ROOT / "fixtures" / "sample_pbip")}).json()
    r = client.post(f"/connections/{pb['id']}/visual-data", json={"report_id": "Stock", "page_id": "overview", "visual_id": "avg_price_card"})
    assert r.status_code == 501


def test_state_survives_a_restart(tmp_path):
    from app.store.database import reset_store

    store = reset_store(str(tmp_path / "persist"))
    c = TestClient(app)
    conn = c.post("/connections", json={"platform": "pbip", "pbip_dir": str(ROOT / "fixtures" / "sample_pbip")}).json()
    assert c.post(f"/connections/{conn['id']}/sync").status_code == 200
    reset_store(str(tmp_path / "persist"))  # new process, same data dir
    assert [x["id"] for x in c.get("/connections").json()] == [conn["id"]]
    # the graph is rebuilt on demand after a restart
    assert c.get(f"/connections/{conn['id']}/lineage", params={"node_id": "stock_value_table"}).json()["steps"]
    assert store is not None
