"""Power BI file connector: relationships in one canonical shape, report
filters read with their selections, and visual data computed from stored
tables by following the model's relationships."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.connectors.base import CapabilityError, QueryResult, make_relationship
from app.connectors.model_file import FilterSet, LoadedModel, LoadedPage, ModelFileConnector, REPORT_ID
from app.connectors.pbip import FieldRef, VisualDef, filter_conditions, parse_literal, parse_tmdl_relationships
from app.models.snapshot import FilterContext


# ------------------------------------------------------------ relationships
def test_relationships_are_normalised_whatever_the_source():
    r = make_relationship("R", "m", "Dim", "Key", "Fact", "Key", cardinality="1:M", cross_filter="Both", is_active="false")
    assert (r.from_table_id, r.to_table_id, r.cardinality, r.cross_filter_direction, r.is_active) == \
        ("Fact", "Dim", "many-to-one", "both", False)
    assert make_relationship("R", "m", "A", "k", "B", "k", cardinality="M:M").cardinality == "many-to-many"
    assert make_relationship("R", "m", "A", "k", "B", "k", is_active=0).is_active is False
    assert make_relationship("R", "m", "A", "k", "B", "k", from_cardinality="one", to_cardinality="one").cardinality == "one-to-one"


class _FakeMetadataDB:
    """Stands in for pbixray's SQLite metadata: the Relationship table joined
    to Table/Column, as _pbix_relationships queries it."""

    def __init__(self, rows):
        self.rows = rows

    def query(self, sql):
        import pandas as pd

        if sql.startswith("PRAGMA"):
            return pd.DataFrame({"name": ["ID", "FromTableID", "FromColumnID", "ToTableID", "ToColumnID", "FromCardinality", "ToCardinality"]})
        return pd.DataFrame(self.rows, columns=["ft", "fcol", "tt", "tcol", "active", "fcard", "tcard", "cross"])


def test_pbix_relationships_keep_calculated_tables_and_skip_auto_dates():
    """pbixray's view drops relationships to calculated tables (SystemFlags 2)
    and names calculated-table columns by ExplicitName only; reading the
    metadata directly must keep them — but not the hidden auto date tables."""
    from types import SimpleNamespace

    from app.connectors.model_file import _pbix_relationships

    db = _FakeMetadataDB([
        ("Sales", "date_month", "Calendar", "Date", 1, 2, 1, 1),         # calculated DAX calendar
        ("Sales", "region", "Region", "region", 1, 2, 1, 1),
        ("Toggle", "Value", "Parameter", "Parameter Order", 1, 1, 1, 2),  # field parameter, both ways
        ("Sales", "Owner", "Flash", "display_name", 0, 2, 2, 2),          # inactive many-to-many
        ("Sales", "order_date", "LocalDateTable_1234", "Date", 1, 2, 1, 1),  # auto date: hidden
        ("Sales", None, "Region", "region", 1, 2, 1, 1),                  # dangling column
    ])
    px = SimpleNamespace(_metadata=SimpleNamespace(source=SimpleNamespace(_db=db)))
    warnings: list[str] = []
    rels = _pbix_relationships(px, warnings)
    got = [(r.from_column_id, r.to_column_id, r.cardinality, r.cross_filter_direction, r.is_active) for r in rels]
    assert got == [
        ("Sales[date_month]", "Calendar[Date]", "many-to-one", "single", True),
        ("Sales[region]", "Region[region]", "many-to-one", "single", True),
        ("Toggle[Value]", "Parameter[Parameter Order]", "one-to-one", "both", True),
        ("Sales[Owner]", "Flash[display_name]", "many-to-many", "both", False),
    ]
    assert [r.id for r in rels] == ["REL001", "REL002", "REL003", "REL004"]
    assert warnings == ["1 relationship(s) point at a table or column that no longer exists — skipped."]


def test_tmdl_relationship_reads_active_flag_and_cardinality():
    rels = parse_tmdl_relationships(
        "relationship abc\n\tisActive: false\n\tfromCardinality: one\n\ttoCardinality: many\n"
        "\tcrossFilteringBehavior: bothDirections\n\tfromColumn: Dim.Key\n\ttoColumn: 'Fact Sales'.Key\n")
    r = rels[0].to_relationship("m")
    assert (r.from_table_id, r.to_table_id, r.is_active, r.cross_filter_direction, r.cardinality) == \
        ("Fact Sales", "Dim", False, "both", "many-to-one")


# ------------------------------------------------------------------ filters
def _col(prop):
    return {"Column": {"Expression": {"SourceRef": {"Source": "c"}}, "Property": prop}}


def _lit(v):
    return {"Literal": {"Value": v}}


def _filter(prop, condition, entity="Cal", kind="Column"):
    return {"field": {kind: {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
            "filter": {"From": [{"Name": "c", "Entity": entity}], "Where": [{"Condition": condition}]}}


def test_power_bi_literals():
    assert [parse_literal(x) for x in ["'O''Neil'", "12L", "3.5D", "1.25M", "null", "true", "datetime'2024-01-01T00:00:00'"]] == \
        ["O'Neil", 12, 3.5, 1.25, None, True, "2024-01-01"]


def test_filter_selections_are_read_and_unsupported_ones_reported():
    filters = [
        _filter("Year", {"And": {"Left": {"Comparison": {"ComparisonKind": 2, "Left": _col("Year"), "Right": _lit("2020L")}},
                                 "Right": {"Comparison": {"ComparisonKind": 4, "Left": _col("Year"), "Right": _lit("2024L")}}}}),
        _filter("Month", {"Or": {"Left": {"Comparison": {"ComparisonKind": 0, "Left": _col("Month"), "Right": _lit("'Jan'")}},
                                 "Right": {"Comparison": {"ComparisonKind": 0, "Left": _col("Month"), "Right": _lit("'Feb'")}}}}),
        _filter("Region", {"Not": {"Expression": {"In": {"Expressions": [_col("Region")], "Values": [[_lit("null")]]}}}}),
        _filter("Sales", {"Comparison": {"ComparisonKind": 1, "Left": {"Measure": {"Expression": {"SourceRef": {"Source": "c"}}, "Property": "Sales"}},
                                         "Right": _lit("0L")}}, kind="Measure"),
        {"field": _col("Unselected"), "filter": None},  # in the pane, nothing selected
    ]
    conditions, notes = filter_conditions(filters)
    assert [(c.field.label(), c.operator, c.values) for c in conditions] == [
        ("Cal[Year]", ">=", [2020]), ("Cal[Year]", "<=", [2024]), ("Cal[Month]", "in", ["Jan", "Feb"]), ("Cal[Region]", "not_in", [None])]
    assert len(notes) == 1 and "measure [Sales]" in notes[0]


# ------------------------------------------------- visual data from the file
TABLES = {
    "Sales": QueryResult(columns=["ProductKey", "DateKey", "Amount"],
                         rows=[[1, 20240101, 100.0], [1, 20250101, 50.0], [2, 20240101, 30.0], [3, 20240101, 7.0], [9, 20240101, 1.0]]),
    "Product": QueryResult(columns=["ProductKey", "Category", "CategoryKey"], rows=[[1, "Bikes", "B"], [2, "Bikes", "B"], [3, "Helmets", "H"]]),
    "Category": QueryResult(columns=["CategoryKey", "Group"], rows=[["B", "Hardware"], ["H", "Safety"]]),
    "Calendar": QueryResult(columns=["DateKey", "Year"], rows=[[20240101, 2024], [20250101, 2025]]),
    "Budget": QueryResult(columns=["Category", "Budget"], rows=[["Bikes", 1.0]]),
}


class _StarSchema(ModelFileConnector):
    """ModelFileConnector over in-memory tables instead of a .pbix."""

    def __init__(self, visual: VisualDef, page_filters: FilterSet | None = None):
        super().__init__(Path("model.pbix"))
        self._model = LoadedModel(name="m", file_format="pbix", relationships=[
            make_relationship("R1", "model", "Sales", "ProductKey", "Product", "ProductKey", cardinality="M:1"),
            make_relationship("R2", "model", "Sales", "DateKey", "Calendar", "DateKey", cardinality="M:1"),
            make_relationship("R3", "model", "Product", "CategoryKey", "Category", "CategoryKey", cardinality="M:1"),
            make_relationship("R4", "model", "Sales", "DateKey", "Calendar", "DateKey", cardinality="M:1", is_active=False),
        ], pages=[LoadedPage(id="P", name="Page", visuals=[visual], filters=page_filters or FilterSet())])

    def get_table_data(self, table: str, limit=None) -> QueryResult:
        return TABLES[table]


def _sum_by(*group: tuple[str, str]) -> VisualDef:
    return VisualDef(id="V", title="Sum of Amount", visual_type="table",
                     measures=[FieldRef("Sales", "Amount", "aggregation", "SUM")],
                     group_by=[FieldRef(t, c, "column") for t, c in group])


def _rows(connector, filters=()):
    return connector.get_visual_data(REPORT_ID, "P", "V", list(filters))


def test_visual_grouped_by_a_related_table_follows_the_relationship():
    data = _rows(_StarSchema(_sum_by(("Product", "Category"))))
    assert data.columns == ["Category", "Sum of Amount"]
    # product 9 has no Product row: Power BI shows it under (Blank)
    assert sorted(data.rows, key=str) == sorted([["Bikes", 180.0], ["Helmets", 7.0], [None, 1.0]], key=str)


def test_snowflake_hop_and_filter_on_a_related_table():
    data = _rows(_StarSchema(_sum_by(("Category", "Group"))), [FilterContext(field="Calendar[Year]", values=[2024])])
    assert sorted(data.rows, key=str) == sorted([["Hardware", 130.0], ["Safety", 7.0], [None, 1.0]], key=str)


def test_comparison_and_not_in_filters():
    connector = _StarSchema(_sum_by(("Product", "Category")))
    data = _rows(connector, [FilterContext(field="Calendar[Year]", operator=">=", values=["2025"])])
    assert data.rows == [["Bikes", 50.0]]
    data = _rows(connector, [FilterContext(field="Product[Category]", operator="not_in", values=["Bikes", None])])
    assert data.rows == [["Helmets", 7.0]]


def test_filter_on_an_unrelated_table_is_ignored_with_a_warning():
    data = _rows(_StarSchema(_sum_by(("Product", "Category"))), [FilterContext(field="Budget[Category]", values=["Bikes"])])
    assert len(data.rows) == 3 and data.warnings and "Budget" in data.warnings[0]


def test_grouping_by_an_unrelated_table_is_refused():
    with pytest.raises(CapabilityError, match="No active relationship"):
        _rows(_StarSchema(_sum_by(("Budget", "Category"))))


def test_card_total_and_report_filters_become_default_filters():
    year = filter_conditions([_filter("Year", {"In": {"Expressions": [_col("Year")], "Values": [[_lit("2024L")]]}}, entity="Calendar")])
    connector = _StarSchema(_sum_by(), FilterSet(conditions=year[0], notes=["x: Top N filter — not applied"]))
    visual = connector.get_visuals(REPORT_ID, "P")[0]
    assert [(f.field, f.operator, f.values) for f in visual.default_filters] == [("Calendar[Year]", "in", [2024])]
    assert visual.filter_notes == ["Page filter · x: Top N filter — not applied"]
    assert _rows(connector, visual.default_filters).rows == [[138.0]]


def test_measures_still_need_the_engine():
    visual = VisualDef(id="V", title="KPI", visual_type="card", measures=[FieldRef("Sales", "Total Sales", "measure")])
    with pytest.raises(CapabilityError, match="Power BI engine"):
        _rows(_StarSchema(visual))


# ---------------------------------------------------------------- snapshots
def test_a_snapshot_rebuilds_the_same_model_without_the_file():
    """What was read from a file is saved as JSON; the app then works from it
    (the original can be deleted to save storage)."""
    import json

    from app.connectors.model_file import load_model_file, model_from_snapshot, model_to_snapshot

    model, _ = load_model_file(Path(__file__).resolve().parent.parent / "fixtures" / "sample_pbip")
    restored = model_from_snapshot(json.loads(json.dumps(model_to_snapshot(model))))
    assert [t.model_dump() for t in restored.tables] == [t.model_dump() for t in model.tables]
    assert {k: [c.model_dump() for c in v] for k, v in restored.columns.items()} == {k: [c.model_dump() for c in v] for k, v in model.columns.items()}
    assert [m.model_dump() for m in restored.measures] == [m.model_dump() for m in model.measures]
    assert [r.model_dump() for r in restored.relationships] == [r.model_dump() for r in model.relationships]
    assert restored.pages == model.pages and restored.report_filters == model.report_filters


def test_previews_come_from_the_snapshot_and_full_data_explains_what_is_missing():
    from app.connectors.model_file import FILE_NOT_KEPT, LoadedModel, model_to_snapshot

    snap = model_to_snapshot(LoadedModel(name="Sales", file_format="pbix"),
                             previews={"Sales": {"columns": ["Region", "Amount"], "rows": [["North", 10], ["South", 20], ["East", 5]]}})
    conn = ModelFileConnector(None, "Sales.pbix", snapshot=snap, file_resolver=lambda: (_ for _ in ()).throw(FileNotFoundError("gone")))
    preview = conn.get_table_data("Sales", limit=2)
    assert preview.columns == ["Region", "Amount"] and preview.rows == [["North", 10], ["South", 20]]
    with pytest.raises(CapabilityError) as e:
        conn.get_table_data("Sales")                      # every row: needs the original
    assert str(e.value) == FILE_NOT_KEPT


def test_releasing_the_original_keeps_the_dashboard_working(isolated_store):
    """After reading, the store saves the snapshot; releasing the file deletes
    it (and its cloud copy) but the dashboard still opens from the snapshot."""
    from app.store.database import FileRecord, ModelSnapshot
    from app.connectors.model_file import LoadedModel, model_to_snapshot
    from app.connectors.registry import ConnectionRecord

    store = isolated_store
    deleted: list[str] = []
    store.blobs.delete = deleted.append                    # stand-in for cloud storage
    (store.upload_dir / "FILE0001_x_Sales.pbix").write_bytes(b"pbix bytes")
    store.files["FILE0001"] = FileRecord(file_id="FILE0001", name="Sales.pbix", stored_as="FILE0001_x_Sales.pbix", size=10, kind="model",
                                         blob_key="local/FILE0001_x_Sales.pbix")
    store.connection_records["CONN0001"] = ConnectionRecord(id="CONN0001", name="Sales", platform="pbix", file_ids=["FILE0001"], workspace_id="file")
    store.model_snapshots["CONN0001"] = ModelSnapshot(connection_id="CONN0001", data=model_to_snapshot(LoadedModel(name="Sales", file_format="pbix")))

    assert store.release_model_file("CONN0001")
    record = store.files["FILE0001"]
    assert not record.kept and record.blob_key is None and deleted == ["local/FILE0001_x_Sales.pbix"]
    assert not (store.upload_dir / "FILE0001_x_Sales.pbix").exists()
    with pytest.raises(FileNotFoundError):
        store.resolve_file("FILE0001")
    assert store.connector("CONN0001").model.name == "Sales"   # opens from the snapshot, no file needed
    store.remove_connection("CONN0001")
    assert "CONN0001" not in store.model_snapshots


def test_model_files_are_kept_only_when_storage_is_free(isolated_store, monkeypatch):
    from app.services.recon_service import keep_model_files

    assert keep_model_files(isolated_store) is True          # local disk: keep
    isolated_store.blobs.enabled = True
    assert keep_model_files(isolated_store) is False         # cloud storage: release after reading
    monkeypatch.setenv("BIP_KEEP_MODEL_FILES", "true")
    assert keep_model_files(isolated_store) is True


def test_a_file_that_keeps_crashing_the_reader_gets_a_clear_message(isolated_store):
    """A read killed by the host (out of memory/time) never saves a snapshot;
    after two such attempts the app explains instead of crashing every page."""
    from app.connectors.registry import ConnectionRecord
    from app.store.database import READ_FAILED

    store = isolated_store
    store.connection_records["CONN0001"] = ConnectionRecord(id="CONN0001", name="Big", platform="pbix", file_ids=["FILE0001"],
                                                            workspace_id="file", read_attempts=2)
    with pytest.raises(CapabilityError) as e:
        store.sync("CONN0001", "file")
    assert str(e.value) == READ_FAILED
