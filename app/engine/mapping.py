"""
Automatic column mapping between two datasets.

Setting up a reconciliation by hand — "Product Name" on the Excel side is
"Product" in the visual, "Sum of Stock Value" is "Stock Value", Tableau
calls it "SUM(Sales)" — is a big part of the manual effort this platform
exists to remove. The suggestion combines three signals:

  1. column-name similarity after stripping BI aggregation noise and
     expanding common abbreviations,
  2. overlap of actual values (the strongest signal for key columns),
  3. data type compatibility.

It's a suggestion, returned for the user to confirm or edit — the UI
always shows it before running.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from app.engine.values import infer_dtype, normalize_key, parse_number
from app.models.data_reconciliation import (
    ColumnMatch,
    ColumnPair,
    MappingSuggestion,
    ReconciliationConfig,
    ValueComparison,
)
from app.models.dataset import Dataset

_AGG_CALL = re.compile(r"^(sum|avg|average|count|countd|cnt|min|max|attr|agg|median)\s*\((.*)\)$", re.I)
_AGG_PREFIX = re.compile(r"^(sum|count|distinct count|avg|average|min|max|total|median)\s+(of\s+)?", re.I)
_BRACKETED = re.compile(r"^'?[^'\[]*'?\[(.+)\]$")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

SYNONYMS = {
    "qty": "quantity", "quant": "quantity", "units": "quantity", "vol": "volume",
    "amt": "amount", "val": "value", "desc": "description", "descr": "description",
    "no": "number", "num": "number", "nbr": "number", "nr": "number",
    "cust": "customer", "client": "customer", "prod": "product", "item": "product",
    "yr": "year", "fy": "year", "mth": "month", "mon": "month", "period": "month", "qtr": "quarter",
    "dept": "department", "acct": "account", "acc": "account", "gl": "account",
    "cat": "category", "ref": "reference", "dt": "date", "px": "price", "rev": "revenue",
    "cc": "costcentre", "costcenter": "costcentre", "loc": "location", "wh": "warehouse",
}
_NOISE_TOKENS = {"the", "of", "total", "sum", "gbp", "usd", "eur", "£", "$", "€"}
_KEY_NAME_TOKENS = {
    "id", "code", "key", "number", "year", "month", "quarter", "week", "date", "day", "period",
    "sku", "account", "costcentre", "region", "country", "category", "type", "name", "customer",
    "product", "department", "location", "warehouse", "entity", "segment", "channel", "description",
    "invoice", "order", "po", "reference", "ref",
}
# Numeric columns named like these are measures even when they hold whole
# numbers ("Customer Count", "Account Balance", "Number of Orders").
_VALUE_NAME_TOKENS = {
    "count", "amount", "value", "balance", "revenue", "sales", "cost", "price", "quantity", "total", "volume",
    "margin", "profit", "spend", "budget", "forecast", "actual", "variance", "units", "qty", "amt", "net", "gross",
}
_COUNT_OF = re.compile(r"^\s*(number|no\.?|#|count)\s+of\b", re.I)


def canonical_tokens(name: str) -> list[str]:
    s = str(name).strip()
    m = _BRACKETED.match(s)
    if m:
        s = m.group(1)
    m = _AGG_CALL.match(s)
    if m:
        s = m.group(2)
    s = _AGG_PREFIX.sub("", s)
    s = _CAMEL.sub(" ", s)
    tokens = [t for t in re.split(r"[^0-9a-zA-Z£$€]+", s.lower()) if t]
    tokens = [SYNONYMS.get(t, t) for t in tokens]
    meaningful = [t for t in tokens if t not in _NOISE_TOKENS]
    return meaningful or tokens


def name_similarity(a: str, b: str) -> float:
    ta, tb = canonical_tokens(a), canonical_tokens(b)
    if not ta or not tb:
        return 0.0
    ja, jb = " ".join(ta), " ".join(tb)
    if ja == jb:
        return 1.0
    sa, sb = set(ta), set(tb)
    jaccard = len(sa & sb) / len(sa | sb)
    subset = 0.85 if (sa <= sb or sb <= sa) else 0.0
    ratio = SequenceMatcher(None, ja, jb).ratio()
    return max(jaccard, subset, ratio * 0.9)


class _Profile:
    def __init__(self, name: str, values: list[Any], dtype: str):
        self.name = name
        self.dtype = dtype
        non_blank = [v for v in values if v not in (None, "")]
        self.distinct = {normalize_key(v) for v in non_blank[:5000]}
        self.uniqueness = len(self.distinct) / len(non_blank) if non_blank else 0.0
        self.total = sum(parse_number(v) or 0.0 for v in non_blank) if dtype == "number" else 0.0
        self.integral = dtype == "number" and all(
            float(parse_number(v) or 0).is_integer() for v in non_blank[:500]
        )
        raw_tokens = {t for t in re.split(r"[^0-9a-z]+", _CAMEL.sub(" ", str(name)).lower()) if t}
        self.value_named = bool(raw_tokens & _VALUE_NAME_TOKENS) or bool(_COUNT_OF.match(str(name)))
        self.key_named = bool(set(canonical_tokens(name)) & _KEY_NAME_TOKENS)

    def looks_like_key(self) -> bool:
        if self.dtype != "number":
            return True
        if self.value_named:
            return False
        # numeric but key-like: Year=2025, Month=3, Product ID=1043, or a
        # unique whole number per row (invoice / order numbers)
        return self.integral and (self.key_named or (self.uniqueness >= 0.98 and len(self.distinct) >= 10))


def _profiles(ds: Dataset) -> list[_Profile]:
    out = []
    for col in ds.columns:
        values = [r.get(col.name) for r in ds.rows]
        out.append(_Profile(col.name, values, col.dtype or infer_dtype(values)))
    return out


def _value_overlap(a: _Profile, b: _Profile) -> float:
    if not a.distinct or not b.distinct:
        return 0.0
    return len(a.distinct & b.distinct) / min(len(a.distinct), len(b.distinct))


def suggest_mapping(left: Dataset, right: Dataset) -> MappingSuggestion:
    lp, rp = _profiles(left), _profiles(right)
    candidates: list[tuple[float, _Profile, _Profile, str, str]] = []
    for a in lp:
        for b in rp:
            key_pair = a.looks_like_key() and b.looks_like_key()
            name_score = name_similarity(a.name, b.name)
            if key_pair:
                overlap = _value_overlap(a, b)
                score = 0.55 * name_score + 0.45 * overlap
                reason = f"name {name_score:.0%}, shared values {overlap:.0%}"
                if overlap >= 0.8:
                    score = max(score, 0.75)
                role = "key"
            else:
                if (a.dtype == "number") != (b.dtype == "number"):
                    continue
                score = name_score
                reason = f"name {name_score:.0%}"
                if a.total and b.total:
                    ratio = b.total / a.total if a.total else 0
                    if 0.8 <= ratio <= 1.25:
                        score += 0.1
                        reason += ", similar totals"
                role = "value"
            if score >= 0.55:
                candidates.append((min(score, 1.0), a, b, role, reason))

    candidates.sort(key=lambda c: c[0], reverse=True)
    used_l: set[str] = set()
    used_r: set[str] = set()
    keys: list[ColumnMatch] = []
    values: list[ColumnMatch] = []
    for score, a, b, role, reason in candidates:
        if a.name in used_l or b.name in used_r:
            continue
        used_l.add(a.name)
        used_r.add(b.name)
        match = ColumnMatch(left=a.name, right=b.name, score=round(score, 3), role=role, reason=reason)
        (keys if role == "key" else values).append(match)

    warnings: list[str] = []
    if not values:
        warnings.append("No numeric columns could be paired — pick the value columns to compare manually.")
    if not keys:
        warnings.append("No key columns found — the grand totals will be compared instead of row by row.")
    else:
        for side, ds, cols in (("Left", left, [k.left for k in keys]), ("Right", right, [k.right for k in keys])):
            combos = [tuple(normalize_key(r.get(c)) for c in cols) for r in ds.rows]
            dupes = len(combos) - len(set(combos))
            if dupes:
                warnings.append(
                    f"{side} side has {dupes} row(s) sharing the same key — they will be summed before comparing "
                    "(usually a finer granularity on that side)."
                )

    config = ReconciliationConfig(
        key_columns=[ColumnPair(left=k.left, right=k.right) for k in keys],
        value_columns=[ValueComparison(left=v.left, right=v.right, tolerance_abs=0.01) for v in values],
    )
    return MappingSuggestion(
        key_columns=keys,
        value_columns=values,
        unmapped_left=[p.name for p in lp if p.name not in used_l],
        unmapped_right=[p.name for p in rp if p.name not in used_r],
        warnings=warnings,
        config=config,
    )
