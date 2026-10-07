"""DAX reference extraction, DAX explanations and Power Query source
detection on the tricky inputs found in real reports."""
from __future__ import annotations

from app.engine.dax import column_ref, extract_references, parse_field
from app.engine.dax_explain import explain
from app.engine.powerquery import parse_m

COLS = {"Sales": {"Amount", "Qty", "Size [cm]"}, "Date": {"Date", "Year"}}
MEASURES = ["Total Sales", "Margin"]


def refs(expr, home="Sales"):
    return extract_references(expr, MEASURES, COLS, home_table=home)


def test_comment_markers_inside_strings_keep_references():
    r = refs('IF([Margin] > 0, "https://x.com -- not a comment", [Total Sales])')
    assert r.measures == {"Margin", "Total Sales"} and not r.unresolved
    assert refs("SUM(Sales[Amount]) // [Ignored]").measures == set()


def test_case_insensitive_columns_resolve_to_the_model_name():
    r = refs("SUM(sales[amount])")
    assert ("Sales", "Amount") in r.columns and not r.unresolved


def test_keywords_before_brackets_are_not_tables():
    r = refs("VAR x = 1 RETURN [Qty] * x")
    assert ("Sales", "Qty") in r.columns and not r.unresolved


def test_columns_defined_in_the_expression_and_date_hierarchy():
    r = refs('SUMX(ADDCOLUMNS(VALUES(Date[Year]), "BOP", [Total Sales]), [BOP])')
    assert not r.unresolved
    r = refs("'Date'[Date].[Year]", home="Date")
    assert ("Date", "Date") in r.columns and not r.unresolved


def test_escaped_bracket_in_column_names_round_trips():
    r = refs("SUM(Sales[Size [cm]]])")
    assert ("Sales", "Size [cm]") in r.columns
    assert parse_field(column_ref("Sales", "Size [cm]")) == ("Sales", "Size [cm]")


def test_explanations_handle_divide_alternate_and_nested_vars():
    assert "(0 instead when [Margin] is zero)" in explain("DIVIDE([Total Sales], [Margin], 0)")
    # 'Return' inside a bracketed name is not the RETURN keyword
    assert explain("VAR x = [Return Rate] RETURN x * 2") == "First works out x = [Return Rate]. Then returns x * 2."
    assert explain('IF([Margin]>0, "https://x.com", [Total Sales])').endswith('otherwise [Total Sales].')


def test_power_query_navigation_and_commented_sources():
    m = '''let
    // Source = Sql.Database("old-server", "OldDb"),
    Source = Sql.Databases("srv"),
    Db = Source{[Name="dw",Kind="Database"]}[Data],
    T = Db{[Schema="rds",Item="budget"]}[Data]
in
    T'''
    info = parse_m(m, [])
    assert len(info.sources) == 1
    s = info.sources[0]
    assert s.server == "srv" and s.database == "dw" and s.schema == "rds" and s.item == "budget"


def test_power_query_column_access_is_not_a_query_reference():
    info = parse_m('let Source = Table.AddColumn(job_margin_raw, "x", each List.Average([job_margin])) in Source',
                   ["job_margin", "job_margin_raw"])
    assert info.referenced_queries == ["job_margin_raw"]
