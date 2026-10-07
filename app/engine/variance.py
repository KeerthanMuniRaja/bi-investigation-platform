"""
Variance attribution: *which* members drive a difference, and *what kind*
of change it is.

  - dimension_drivers: for each key dimension (Product, Region, Month...),
    how much of the variance each member contributes.
  - rate_volume: the classic price/volume decomposition for any measure
    that is rate x volume (Stock Value = Buying Price x Quantity, Revenue
    = Price x Units, Cost = Rate x Hours). This is what turns the spec's
    "£28,228 variance" into "price rises added £X, lower quantities took
    away £Y".
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from app.engine.values import normalize_key, parse_number
from app.models.data_reconciliation import (
    DimensionDrivers,
    DriverMember,
    RateVolumeLine,
    RateVolumeResult,
    ResultRow,
)
from app.models.dataset import Dataset


def dimension_drivers(rows: list[ResultRow], key_labels: list[str], value_index: int, value_label: str,
                      top_n: int = 10) -> list[DimensionDrivers]:
    out = []
    for label in key_labels:
        totals: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        shown: dict[str, str] = {}
        for r in rows:
            if r.status == "MATCH":
                continue
            raw = r.keys.get(label, "")
            member = normalize_key(raw)
            shown.setdefault(member, str(raw))
            totals[member] += r.cells[value_index].difference
            counts[member] += 1
        abs_total = sum(abs(v) for v in totals.values())
        if abs_total <= 1e-9:
            continue
        if len(totals) == 1 and len(key_labels) > 1:
            continue  # one member (e.g. a single month) explains nothing
        members = sorted(totals.items(), key=lambda kv: abs(kv[1]), reverse=True)[:top_n]
        out.append(DimensionDrivers(
            dimension=label,
            value_label=value_label,
            members=[
                DriverMember(member=shown[m] or "(blank)", difference=d, share_pct=round(100 * abs(d) / abs_total, 2), rows=counts[m])
                for m, d in members if abs(d) > 1e-9
            ],
        ))
    return out


def rate_volume(
    dataset_a: Dataset,
    dataset_b: Dataset,
    key_columns: list[str],
    volume_column: str,
    rate_column: Optional[str] = None,
    value_column: Optional[str] = None,
) -> RateVolumeResult:
    """Decompose value_b - value_a into rate, volume and interaction effects
    per key. Supply either rate_column or value_column (rate is then
    value / volume, i.e. a weighted average rate)."""
    if not rate_column and not value_column:
        raise ValueError("Provide rate_column or value_column")
    for ds in (dataset_a, dataset_b):
        needed = [*key_columns, volume_column, *(c for c in (rate_column, value_column) if c)]
        missing = [c for c in needed if c not in ds.column_names()]
        if missing:
            raise ValueError(f"{ds.name} is missing column(s): {', '.join(missing)}")

    a, b = _rate_volume_groups(dataset_a, key_columns, volume_column, rate_column, value_column), \
        _rate_volume_groups(dataset_b, key_columns, volume_column, rate_column, value_column)

    lines = []
    for key in list(a) + [k for k in b if k not in a]:
        ga, gb = a.get(key), b.get(key)
        line = RateVolumeLine(key=(ga or gb)["label"])
        line.value_a = ga["value"] if ga else 0.0
        line.value_b = gb["value"] if gb else 0.0
        if ga and gb and ga["volume"] and gb["volume"]:
            ra, rb = ga["value"] / ga["volume"], gb["value"] / gb["volume"]
            va, vb = ga["volume"], gb["volume"]
            line.rate_effect = (rb - ra) * va
            line.volume_effect = (vb - va) * ra
            line.interaction = (rb - ra) * (vb - va)
        else:
            line.new_or_lost = line.value_b - line.value_a
        line.total_change = line.value_b - line.value_a
        lines.append(line)

    lines.sort(key=lambda l: abs(l.total_change), reverse=True)
    result = RateVolumeResult(
        total_a=sum(l.value_a for l in lines),
        total_b=sum(l.value_b for l in lines),
        total_change=sum(l.total_change for l in lines),
        rate_effect=sum(l.rate_effect for l in lines),
        volume_effect=sum(l.volume_effect for l in lines),
        interaction=sum(l.interaction for l in lines),
        new_or_lost=sum(l.new_or_lost for l in lines),
        lines=lines,
    )
    parts = [
        ("rate/price changes", result.rate_effect),
        ("volume changes", result.volume_effect),
        ("rate × volume interaction", result.interaction),
        ("new or discontinued items", result.new_or_lost),
    ]
    parts = [p for p in parts if abs(p[1]) > 0.005]
    result.explanation = (
        f"Total moved {result.total_change:+,.2f} ({result.total_a:,.2f} → {result.total_b:,.2f}): "
        + ", ".join(f"{name} {amount:+,.2f}" for name, amount in parts)
        + "."
    )
    return result


def _rate_volume_groups(ds: Dataset, keys: list[str], volume_col: str, rate_col: Optional[str],
                        value_col: Optional[str]) -> dict[tuple, dict[str, Any]]:
    groups: dict[tuple, dict[str, Any]] = {}
    for row in ds.rows:
        key = tuple(normalize_key(row.get(k)) for k in keys)
        volume = parse_number(row.get(volume_col)) or 0.0
        if value_col:
            value = parse_number(row.get(value_col)) or 0.0
        else:
            value = (parse_number(row.get(rate_col)) or 0.0) * volume
        g = groups.setdefault(key, {"label": " / ".join(str(row.get(k)) for k in keys) or "All", "volume": 0.0, "value": 0.0})
        g["volume"] += volume
        g["value"] += value
    return groups
