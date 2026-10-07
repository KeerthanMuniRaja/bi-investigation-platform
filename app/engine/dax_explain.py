"""
Plain-English explanations of DAX measures — deterministic, no AI service.

Parses the expression into a small call tree and describes it with
templates for the functions that make up most real-world measures
(aggregations, iterators, CALCULATE + filter modifiers, time
intelligence, DIVIDE, IF/SWITCH, VAR/RETURN). Anything it doesn't know is
described structurally rather than guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Union

from app.engine.dax import _TOKEN, strip_comments, tokenize


@dataclass
class Call:
    name: str
    args: list["Node"] = field(default_factory=list)


@dataclass
class Raw:
    text: str


Node = Union[Call, Raw]

_CALL_START = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_.]*)\s*\(")


def _skip_opaque(text: str, i: int) -> int:
    """text[i] opens a "string", 'quoted table' or [bracket name]; return the
    index just past its closing delimiter (doubled delimiter = escape)."""
    close = "]" if text[i] == "[" else text[i]
    j = i + 1
    while j < len(text):
        if text[j] == close:
            if j + 1 < len(text) and text[j + 1] == close:
                j += 2
                continue
            return j + 1
        j += 1
    return len(text)


def _split_top(text: str, sep: str = ",") -> list[str]:
    parts, depth, start, i = [], 0, 0, 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'[":
            i = _skip_opaque(text, i)
            continue
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts]


def _matching_paren(text: str, open_index: int) -> int:
    depth, i = 0, open_index
    while i < len(text):
        ch = text[i]
        if ch in "\"'[":
            i = _skip_opaque(text, i)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _strip_parens(text: str) -> str:
    """Drop redundant outer parentheses; one pass, so ((((1)))) isn't quadratic."""
    text = text.strip()
    if not text.startswith("("):
        return text
    match, stack, i = {}, [], 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'[":
            i = _skip_opaque(text, i)
            continue
        if ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            match[stack.pop()] = i
        i += 1
    lo, hi = 0, len(text) - 1
    while lo < hi and text[lo] == "(" and match.get(lo) == hi:
        lo += 1
        hi -= 1
        while lo < hi and text[lo].isspace():
            lo += 1
        while hi > lo and text[hi].isspace():
            hi -= 1
    return text[lo:hi + 1]


def parse(text: str) -> Node:
    text = _strip_parens(text)
    m = _CALL_START.match(text)
    if m:
        open_i = text.index("(", m.start(1))
        close_i = _matching_paren(text, open_i)
        if close_i == len(text) - 1:
            inner = text[open_i + 1:close_i]
            args = [parse(a) for a in _split_top(inner)] if inner.strip() else []
            return Call(name=m.group(1).upper(), args=args)
    return Raw(text=text)


# --------------------------------------------------------------- describe
_AGG = {"SUM": "the total of", "AVERAGE": "the average of", "MIN": "the smallest", "MAX": "the largest",
        "COUNT": "the number of values in", "COUNTA": "the number of non-blank values in",
        "DISTINCTCOUNT": "the number of distinct values of", "COUNTBLANK": "the number of blanks in",
        "MEDIAN": "the median of", "COUNTROWS": "the number of rows in", "DISTINCTCOUNTNOBLANK": "the number of distinct values of"}
_ITER = {"SUMX": "the sum", "AVERAGEX": "the average", "MINX": "the minimum", "MAXX": "the maximum",
         "COUNTX": "the count", "MEDIANX": "the median", "PRODUCTX": "the product", "CONCATENATEX": "the text list"}
_TOTALS = {"TOTALYTD": "year to date", "TOTALMTD": "month to date", "TOTALQTD": "quarter to date"}
_PERIODS = {"DATESYTD": "year to date", "DATESMTD": "month to date", "DATESQTD": "quarter to date",
            "SAMEPERIODLASTYEAR": "the same period last year", "PREVIOUSMONTH": "the previous month",
            "PREVIOUSYEAR": "the previous year", "PREVIOUSQUARTER": "the previous quarter", "PREVIOUSDAY": "the previous day",
            "NEXTMONTH": "the next month", "NEXTYEAR": "the next year", "NEXTQUARTER": "the next quarter",
            "STARTOFMONTH": "the first day of the month", "ENDOFMONTH": "the last day of the month",
            "STARTOFYEAR": "the first day of the year", "ENDOFYEAR": "the last day of the year"}


def _short(text: str, n: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def describe(node: Node, depth: int = 0) -> str:
    if isinstance(node, Raw):
        return _short(node.text)
    if depth > 4:
        return f"{node.name}(…)"
    n, a = node.name, node.args
    d = lambda i: describe(a[i], depth + 1) if i < len(a) else "?"  # noqa: E731

    t = lambda i: _table_text(a[i], depth + 1) if i < len(a) else "?"  # noqa: E731

    if n == "COUNTROWS" and a:
        return f"the number of rows in {t(0)}"
    if n in ("MIN", "MAX") and len(a) == 2:
        return f"the {'smaller' if n == 'MIN' else 'larger'} of {d(0)} and {d(1)}"
    if n in _AGG and a:
        return f"{_AGG[n]} {d(0)}"
    if n in _ITER and len(a) >= 2:
        return f"{_ITER[n]}, over each row of {t(0)}, of {d(1)}"
    if n == "DIVIDE" and len(a) >= 2:
        fallback = f"{d(2)} instead" if len(a) > 2 else "blank instead of an error"
        return f"{d(0)} divided by {d(1)} ({fallback} when {d(1)} is zero)"
    if n == "CALCULATE" and a:
        filters = [describe(x, depth + 1) for x in a[1:]]
        return d(0) + (f", with {'; '.join(_filter_text(f) for f in filters)}" if filters else " (in the current filter context)")
    if n == "CALCULATETABLE" and a:
        return f"the table {d(0)}" + (f" with {'; '.join(describe(x, depth + 1) for x in a[1:])}" if len(a) > 1 else "")
    if n in _TOTALS and len(a) >= 2:
        return f"{d(0)} {_TOTALS[n]} (using {d(1)})"
    if n in _PERIODS:
        return _PERIODS[n]
    if n == "DATEADD" and len(a) >= 3:
        return f"shifted by {d(1)} {d(2).lower()}(s)"
    if n == "PARALLELPERIOD" and len(a) >= 3:
        return f"the parallel {d(2).lower()} {d(1)} period(s) away"
    if n == "DATESINPERIOD" and len(a) >= 4:
        return f"the {d(2)} {d(3).lower()}(s) window from {d(1)}"
    if n == "DATESBETWEEN" and len(a) >= 3:
        return f"dates between {d(1)} and {d(2)}"
    if n == "FILTER" and len(a) >= 2:
        return f"rows of {t(0)} where {d(1)}"
    if n in ("ALL", "REMOVEFILTERS") :
        return f"ignoring filters on {', '.join(describe(x, depth + 1) for x in a) or 'everything'}"
    if n == "ALLEXCEPT" and a:
        return f"ignoring all filters on {d(0)} except {', '.join(describe(x, depth + 1) for x in a[1:])}"
    if n == "ALLSELECTED":
        return f"respecting only the user's selections on {', '.join(describe(x, depth + 1) for x in a) or 'the visual'}"
    if n == "KEEPFILTERS" and a:
        return f"keeping existing filters and adding {d(0)}"
    if n == "USERELATIONSHIP" and len(a) >= 2:
        return f"using the inactive relationship {d(0)} ↔ {d(1)}"
    if n == "CROSSFILTER" and len(a) >= 3:
        return f"changing filter direction between {d(0)} and {d(1)} to {d(2).lower()}"
    if n == "IF" and len(a) >= 2:
        return f"if {d(0)} then {d(1)}" + (f", otherwise {d(2)}" if len(a) > 2 else ", otherwise blank")
    if n == "SWITCH" and a:
        if isinstance(a[0], Call) and a[0].name == "TRUE":
            cases = [f"{describe(a[i], depth + 1)} → {describe(a[i + 1], depth + 1)}" for i in range(1, len(a) - 1, 2)]
            tail = f"; otherwise {describe(a[-1], depth + 1)}" if len(a) % 2 == 0 else ""
            return f"the first matching case: {'; '.join(cases[:4])}{'…' if len(cases) > 4 else ''}{tail}"
        return f"a value chosen by {d(0)} ({(len(a) - 1) // 2} case(s))"
    if n == "SELECTEDVALUE" and a:
        return f"the single selected {d(0)}" + (f" (or {d(1)} when none or several are selected)" if len(a) > 1 else "")
    if n in ("HASONEVALUE", "ISFILTERED", "ISCROSSFILTERED", "ISINSCOPE") and a:
        return {"HASONEVALUE": "exactly one", "ISFILTERED": "a filter on", "ISCROSSFILTERED": "a cross-filter on",
                "ISINSCOPE": "the visual grouping by"}[n] + f" {d(0)}"
    if n == "RELATED" and a:
        return f"the related {d(0)}"
    if n == "LOOKUPVALUE" and a:
        return f"{d(0)} looked up where {', '.join(f'{describe(a[i], depth + 1)} = {describe(a[i + 1], depth + 1)}' for i in range(1, len(a) - 1, 2))}"
    if n == "RANKX" and len(a) >= 2:
        return f"the rank of {d(1)} across {d(0)}"
    if n == "COALESCE" and a:
        return " or, if blank, ".join(describe(x, depth + 1) for x in a)
    if n in ("BLANK",):
        return "blank"
    if n in ("TRUE", "FALSE"):
        return n.lower()
    if n == "FORMAT" and a:
        return f"{d(0)} formatted as text"
    if n in ("VALUES", "DISTINCT") and a:
        return f"the distinct values of {d(0)}"
    if n in ("SUMMARIZE", "SUMMARIZECOLUMNS", "ADDCOLUMNS", "SELECTCOLUMNS", "GROUPBY") and a:
        return f"a summary table of {d(0)}"
    if n in ("ISBLANK", "ISERROR") and a:
        return f"{d(0)} is {'blank' if n == 'ISBLANK' else 'an error'}"
    if n == "IFERROR" and len(a) >= 2:
        return f"{d(0)}, or {d(1)} if that errors"
    if n in ("ROUND", "ROUNDUP", "ROUNDDOWN") and a:
        return f"{d(0)} rounded to {d(1)} decimal(s)" if len(a) > 1 else f"{d(0)} rounded"
    if n in ("ABS", "INT", "TRUNC"):
        return f"{n.lower()} of {d(0)}"
    return f"{n}({', '.join(describe(x, depth + 1) for x in a[:3])}{', …' if len(a) > 3 else ''})"


def _table_text(node: Node, depth: int) -> str:
    """A table argument (FILTER's or an iterator's first): ALL(x) there means
    'every row of x', not the filter-modifier wording CALCULATE needs."""
    if isinstance(node, Call) and node.args:
        inner = ", ".join(describe(x, depth + 1) for x in node.args)
        if node.name in ("ALL", "REMOVEFILTERS", "ALLNOBLANKROW"):
            return f"all of {inner} (ignoring filters)"
        if node.name == "ALLSELECTED":
            return f"the user's selection of {inner}"
        if node.name in ("VALUES", "DISTINCT"):
            return f"each distinct {inner}"
        if node.name == "ALLEXCEPT":
            return f"all of {describe(node.args[0], depth + 1)} (keeping filters on {', '.join(describe(x, depth + 1) for x in node.args[1:])})"
    return describe(node, depth)


def _code_only(expression: str) -> str:
    """Comments dropped; string, quoted-table and bracket contents emptied, so
    names like [Sales IF(x)] or "DIVIDE(" in a label can't trip keyword sniffing."""
    blank = {"cmt": " ", "str": '""', "qt": "''", "br": "[]"}
    return _TOKEN.sub(lambda m: blank.get(m.lastgroup, m.group()), expression or "")


def _var_blocks(text: str) -> tuple[list[tuple[str, str]], str] | None:
    """Top-level VAR name = body ... RETURN expr, or None if the expression
    isn't a VAR block. VAR/RETURN inside parentheses, strings, [names] or a
    nested VAR body don't count."""
    toks = tokenize(text)
    if not toks or toks[0][0] != "word" or toks[0][1].upper() != "VAR":
        return None
    depth, nested, starts, ret = 0, 0, [], None
    for i, (kind, value, pos) in enumerate(toks):
        if kind == "other" and value in "({":
            depth += 1
        elif kind == "other" and value in ")}":
            depth -= 1
        elif kind == "word" and depth == 0 and value.upper() == "VAR":
            if i and toks[i - 1][1] == "=":
                nested += 1          # VAR a = VAR b = .. RETURN b: an inner block
            elif not nested:
                starts.append(i)
        elif kind == "word" and depth == 0 and value.upper() == "RETURN":
            if nested:
                nested -= 1
            else:
                ret = i
                break
    if ret is None:
        return None
    variables = []
    for n, s in enumerate(starts):
        end = toks[starts[n + 1]][2] if n + 1 < len(starts) else toks[ret][2]
        if s + 2 < len(toks) and toks[s + 1][0] == "word" and toks[s + 2][1] == "=":
            variables.append((toks[s + 1][1], text[toks[s + 2][2] + 1:end].strip()))
    return variables, text[toks[ret][2] + len(toks[ret][1]):].strip()


def _filter_text(text: str) -> str:
    return text if text.startswith(("ignoring", "respecting", "keeping", "using", "the ", "shifted", "rows of", "dates", "changing")) \
        else f"only where {text}"


def explain(expression: str) -> str:
    """One or two sentences describing what a measure calculates."""
    text = strip_comments(expression or "").strip()
    if not text:
        return "Empty expression."
    blocks = _var_blocks(text)
    if blocks:
        variables, ret = blocks
        defs = "; ".join(f"{name} = {describe(parse(body))}" for name, body in variables[:5])
        more = "…" if len(variables) > 5 else ""
        return _sentence(f"First works out {defs}{more}. Then returns {describe(parse(ret))}")
    return _sentence(f"Returns {describe(parse(text))}")


def category(expression: str) -> str:
    up = _code_only(expression).upper()
    if any(f + "(" in up for f in (*_TOTALS, *_PERIODS, "DATEADD", "PARALLELPERIOD", "DATESINPERIOD")):
        return "time intelligence"
    if "RANKX(" in up or "TOPN(" in up:
        return "ranking"
    if "DIVIDE(" in up or re.search(r"\]\s*/\s*\[", up):
        return "ratio"
    if "SWITCH(" in up or "IF(" in up:
        return "conditional"
    if re.search(r"\b(SUMX|AVERAGEX|MINX|MAXX|COUNTX)\(", up):
        return "row-by-row calculation"
    if "CALCULATE(" in up:
        return "filtered aggregation"
    if re.search(r"\b(DISTINCTCOUNT|COUNTROWS|COUNT|COUNTA)\(", up):
        return "count"
    if re.search(r"\b(SUM|AVERAGE|MIN|MAX)\(", up):
        return "aggregation"
    return "other"


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > 700:
        text = text[:699] + "…"
    return text if text.endswith((".", "…")) else text + "."
