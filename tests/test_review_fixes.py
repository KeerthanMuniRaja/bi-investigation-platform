"""Regression tests for defects found in the full-codebase review: number
parsing, file reading, reconciliation edge cases, Excel export safety,
storage/API consistency and the interactive lineage graph."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.connectors.base import QueryResult
from app.engine.data_reconciliation import reconcile_datasets
from app.engine.datasets import build_dataset, rows_from_query_result
from app.engine.excel_export import export_data_reconciliation
from app.engine.extraction import build_full_graph
from app.engine.graph_store import GraphStore
from app.engine.mapping import suggest_mapping
from app.engine.neighborhood import neighborhood
from app.engine.values import normalize_key, parse_number
from app.main import app
from app.models.data_reconciliation import ColumnPair, ReconciliationConfig, ValueComparison
from app.models.dataset import DatasetSpec, Transform
from app.models.graph import Confidence
from app.sources.files import parse_table, read_table
from tests.fakes import MockConnector

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


def _ds(name, columns, rows):
    return build_dataset(name, name, DatasetSpec(kind="inline", name=name), columns, [dict(zip(columns, r)) for r in rows])


# ------------------------------------------------------------------ values
@pytest.mark.parametrize("raw, expected", [
    ("1.234,56", 1234.56), ("€ 1.234,56", 1234.56), ("1,5", 1.5), ("1 234,56", 1234.56), ("1\u202f234", 1234),
    ("£(1,234)", -1234), ("-(1,000)", -1000), ("\u22121,234", -1234), ("1,234 CR", -1234), ("1,234 DR", 1234),
    ("1,234", 1234), ("1.234.567", 1234567), ("1,23,456", 123456), ("3,6M", 3.6e6), ("12.5%", 0.125),
    ("1e309", None), ("1e99999999", None), ("9" * 400, None), ("n/a", None), ("–", None),
])
def test_number_formats(raw, expected):
    got = parse_number(raw)
    assert (got is None and expected is None) or got == pytest.approx(expected)


def test_decimal_keys_meet_ints():
    from decimal import Decimal
    assert normalize_key(Decimal("2025.0")) == normalize_key(2025) == "2025"


# ------------------------------------------------------------------- files
def test_cp1252_csv_with_even_byte_count_is_not_read_as_utf16(tmp_path):
    for extra in ("", "x"):
        path = tmp_path / f"f{extra}.csv"
        path.write_bytes(f'Product,Amount\nPump,"£1,234"\nValve{extra},£500\n'.encode("cp1252"))
        t = read_table(path)
        assert t.columns == ["Product", "Amount"] and len(t.rows) == 2


def test_merged_title_vertical_merge_and_two_level_header(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Stock Report - March 2026", None, None])
    ws.merge_cells("A1:C1")
    ws.append([])
    ws.append(["Region", "Product", "Budget"])
    ws.append(["North", "Pump", 900])
    ws.append([None, "Valve", None])
    ws.merge_cells("A4:A5")  # label merged down: filled
    ws.merge_cells("C4:C5")  # number merged down: counted once
    wb.save(tmp_path / "m.xlsx")
    t = read_table(tmp_path / "m.xlsx")
    assert t.header_row == 2 and t.columns == ["Region", "Product", "Budget"]
    assert [r["Region"] for r in t.rows] == ["North", "North"] and sum(r["Budget"] or 0 for r in t.rows) == 900

    wb = Workbook()
    ws = wb.active
    ws.append(["Product", "Sales", None])
    ws.append([None, 2024, 2025])
    ws.merge_cells("B1:C1")
    ws.append(["Pump", 100, 120])
    wb.save(tmp_path / "two.xlsx")
    t = read_table(tmp_path / "two.xlsx")
    assert t.columns == ["Product", "Sales 2024", "Sales 2025"] and t.rows == [{"Product": "Pump", "Sales 2024": 100, "Sales 2025": 120}]


def test_total_rows_anywhere_but_not_company_names():
    grid = [["Region", "Product", "Value"], ["North", "A", 100], ["North Total", None, 100], ["Total SA", "C", 5],
            ["Grand Total", None, 105], ["South", "Total", 1], ["A", "B", 3, ], ["Result", None, 9]]
    t = parse_table(grid)
    assert [r["Region"] for r in t.rows] == ["North", "Total SA", "A"]
    assert "'North Total'" in t.warnings[-1]


def test_duplicate_headers_never_collide():
    t = parse_table([["A", "A", "A (2)"], [1, 2, 3]])
    assert t.columns == ["A", "A (2)", "A (2) (2)"] and t.rows == [{"A": 1, "A (2)": 2, "A (2) (2)": 3}]


def test_query_columns_with_the_same_short_name_are_kept_apart():
    cols, rows = rows_from_query_result(QueryResult(columns=["Sales[Amount]", "Budget[Amount]"], rows=[[10, 12]]))
    assert cols == ["Amount", "Budget[Amount]"] and rows == [{"Amount": 10, "Budget[Amount]": 12}]


def test_rename_onto_an_existing_column_is_refused():
    with pytest.raises(ValueError, match="duplicate"):
        build_dataset("x", "x", DatasetSpec(kind="inline", transforms=[Transform(op="rename", mapping={"Amt": "Amount"})]),
                      ["Amt", "Amount"], [{"Amt": 1, "Amount": 2}])


# ---------------------------------------------------------- reconciliation
def _cfg(key=True, tol=0.0):
    return ReconciliationConfig(key_columns=[ColumnPair(left="K", right="K")] if key else [],
                                value_columns=[ValueComparison(left="V", right="V", tolerance_abs=tol)])


def test_nothing_compared_is_not_agreement():
    rec = reconcile_datasets(_ds("L", ["K", "V"], []), _ds("R", ["K", "V"], []), _cfg(), "R1")
    assert rec.status == "DIFFERENCES_FOUND" and rec.confidence == Confidence.UNAVAILABLE
    assert not any(h.title == "Sources agree" for h in rec.hypotheses)


def test_float_drift_is_not_a_mismatch():
    left = _ds("L", ["K", "V"], [["a", 0.1] for _ in range(2000)])
    right = _ds("R", ["K", "V"], [["a", round(0.1 * 2000, 2)]])
    assert reconcile_datasets(left, right, _cfg(), "R1").status == "MATCHED"


def test_excel_style_rounding_and_numeric_period_order():
    rec = reconcile_datasets(_ds("L", ["K", "V"], [["a", 2.5], ["b", 1234.5]]), _ds("R", ["K", "V"], [["a", 3], ["b", 1235]]), _cfg(), "R1")
    assert any(h.category == "rounding" for h in rec.hypotheses)
    months = [[m, 10] for m in range(1, 13)]
    rec = reconcile_datasets(_ds("L", ["Month", "V"], months), _ds("R", ["Month", "V"], [r for r in months if r[0] != 9]),
                             ReconciliationConfig(key_columns=[ColumnPair(left="Month", right="Month")],
                                                  value_columns=[ValueComparison(left="V", right="V")]), "R2")
    assert not any(h.category == "timing" for h in rec.hypotheses)  # month 9 isn't the latest


def test_key_format_amount_has_the_same_sign_as_the_variance():
    rec = reconcile_datasets(_ds("L", ["K", "V"], [["00123", 100], ["x", 5]]), _ds("R", ["K", "V"], [["123", 90], ["x", 5]]), _cfg(), "R1")
    kf = next(h for h in rec.hypotheses if h.category == "key_format")
    assert kf.affected_amount == pytest.approx(rec.value_summaries[0].difference) == pytest.approx(-10)
    assert not any(h.category == "reclassification" for h in rec.hypotheses)


def test_value_like_names_are_values_and_unique_ids_are_keys():
    left = _ds("L", ["Region", "Customer Count", "Account Balance"], [["N", 10, 500], ["S", 12, 700]])
    m = suggest_mapping(left, left)
    assert [k.left for k in m.key_columns] == ["Region"] and {v.left for v in m.value_columns} == {"Customer Count", "Account Balance"}
    ids = _ds("L", ["Invoice", "Amount"], [[10001 + i, 5.5 * i] for i in range(30)])
    m = suggest_mapping(ids, ids)
    assert [k.left for k in m.key_columns] == ["Invoice"] and [v.left for v in m.value_columns] == ["Amount"]


def test_export_survives_control_characters_and_never_writes_formulas(tmp_path):
    rec = reconcile_datasets(_ds("L", ["K", "V"], [["bad\x07key", 1], ["=1+1", 2]]), _ds("R", ["K", "V"], [["bad\x07key", 2], ["=1+1", 2]]), _cfg(), "R1")
    path = tmp_path / "x.xlsx"
    export_data_reconciliation(rec, str(path))
    cells = [c for ws in load_workbook(path).worksheets for row in ws.iter_rows() for c in row]
    assert not any(c.data_type == "f" for c in cells)
    assert any(c.value == "=1+1" for c in cells)


# ------------------------------------------------------------ storage / API
@pytest.fixture()
def client():
    return TestClient(app)


def _upload(client, name):
    r = client.post("/files", files={"files": (name, (SAMPLES / name).read_bytes())})
    assert r.status_code == 200, r.text
    return r.json()[0]["file_id"]


def test_file_in_use_cannot_be_deleted_and_bad_upload_rolls_back(client):
    fid = _upload(client, "powerbi_export_stock_value.csv")
    conn = client.post("/connections", json={"platform": "files", "file_ids": [fid]}).json()
    assert client.delete(f"/files/{fid}").status_code == 409
    client.delete(f"/connections/{conn['id']}")
    assert client.delete(f"/files/{fid}").status_code == 200
    before = len(client.get("/files").json())
    r = client.post("/files", files=[("files", ("a.csv", b"x,y\n1,2\n")), ("files", ("b.xlsx", b"not a workbook"))])
    assert r.status_code == 400 and len(client.get("/files").json()) == before


def test_connection_setup_errors_are_400(client):
    assert client.post("/connections", json={"platform": "pbip", "pbip_dir": "C:/does/not/exist"}).status_code == 400
    assert client.post("/connections", json={"platform": "pbix"}).status_code == 400


def test_deletes_clean_up_references_and_template_remembers_new_file(client):
    a, b = _upload(client, "tableau_sales_by_region.csv"), _upload(client, "finance_sales_by_region.xlsx")
    left = client.post("/datasets", json={"kind": "file", "file_id": a}).json()
    right = client.post("/datasets", json={"kind": "file", "file_id": b}).json()
    rec = client.post("/recon/run", json={"left_dataset_id": left["dataset_id"], "right_dataset_id": right["dataset_id"],
                                          "create_investigation": True}).json()["reconciliation"]
    tpl = client.post(f"/recon/{rec['reconciliation_id']}/save-template", json={"name": "Sales"}).json()
    newer = _upload(client, "finance_sales_by_region.xlsx")
    client.post(f"/recon-templates/{tpl['definition_id']}/run", json={"right_file_id": newer})
    assert client.get(f"/recon-templates/{tpl['definition_id']}").json()["right"]["file_id"] == newer

    inv_id = rec["investigation_id"]
    assert client.delete(f"/investigations/{inv_id}").status_code == 200
    assert client.get(f"/recon/{rec['reconciliation_id']}").json()["reconciliation"]["investigation_id"] is None
    assert client.delete(f"/recon/{rec['reconciliation_id']}").status_code == 200
    assert rec["reconciliation_id"] not in client.get(f"/recon-templates/{tpl['definition_id']}").json()["run_ids"]
    assert client.get("/recon/DREC9999").status_code == 404 and "not found" in client.get("/recon/DREC9999").json()["detail"]


def test_concurrent_notes_are_all_kept(client, mock_connection):
    inv = client.post("/investigations", json={"question": "why?"}).json()
    threads = [threading.Thread(target=lambda i=i: client.post(f"/investigations/{inv['investigation_id']}/notes", json={"text": f"n{i}"}))
               for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(client.get(f"/investigations/{inv['investigation_id']}").json()["notes"]) == 8


def test_investigation_graph_targets_are_validated(client, mock_connection):
    assert client.post("/investigations", json={"question": "q", "connection_id": "CONN9999", "lineage_root_id": "x"}).status_code == 404
    client.post(f"/connections/{mock_connection}/sync", params={"workspace_id": "WS001"})
    r = client.post("/investigations", json={"question": "q", "connection_id": mock_connection, "lineage_root_id": "nope"})
    assert r.status_code == 404


def test_bounds_on_paging(client):
    assert client.get("/recon/X?limit=-1").status_code == 422 and client.get("/recon/X?offset=-2").status_code == 422


# --------------------------------------------------- interactive lineage
def test_neighborhood_levels_counts_and_expansion(client):
    graph = GraphStore()
    build_full_graph(graph, MockConnector(), "WS001")
    measure = next(n for n in graph.all_nodes() if n.type.value == "MEASURE" and graph.incoming(n.id) and graph.outgoing(n.id))
    g = neighborhood(graph, measure.id, up=1, down=1)
    levels = {n["id"]: n["level"] for n in g["nodes"]}
    assert levels[measure.id] == 0 and min(levels.values()) == -1 and max(levels.values()) >= 1
    root = next(n for n in g["nodes"] if n["id"] == measure.id)
    assert root["up_count"] >= 1 and root["down_count"] >= 1
    deeper = neighborhood(graph, measure.id, up=4, down=0)
    assert len(deeper["nodes"]) >= len([n for n in g["nodes"] if n["level"] <= 0])

    pb = client.post("/connections", json={"platform": "pbip", "pbip_dir": str(ROOT / "fixtures" / "sample_pbip")}).json()
    client.post(f"/connections/{pb['id']}/sync")
    r = client.get(f"/connections/{pb['id']}/lineage-graph", params={"node_id": "stock_value_table", "up": 3, "down": 1})
    assert r.status_code == 200 and r.json()["nodes"] and r.json()["edges"]
    one = r.json()["nodes"][-1]
    e = client.get(f"/connections/{pb['id']}/lineage-graph/expand", params={"node_id": one["id"], "direction": "up", "level": one["level"]})
    assert e.status_code == 200
    assert client.get(f"/connections/{pb['id']}/lineage-graph", params={"node_id": "nope"}).status_code == 404
    assert client.get(f"/connections/{pb['id']}/lineage-graph", params={"node_id": "stock_value_table", "up": 99}).status_code == 422


def test_root_hands_over_to_the_web_app():
    """http://localhost:8000/#/reconcile must open the Next.js app at its
    readable address; the first, hash-routed interface is gone."""
    from app.main import app

    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert 'const WEB = "http://localhost:3000";' in page.text and "__WEB_URL__" not in page.text
    assert "location.replace(url)" in page.text
    assert client.get("/legacy").status_code == 404
    assert client.get("/static/app.js").status_code == 404
