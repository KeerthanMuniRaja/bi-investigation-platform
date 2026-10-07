"""
DAX helpers:

  - reference extraction: which columns/measures a DAX expression uses.
    This is what gives real measure -> column lineage on live models and
    PBIP/TMDL projects. Parsed
    references are marked INFERRED in the graph; INFO.CALCDEPENDENCY()
    results, when available, are VERIFIED.
  - query building: turning "this visual's measures + group-by columns +
    filter context" into a SUMMARIZECOLUMNS query for Execute Queries, so
    a live Power BI visual's data can be pulled and reconciled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from app.models.snapshot import FilterContext

# One pass over the text, so a "//" inside a string, a "--" inside 'Quoted
# Table' or an apostrophe inside [Column Name] can't be mistaken for a
# comment or delimiter (stripping comments with a regex first got this wrong).
_TOKEN = re.compile(r"""
    (?P<str>"(?:[^"]|"")*"?)
  | (?P<cmt>//[^\n]*|--[^\n]*|/\*.*?(?:\*/|\Z))
  | (?P<qt>'(?:[^']|'')+')
  | (?P<br>\[(?:[^\]]|\]\])*\])
  | (?P<num>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)
  | (?P<word>[A-Za-z_][\w.]*)
  | (?P<ws>\s+)
  | (?P<other>.)
""", re.S | re.X)
_FIELD = re.compile(r"^\s*(?:'((?:[^']|'')+)'|([^\['][^\[]*?))\s*\[((?:[^\]]|\]\])+)\]\s*$", re.S)
_FIELD_LOOSE = re.compile(r"^\s*(?:'((?:[^']|'')+)'|([^\['][^\[]*?))\s*\[(.+)\]\s*$", re.S)  # unescaped ids: T[a]b]

# Words that can sit right before "[" without being a table name (RETURN [x], NOT [Flag]).
_NOT_TABLE = {"return", "var", "not", "in", "and", "or", "asc", "desc", "true", "false", "defer"}
_INTERVALS = {"year", "quarter", "month", "week", "day", "hour", "minute", "second"}
_INTERVAL_FUNCS = {"DATEADD", "DATESINPERIOD", "PARALLELPERIOD", "DATEDIFF", "EDATE", "STARTOFPERIOD", "ENDOFPERIOD"}
_DATA_TYPES = {"integer", "double", "string", "boolean", "currency", "datetime"}
# Iterators: the table argument (index) gives bare [col] references in later arguments their row context.
_ROW_CONTEXT_ARG = {f: 0 for f in (
    "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "COUNTAX", "MEDIANX", "PRODUCTX", "CONCATENATEX", "RANKX", "FILTER",
    "ADDCOLUMNS", "SELECTCOLUMNS", "GENERATE", "GENERATEALL", "STDEVX.P", "STDEVX.S", "VARX.P", "VARX.S", "GEOMEANX",
    "PERCENTILEX.INC", "PERCENTILEX.EXC", "SUMMARIZE", "GROUPBY")} | {"TOPN": 1}
# Table functions that hand their input table on, so SUMX(FILTER(Sales, ..), [Qty]) still iterates Sales.
_PASSTHROUGH_ARG = {f: 0 for f in (
    "", "FILTER", "CALCULATETABLE", "ALL", "ALLNOBLANKROW", "ALLSELECTED", "ALLEXCEPT", "ALLCROSSFILTERED", "VALUES",
    "DISTINCT", "RELATEDTABLE", "KEEPFILTERS", "ADDCOLUMNS")} | {"TOPN": 1, "SAMPLE": 1}
# Functions whose string-literal arguments name new columns ("Rev" in ADDCOLUMNS(.., "Rev", [Sales])).
_COLUMN_CREATORS = {"ADDCOLUMNS", "SELECTCOLUMNS", "SUMMARIZE", "SUMMARIZECOLUMNS", "GROUPBY", "ROW", "DATATABLE",
                    "SUBSTITUTEWITHINDEX", "ADDMISSINGITEMS"}
_CTOR_COLUMN = re.compile(r"value\d*$")  # {..} constructors / GENERATESERIES name their columns [Value], [Value1]..


@dataclass
class DaxReferences:
    columns: set[tuple[str, str]] = field(default_factory=set)  # (table, column)
    measures: set[str] = field(default_factory=set)
    tables: set[str] = field(default_factory=set)               # whole-table references, e.g. COUNTROWS(Sales)
    unresolved: set[str] = field(default_factory=set)


def tokenize(expression: str) -> list[tuple[str, str, int]]:
    """(kind, text, offset) tokens, whitespace and comments dropped."""
    if not isinstance(expression, str):
        return []
    return [(m.lastgroup, m.group(), m.start()) for m in _TOKEN.finditer(expression) if m.lastgroup not in ("ws", "cmt")]


def strip_comments(expression: str) -> str:
    if not isinstance(expression, str):
        return ""
    return _TOKEN.sub(lambda m: " " if m.lastgroup == "cmt" else m.group(), expression)


def strip_noise(expression: str) -> str:
    """Comments removed and string contents blanked (for keyword sniffing)."""
    if not isinstance(expression, str):
        return ""
    return _TOKEN.sub(lambda m: " " if m.lastgroup == "cmt" else '""' if m.lastgroup == "str" else m.group(), expression)


def _unbracket(token: str) -> str:
    return token[1:-1].replace("]]", "]")


def _unquote(token: str) -> str:
    return token[1:-1].replace("''", "'")


@dataclass
class _Frame:
    """One open ( or { while walking tokens — just enough structure to know
    which table an iterator walks and which strings name new columns."""
    func: str                         # upper-cased function name, "" for bare parentheses, "{" for constructors
    arg: int = 0
    items: int = 0                    # tokens/calls seen in the current argument
    first: Optional[tuple[str, str]] = None
    arg_table: Optional[str] = None   # the current argument, if it is (or evaluates to) exactly one model table
    tables: dict[int, str] = field(default_factory=dict)

    def end_arg(self, local_cols: set[str]) -> None:
        if self.items == 1 and self.arg_table:
            self.tables[self.arg] = self.arg_table
        if self.items == 1 and self.first and self.first[0] == "str" and self.func in _COLUMN_CREATORS:
            local_cols.add(self.first[1][1:-1].replace('""', '"').lower())

    def result_table(self) -> Optional[str]:
        idx = _PASSTHROUGH_ARG.get(self.func)
        return None if idx is None else self.tables.get(idx)


def extract_references(
    expression: str,
    measure_names: Iterable[str],
    columns_by_table: dict[str, set[str]],
    home_table: Optional[str] = None,
) -> DaxReferences:
    """Resolve every [x] / Table[x] reference in a DAX expression against
    the model's known measures and columns. DAX names are case-insensitive;
    results always use the model's canonical spelling."""
    toks = tokenize(expression)
    measures = {m.lower(): m for m in measure_names}
    tables = {t.lower(): t for t in columns_by_table}
    cols = {t: {c.lower(): c for c in cs} for t, cs in columns_by_table.items()}
    refs = DaxReferences()
    var_names = {toks[i + 1][1].lower() for i in range(len(toks) - 1)
                 if toks[i][0] == "word" and toks[i][1].lower() == "var" and toks[i + 1][0] == "word"}
    local_cols: set[str] = set()
    constructor = False
    bare: list[tuple[str, list[str]]] = []   # deferred: a local column may be defined after its first use
    stack = [_Frame(func="")]

    def item(kind: str, text: str) -> _Frame:
        top = stack[-1]
        top.items += 1
        if top.items == 1:
            top.first = (kind, text)
        top.arg_table = None
        return top

    def skip_variation(j: int) -> int:  # 'Date'[Date].[Year]: .[Year] is a hierarchy level, not a column
        while j + 1 < len(toks) and toks[j][:2] == ("other", ".") and toks[j + 1][0] == "br":
            j += 2
        return j

    i = 0
    while i < len(toks):
        kind, text, _ = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ("", "", 0)
        low = text.lower()
        if kind == "word" and nxt[:2] == ("other", "("):
            item(kind, text)
            stack.append(_Frame(func=text.upper()))
            constructor |= text.upper() == "GENERATESERIES"
            i += 2
            continue
        if kind in ("qt", "word") and nxt[0] == "br" and not (kind == "word" and low in _NOT_TABLE):
            item(kind, text)
            table = _unquote(text) if kind == "qt" else text
            name = _unbracket(nxt[1])
            real = tables.get(table.lower())
            col = cols[real].get(name.lower()) if real else None
            if col:
                refs.columns.add((real, col))
            elif name.lower() in measures:  # Table[Measure] is legal DAX
                refs.measures.add(measures[name.lower()])
            else:
                refs.unresolved.add(f"{table}[{name}]")
            i = skip_variation(i + 2)
            continue
        if kind == "br":
            item(kind, text)
            # innermost iterator first: SUMX(Returns, [Qty]) is Returns[Qty] even in a measure homed elsewhere
            context = [f.tables[_ROW_CONTEXT_ARG[f.func]] for f in reversed(stack)
                       if f.func in _ROW_CONTEXT_ARG and f.arg > _ROW_CONTEXT_ARG[f.func]
                       and _ROW_CONTEXT_ARG[f.func] in f.tables]
            bare.append((_unbracket(text), context))
            i = skip_variation(i + 1)
            continue
        if kind == "other" and text in "({":
            if text == "{":
                prev = toks[i - 1] if i else ("", "", 0)
                constructor |= not (prev[0] == "word" and prev[1].lower() == "in")
            item(kind, text)
            stack.append(_Frame(func="{" if text == "{" else ""))
        elif kind == "other" and text in ")}" and len(stack) > 1:
            frame = stack.pop()
            frame.end_arg(local_cols)
            stack[-1].arg_table = frame.result_table() if stack[-1].items == 1 else None
        elif kind == "other" and text == ",":
            top = stack[-1]
            top.end_arg(local_cols)
            top.arg, top.items, top.first, top.arg_table = top.arg + 1, 0, None, None
        else:
            top = item(kind, text)
            real = None
            if kind == "qt":
                real = tables.get(_unquote(text).lower())
            elif kind == "word" and low not in var_names and low not in _NOT_TABLE \
                    and not (low in _INTERVALS and top.func in _INTERVAL_FUNCS) \
                    and not (low in _DATA_TYPES and top.func == "DATATABLE"):
                real = tables.get(low)
            if real:
                refs.tables.add(real)
                top.arg_table = real
        i += 1
    while stack:  # unbalanced text: still record what the open frames defined
        stack.pop().end_arg(local_cols)

    for name, context in bare:
        low = name.lower()
        if low in measures:
            refs.measures.add(measures[low])
            continue
        if low in local_cols or (constructor and _CTOR_COLUMN.match(low)):
            continue  # a column the expression itself defines, not a model column
        for table in [*context, home_table]:
            if table and low in cols.get(table, {}):
                refs.columns.add((table, cols[table][low]))
                break
        else:
            owners = [t for t, cs in cols.items() if low in cs]
            if len(owners) == 1:
                refs.columns.add((owners[0], cols[owners[0]][low]))
            else:
                refs.unresolved.add(f"[{name}]")
    # a table whose columns are referenced is already covered by those columns
    refs.tables -= {t for t, _ in refs.columns}
    return refs


# ------------------------------------------------------------ query builder
def quote_table(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def column_ref(table: str, column: str) -> str:
    return f"{quote_table(table)}[{column.replace(']', ']]')}]"


def measure_ref(name: str) -> str:
    return f"[{name.replace(']', ']]')}]"


def parse_field(field_name: str) -> tuple[str, str]:
    """'Calendar[Year]' / "'Date Table'[Year]" / 'T[Size [cm]]]' -> (table, column).
    Node ids are built without escaping, so an unescaped T[a]b] is accepted too."""
    m = _FIELD.match(field_name)
    column = m.group(3).replace("]]", "]") if m else None
    if not m:
        m = _FIELD_LOOSE.match(field_name)
        if not m:
            raise ValueError(f"Filter field '{field_name}' must look like Table[Column]")
        column = m.group(3)
    table = m.group(1).replace("''", "'") if m.group(1) else m.group(2).strip()
    return table, column


_NUMERIC_TYPES = {"int64", "integer", "whole number", "double", "decimal", "currency", "number", "int", "float"}
_BOOLEAN_TYPES = {"boolean", "bool", "true/false", "logical"}


def dax_literal(value: Any, data_type: Optional[str] = None) -> str:
    dtype = (data_type or "").lower()
    if value is None:
        return "BLANK()"  # str(None) would filter on the text "None"
    if isinstance(value, bool):
        return "TRUE()" if value else "FALSE()"
    if dtype in _BOOLEAN_TYPES and str(value).strip().lower() in ("true", "false", "1", "0", "yes", "no"):
        return "TRUE()" if str(value).strip().lower() in ("true", "1", "yes") else "FALSE()"
    if dtype in _NUMERIC_TYPES or (isinstance(value, (int, float)) and not dtype):
        try:
            number = float(value)
            return str(int(number)) if number.is_integer() else repr(number)
        except (TypeError, ValueError):
            pass
    if dtype in ("datetime", "date") and isinstance(value, str):
        return f'DATEVALUE("{value}")'
    return '"' + str(value).replace('"', '""') + '"'


def filter_expression(f: FilterContext, data_type: Optional[str] = None) -> str:
    table, column = parse_field(f.field)
    col = column_ref(table, column)
    values = [dax_literal(v, data_type) for v in f.values]
    op = (f.operator or "in").lower()
    if op in ("in", "equals", "=", "eq"):
        return f"TREATAS({{{', '.join(values)}}}, {col})"
    if op in ("not in", "not_in", "!=", "<>"):
        return f"FILTER(ALL({col}), NOT {col} IN {{{', '.join(values)}}})"
    if op == "between" and len(values) == 2:
        return f"FILTER(ALL({col}), {col} >= {values[0]} && {col} <= {values[1]})"
    if op in (">", ">=", "<", "<=") and values:
        return f"FILTER(ALL({col}), {col} {op} {values[0]})"
    raise ValueError(f"Unsupported filter operator '{f.operator}'")


def build_summarize_query(
    group_by: list[tuple[str, str]],
    measures: list[tuple[str, str]],
    filters: list[FilterContext],
    column_types: Optional[dict[tuple[str, str], str]] = None,
) -> str:
    """measures: (output name, DAX expression such as [Stock Value] or
    SUM('Stock'[Quantity]))."""
    if not group_by and not measures:
        # SUMMARIZECOLUMNS with only filter arguments is invalid DAX — fail with a reason, not a query error
        raise ValueError("Nothing to query: the visual has no group-by columns and no measures")
    column_types = column_types or {}
    args = [column_ref(t, c) for t, c in group_by]
    for f in filters:
        if not f.values:
            continue
        args.append(filter_expression(f, column_types.get(parse_field(f.field))))
    for name, expression in measures:
        args.append(f'"{name.replace(chr(34), chr(34) * 2)}", {expression}')
    if not measures and group_by:
        return "EVALUATE SUMMARIZECOLUMNS(" + ", ".join(args) + ")"
    return "EVALUATE SUMMARIZECOLUMNS(\n    " + ",\n    ".join(args) + "\n)"
