"""
Tabular reconciliation engine: compare any two Datasets row by row.

This is the workhorse behind "BI visual vs finance Excel", "Tableau vs
Power BI", "this month's export vs last month's", etc. It:

  - normalizes keys and numbers (formatting noise is not a variance),
  - aggregates when one side is more granular than the other,
  - classifies every key as MATCH / MISMATCH / ONLY_LEFT / ONLY_RIGHT
    with per-column tolerances,
  - builds a variance bridge — how much of the total difference comes
    from rows missing on each side vs values that differ — so the answer
    to "why don't these totals agree?" is quantified, not guessed,
  - hands off to the driver and hypothesis engines for the "why".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from app.engine import hypotheses as hypothesis_engine
from app.engine.values import is_blank, normalize_key, parse_number
from app.engine.variance import dimension_drivers
from app.models.data_reconciliation import (
    CellDiff,
    DataReconciliation,
    ReconciliationConfig,
    ReconciliationSummary,
    ResultRow,
    ValueSummary,
    VarianceBridge,
)
from app.models.dataset import Dataset
from app.models.graph import Confidence

_EPS = 1e-9
ALL_KEY = ("__all__",)


@dataclass
class _Group:
    display: list[Any]
    values: list[Optional[float]]
    count: int = 1


@dataclass
class SideStats:
    name: str
    rows_in: int = 0
    rows_after_filter: int = 0
    groups: dict[tuple, _Group] = field(default_factory=dict)
    duplicate_rows: int = 0
    unparsed: list[int] = field(default_factory=list)
    unparsed_examples: list[list[str]] = field(default_factory=list)  # per value column


def validate_config(left: Dataset, right: Dataset, config: ReconciliationConfig) -> None:
    lcols, rcols = set(left.column_names()), set(right.column_names())
    problems = []
    for pair in [*config.key_columns, *config.value_columns]:
        if pair.left not in lcols:
            problems.append(f"'{pair.left}' is not a column of {left.name}")
        if pair.right not in rcols:
            problems.append(f"'{pair.right}' is not a column of {right.name}")
    for col in config.left_filters:
        if col not in lcols:
            problems.append(f"filter column '{col}' is not a column of {left.name}")
    for col in config.right_filters:
        if col not in rcols:
            problems.append(f"filter column '{col}' is not a column of {right.name}")
    if not config.value_columns:
        problems.append("at least one value column pair is required")
    if problems:
        raise ValueError("Invalid reconciliation config: " + "; ".join(problems))


def _collect(ds: Dataset, key_cols: list[str], value_cols: list[str], filters: dict[str, list[Any]],
             config: ReconciliationConfig, scale: float) -> SideStats:
    stats = SideStats(name=ds.name, rows_in=len(ds.rows), unparsed=[0] * len(value_cols),
                      unparsed_examples=[[] for _ in value_cols])
    wanted = {col: {normalize_key(v, config.case_insensitive_keys) for v in vals} for col, vals in filters.items()}
    for row in ds.rows:
        if any(normalize_key(row.get(col), config.case_insensitive_keys) not in vals for col, vals in wanted.items()):
            continue
        stats.rows_after_filter += 1
        key = tuple(normalize_key(row.get(c), config.case_insensitive_keys) for c in key_cols) if key_cols else ALL_KEY
        parsed: list[Optional[float]] = []
        for i, col in enumerate(value_cols):
            raw = row.get(col)
            number = parse_number(raw)
            if number is None and not is_blank(raw):
                stats.unparsed[i] += 1
                if len(stats.unparsed_examples[i]) < 5:
                    stats.unparsed_examples[i].append(repr(raw))
            scaled = number * scale if number is not None else None
            parsed.append(scaled if scaled is None or math.isfinite(scaled) else None)

        group = stats.groups.get(key)
        if group is None:
            display = [row.get(c) for c in key_cols] if key_cols else ["All rows"]
            stats.groups[key] = _Group(display=display, values=parsed)
            continue
        stats.duplicate_rows += 1
        group.count += 1
        if config.aggregate == "first":
            continue
        group.values = [
            a if b is None else (b if a is None else a + b) for a, b in zip(group.values, parsed)
        ]
    return stats


def reconcile_datasets(
    left: Dataset,
    right: Dataset,
    config: ReconciliationConfig,
    reconciliation_id: str,
    name: Optional[str] = None,
) -> DataReconciliation:
    validate_config(left, right, config)
    key_left = [p.left for p in config.key_columns]
    key_right = [p.right for p in config.key_columns]
    val_left = [p.left for p in config.value_columns]
    val_right = [p.right for p in config.value_columns]
    key_labels = [p.display() for p in config.key_columns] or ["Scope"]
    n_values = len(config.value_columns)

    ls = _collect(left, key_left, val_left, config.left_filters, config, config.scale_left)
    rs = _collect(right, key_right, val_right, config.right_filters, config, config.scale_right)

    rows: list[ResultRow] = []
    for key in list(ls.groups) + [k for k in rs.groups if k not in ls.groups]:
        lg, rg = ls.groups.get(key), rs.groups.get(key)
        display = (lg or rg).display
        cells = []
        for i, vc in enumerate(config.value_columns):
            lv = lg.values[i] if lg else None
            rv = rg.values[i] if rg else None
            cells.append(_compare(lv, rv, vc.tolerance_abs, vc.tolerance_pct, config.treat_blank_as_zero, bool(lg and rg)))
        if lg is None:
            status = "ONLY_RIGHT"
        elif rg is None:
            status = "ONLY_LEFT"
        elif all(c.within_tolerance for c in cells):
            status = "MATCH"
        else:
            status = "MISMATCH"
        rows.append(ResultRow(
            keys={label: _display(v) for label, v in zip(key_labels, display)},
            status=status,
            cells=cells,
            left_rows=lg.count if lg else 0,
            right_rows=rg.count if rg else 0,
        ))

    order = {"MISMATCH": 0, "ONLY_LEFT": 1, "ONLY_RIGHT": 1, "MATCH": 2}
    rows.sort(key=lambda r: (order[r.status], -max((abs(c.difference) for c in r.cells), default=0.0)))

    counts = {s: sum(1 for r in rows if r.status == s) for s in order}
    total = len(rows)
    summary = ReconciliationSummary(
        total_keys=total,
        matched=counts["MATCH"],
        mismatched=counts["MISMATCH"],
        only_left=counts["ONLY_LEFT"],
        only_right=counts["ONLY_RIGHT"],
        match_rate_pct=round(100.0 * counts["MATCH"] / total, 2) if total else 100.0,
        left_rows=ls.rows_after_filter,
        right_rows=rs.rows_after_filter,
    )

    value_summaries = []
    for i, vc in enumerate(config.value_columns):
        bridge = VarianceBridge()
        total_left = total_right = 0.0
        mismatched = 0
        for r in rows:
            c = r.cells[i]
            total_left += c.left or 0.0
            total_right += c.right or 0.0
            if r.status == "ONLY_LEFT":
                bridge.missing_from_right -= c.left or 0.0
            elif r.status == "ONLY_RIGHT":
                bridge.missing_from_left += c.right or 0.0
            else:
                bridge.value_differences += c.difference
                if not c.within_tolerance:
                    mismatched += 1
        bridge.total = total_right - total_left
        diff = total_right - total_left
        value_summaries.append(ValueSummary(
            label=vc.display(),
            left_column=vc.left,
            right_column=vc.right,
            total_left=total_left,
            total_right=total_right,
            difference=diff,
            difference_pct=(diff / abs(total_left) * 100) if abs(total_left) > _EPS else None,
            mismatched_rows=mismatched,
            bridge=bridge,
        ))

    warnings: list[str] = []
    confidence = Confidence.VERIFIED
    for side, stats, cols in (("left", ls, val_left), ("right", rs, val_right)):
        for col, n, examples in zip(cols, stats.unparsed, stats.unparsed_examples):
            if n:
                warnings.append(f"{n} value(s) in {stats.name} column '{col}' are not numbers and were treated as blank "
                                f"(e.g. {', '.join(examples[:3])}).")
                confidence = Confidence.PARTIALLY_VERIFIED
        if stats.duplicate_rows and config.key_columns:
            how = "kept the first row" if config.aggregate == "first" else "summed"
            warnings.append(f"{stats.name}: {stats.duplicate_rows} row(s) shared a key with another row and were {how}.")
            if config.aggregate == "first":
                confidence = Confidence.PARTIALLY_VERIFIED
        if stats.rows_after_filter < stats.rows_in:
            warnings.append(f"{stats.name}: filters kept {stats.rows_after_filter} of {stats.rows_in} rows.")
        if stats.rows_after_filter == 0:
            warnings.append(f"{stats.name} has no rows to compare" + (" after filters — check the filter values." if stats.rows_in else "."))
            confidence = Confidence.UNAVAILABLE
    if not config.key_columns:
        warnings.append("No key columns: grand totals were compared, not individual rows.")

    result = DataReconciliation(
        reconciliation_id=reconciliation_id,
        name=name or f"{left.name} vs {right.name}",
        left_dataset_id=left.dataset_id,
        right_dataset_id=right.dataset_id,
        left_name=left.name,
        right_name=right.name,
        config=config,
        # nothing compared is not agreement: an empty side or a filter typo must not read as "Sources agree"
        status="MATCHED" if total and counts["MATCH"] == total else "DIFFERENCES_FOUND",
        summary=summary,
        value_summaries=value_summaries,
        rows=rows,
        warnings=warnings,
        confidence=confidence,
    )
    result.drivers = [d for i in range(n_values) for d in dimension_drivers(rows, key_labels, i, value_summaries[i].label)] \
        if config.key_columns else []
    result.hypotheses = hypothesis_engine.generate(result, ls, rs)
    return result


def _compare(lv: Optional[float], rv: Optional[float], tol_abs: float, tol_pct: float,
             blank_as_zero: bool, both_sides: bool) -> CellDiff:
    diff = (rv or 0.0) - (lv or 0.0)
    pct = (diff / abs(lv) * 100) if lv not in (None, 0) and abs(lv) > _EPS else None
    if not both_sides:
        within = False
    elif lv is None and rv is None:
        within = True
    elif not blank_as_zero and (lv is None) != (rv is None):
        within = False
    else:
        # relative epsilon: summing thousands of 2-dp amounts drifts by ~1e-7
        threshold = max(tol_abs, abs(lv or 0.0) * tol_pct / 100.0) + max(_EPS, 1e-9 * max(abs(lv or 0.0), abs(rv or 0.0)))
        within = abs(diff) <= threshold
    return CellDiff(left=lv, right=rv, difference=diff, difference_pct=pct, within_tolerance=within)


def _display(value: Any) -> Any:
    return "" if value is None else value
