"""Unit tests for value parsing, file reading, mapping, reconciliation,
hypotheses and variance decomposition."""
from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from app.engine.data_reconciliation import reconcile_datasets
from app.engine.datasets import build_dataset
from app.engine.mapping import canonical_tokens, suggest_mapping
from app.engine.values import normalize_key, parse_number
from app.engine.variance import rate_volume
from app.models.data_reconciliation import ColumnPair, ReconciliationConfig, ValueComparison
from app.models.dataset import DatasetSpec, Transform
from app.sources.files import detect_header_row, parse_table, read_table

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def ds(name, columns, rows, transforms=None):
    return build_dataset(name, name, DatasetSpec(kind="inline", transforms=transforms or []), columns, rows)


# ------------------------------------------------------------------ values
@pytest.mark.parametrize("raw,expected", [
    ("£3,615,900", 3615900.0), ("(1,200)", -1200.0), ("1,200-", -1200.0), ("12.5%", 0.125),
    ("3.6M", 3_600_000.0), ("-£1,000", -1000.0), ("USD 12", 12.0), ("1.2bn", 1.2e9), (42, 42.0),
    ("abc", None), ("", None), ("n/a", None), (None, None),
])
def test_parse_number_handles_bi_and_excel_formats(raw, expected):
    assert parse_number(raw) == expected


def test_normalize_key_lines_up_equivalent_values():
    assert normalize_key(2025) == normalize_key(2025.0) == normalize_key("2025") == normalize_key("2025.0")
    assert normalize_key(" Pump ") == normalize_key("pump")
    assert normalize_key("Mar") == normalize_key("March") == normalize_key("march")
    from datetime import datetime
    assert normalize_key(datetime(2026, 3, 1)) == normalize_key("2026-03-01")


# ------------------------------------------------------------------- files
def test_header_detection_skips_titles_and_drops_total_and_note_rows():
    table = read_table(SAMPLES / "finance_stock_ledger_mar2026.xlsx")
    assert table.header_row == 3
    assert table.columns[0] == "Product Name"
    assert len(table.rows) == 6  # Total row and footnote removed
    assert any("total" in w.lower() for w in table.warnings)


def test_merged_cells_are_filled(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Region", "Product", "Sales"])
    ws.append(["North", "A", 10])
    ws.append([None, "B", 20])
    ws.merge_cells("A2:A3")
    path = tmp_path / "merged.xlsx"
    wb.save(path)
    rows = read_table(path).rows
    assert [r["Region"] for r in rows] == ["North", "North"]


def test_csv_with_semicolons_and_detect_header():
    grid = [["Report title", None, None], [None, None, None], ["Code", "Name", "Amount"], ["001", "X", "1.5"]]
    assert detect_header_row(grid) == 2
    table = parse_table(grid)
    assert table.columns == ["Code", "Name", "Amount"]


def test_unpivot_transform_turns_month_columns_into_rows():
    d = ds("wide", ["Account", "Jan", "Feb"], [{"Account": "Rev", "Jan": 1, "Feb": 2}],
           transforms=[Transform(op="unpivot", columns=["Account"], var_name="Month", value_name="Amount")])
    assert d.column_names() == ["Account", "Month", "Amount"]
    assert d.rows == [{"Account": "Rev", "Month": "Jan", "Amount": 1}, {"Account": "Rev", "Month": "Feb", "Amount": 2}]


# ----------------------------------------------------------------- mapping
def test_canonical_tokens_strip_bi_aggregation_noise():
    assert canonical_tokens("SUM(Sales)") == canonical_tokens("Sum of Sales") == canonical_tokens("Sales") == ["sales"]
    assert canonical_tokens("Stock[Qty]") == ["quantity"]


def test_mapping_pairs_differently_named_columns():
    left = ds("L", ["Product", "Month", "Stock Value"], [{"Product": "Pump", "Month": "March", "Stock Value": 1}])
    right = ds("R", ["Product Name", "Period", "Sum of Stock Value (GBP)"], [{"Product Name": "pump", "Period": "Mar", "Sum of Stock Value (GBP)": "£1"}])
    m = suggest_mapping(left, right)
    assert {(k.left, k.right) for k in m.key_columns} == {("Product", "Product Name"), ("Month", "Period")}
    assert [(v.left, v.right) for v in m.value_columns] == [("Stock Value", "Sum of Stock Value (GBP)")]


# ---------------------------------------------------------- reconciliation
def _cfg(tol=0.0, **kw):
    return ReconciliationConfig(key_columns=[ColumnPair(left="K", right="K")],
                                value_columns=[ValueComparison(left="V", right="V", tolerance_abs=tol)], **kw)


def test_reconciliation_classifies_rows_and_bridge_adds_up():
    left = ds("A", ["K", "V"], [{"K": "a", "V": 10}, {"K": "b", "V": 20}, {"K": "c", "V": 5}])
    right = ds("B", ["K", "V"], [{"K": "A", "V": "10"}, {"K": "b", "V": 25}, {"K": "d", "V": 7}])
    rec = reconcile_datasets(left, right, _cfg(), "R1")
    statuses = {r.keys["K"]: r.status for r in rec.rows}
    assert statuses == {"a": "MATCH", "b": "MISMATCH", "c": "ONLY_LEFT", "d": "ONLY_RIGHT"}
    vs = rec.value_summaries[0]
    assert vs.difference == pytest.approx(7.0)  # 42 - 35
    b = vs.bridge
    assert (b.missing_from_right, b.missing_from_left, b.value_differences) == (-5.0, 7.0, 5.0)
    assert b.missing_from_right + b.missing_from_left + b.value_differences == pytest.approx(vs.difference)


def test_tolerance_and_duplicate_aggregation():
    left = ds("A", ["K", "V"], [{"K": "a", "V": 100.0}])
    right = ds("B", ["K", "V"], [{"K": "a", "V": 60.0}, {"K": "a", "V": 40.004}])
    rec = reconcile_datasets(left, right, _cfg(tol=0.01), "R1")
    assert rec.status == "MATCHED"
    assert rec.rows[0].right_rows == 2


def test_no_keys_compares_grand_totals():
    left = ds("Card", ["Total"], [{"Total": 3615900}])
    right = ds("Sheet", ["Amount"], [{"Amount": 3000000}, {"Amount": 615900}])
    cfg = ReconciliationConfig(value_columns=[ValueComparison(left="Total", right="Amount")])
    rec = reconcile_datasets(left, right, cfg, "R1")
    assert rec.status == "MATCHED"


def test_filters_restrict_rows():
    left = ds("A", ["K", "Y", "V"], [{"K": "a", "Y": 2025, "V": 1}, {"K": "a", "Y": 2026, "V": 2}])
    right = ds("B", ["K", "V"], [{"K": "a", "V": 2}])
    cfg = ReconciliationConfig(key_columns=[ColumnPair(left="K", right="K")],
                               value_columns=[ValueComparison(left="V", right="V")], left_filters={"Y": ["2026"]})
    assert reconcile_datasets(left, right, cfg, "R1").status == "MATCHED"


def test_invalid_config_is_rejected():
    left = ds("A", ["K", "V"], [])
    with pytest.raises(ValueError, match="not a column"):
        reconcile_datasets(left, left, ReconciliationConfig(value_columns=[ValueComparison(left="X", right="V")]), "R1")


# -------------------------------------------------------------- hypotheses
def _top(rec):
    return rec.hypotheses[0]


def test_hypothesis_unit_mismatch():
    left = ds("A", ["K", "V"], [{"K": k, "V": v} for k, v in (("a", 12000), ("b", 5500), ("c", 800))])
    right = ds("B", ["K", "V"], [{"K": k, "V": v / 1000} for k, v in (("a", 12000), ("b", 5500), ("c", 800))])
    top = _top(reconcile_datasets(left, right, _cfg(), "R1"))
    assert top.category == "units" and "scale_right = 1000" in top.suggested_action


def test_hypothesis_missing_rows_fully_explain_variance_is_verified():
    left = ds("A", ["K", "V"], [{"K": "a", "V": 1}, {"K": "b", "V": 2}])
    right = ds("B", ["K", "V"], [{"K": "a", "V": 1}])
    top = _top(reconcile_datasets(left, right, _cfg(), "R1"))
    assert top.category == "missing_rows" and top.confidence.value == "VERIFIED"


def test_hypothesis_key_format():
    left = ds("A", ["K", "V"], [{"K": "00123", "V": 1}, {"K": "00456", "V": 2}])
    right = ds("B", ["K", "V"], [{"K": "123", "V": 1}, {"K": "456", "V": 2}])
    cats = [h.category for h in reconcile_datasets(left, right, _cfg(), "R1").hypotheses]
    assert "key_format" in cats


def test_hypothesis_sign_flip_and_reclassification():
    left = ds("A", ["K", "V"], [{"K": "a", "V": 100}, {"K": "b", "V": -40}])
    right = ds("B", ["K", "V"], [{"K": "a", "V": -100}, {"K": "b", "V": 40}])
    assert _top(reconcile_datasets(left, right, _cfg(), "R1")).category == "sign"

    left = ds("A", ["K", "V"], [{"K": "a", "V": 100}, {"K": "b", "V": 50}])
    right = ds("B", ["K", "V"], [{"K": "a", "V": 80}, {"K": "b", "V": 70}])
    assert "reclassification" in [h.category for h in reconcile_datasets(left, right, _cfg(), "R1").hypotheses]


def test_hypothesis_period_cutoff():
    rows = [{"Month": m, "V": 10} for m in ("2026-01", "2026-02", "2026-03")]
    left = ds("A", ["Month", "V"], rows)
    right = ds("B", ["Month", "V"], rows[:2])
    cfg = ReconciliationConfig(key_columns=[ColumnPair(left="Month", right="Month")], value_columns=[ValueComparison(left="V", right="V")])
    assert "timing" in [h.category for h in reconcile_datasets(left, right, cfg, "R1").hypotheses]


def test_matched_sources_say_so():
    left = ds("A", ["K", "V"], [{"K": "a", "V": 1}])
    rec = reconcile_datasets(left, left, _cfg(), "R1")
    assert rec.status == "MATCHED" and _top(rec).category == "agreement"


# ---------------------------------------------------------------- variance
def test_rate_volume_explains_spec_variance_exactly():
    from tests.fakes import MockConnector
    from app.engine.datasets import rows_from_query_result
    from app.models.snapshot import FilterContext

    c = MockConnector()
    sets = []
    for year in ("2025", "2026"):
        cols, rows = rows_from_query_result(c.get_visual_data("R001", "P001", "V001", [FilterContext(field="Calendar[Year]", values=[year])]))
        sets.append(ds(year, cols, rows))
    result = rate_volume(sets[0], sets[1], ["Product"], volume_column="Quantity", value_column="Stock Value")
    assert result.total_change == pytest.approx(28_228.0)
    assert result.rate_effect + result.volume_effect + result.interaction + result.new_or_lost == pytest.approx(28_228.0)
    assert result.rate_effect == pytest.approx(8000 - 20000 + 8000 + 14100)
