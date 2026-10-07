"""
Root-cause hypothesis generation for reconciliations (Section 18, Modules
6-7: rank competing explanations against each other instead of stopping at
"these numbers differ").

Every rule here is deterministic and grounded in the result itself — each
hypothesis carries the facts that support it and a confidence that says
whether it is arithmetically proven (VERIFIED: e.g. the missing rows add
up to exactly the variance) or a pattern that fits (INFERRED). Nothing is
guessed from outside the data.

Rules, roughly in the order analysts check them:
  no difference · rows missing on one side (scope/filter/completeness)
  · period cut-off (latest period missing) · key formatting mismatch
  · unit scale (thousands vs units) · consistent factor (FX / VAT)
  · sign convention · rounding · reclassification between categories
  · duplicate/granularity double counting · concentrated record changes
"""
from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Optional

from app.engine.values import loose_key, normalize_key
from app.models.graph import Confidence
from app.models.investigation import Hypothesis

if TYPE_CHECKING:
    from app.engine.data_reconciliation import SideStats
    from app.models.data_reconciliation import DataReconciliation, ResultRow

_SCALES = {1000.0: "thousands", 1_000_000.0: "millions", 100.0: "hundreds (or pence vs pounds)",
           0.001: "thousands", 0.000001: "millions", 0.01: "hundredths (or pounds vs pence)"}


def _money(x: float) -> str:
    return f"{x:,.2f}"


def generate(result: "DataReconciliation", left: "SideStats", right: "SideStats") -> list[Hypothesis]:
    if not result.value_summaries:
        return []
    ctx = _Context(result, left, right)
    found: list[Hypothesis] = []
    for rule in (_no_difference, _missing_rows, _period_cutoff, _key_format, _scale_factor, _sign_flip,
                 _rounding, _reclassification, _duplicates, _concentrated):
        h = rule(ctx)
        if h:
            found.append(h)
    found.sort(key=lambda h: h.score, reverse=True)
    for i, h in enumerate(found, start=1):
        h.hypothesis_id = f"H{i:03d}"
    return found


class _Context:
    def __init__(self, result: "DataReconciliation", left: "SideStats", right: "SideStats"):
        self.result = result
        self.left = left
        self.right = right
        self.primary = result.value_summaries[0]
        self.total_var = self.primary.difference
        self.abs_var = abs(self.total_var)
        self.key_labels = result.key_labels() or ["Scope"]
        self.rows = result.rows
        self.only_left = [r for r in self.rows if r.status == "ONLY_LEFT"]
        self.only_right = [r for r in self.rows if r.status == "ONLY_RIGHT"]
        self.mismatched = [r for r in self.rows if r.status == "MISMATCH"]
        self.matched_pairs = [r for r in self.rows if r.status in ("MATCH", "MISMATCH")]
        self.tol = result.config.value_columns[0]
        self.key_format_pairs = _loose_pairs(self.only_left, self.only_right)

    def key_text(self, row: "ResultRow") -> str:
        return " / ".join(str(v) for v in row.keys.values()) or "All rows"

    def share_of_variance(self, amount: float) -> float:
        return abs(amount) / self.abs_var if self.abs_var > 1e-9 else 0.0


def _h(title: str, description: str, category: str, score: float, confidence: Confidence, facts: list[str],
       action: str, amount: Optional[float] = None) -> Hypothesis:
    return Hypothesis(
        hypothesis_id="",
        title=title,
        description=description,
        category=category,
        score=round(max(0.0, min(score, 1.0)), 3),
        supported=score >= 0.6,
        confidence=confidence,
        facts=facts,
        suggested_action=action,
        affected_amount=amount,
    )


# ----------------------------------------------------------------- rules
def _no_difference(ctx: _Context) -> Optional[Hypothesis]:
    if ctx.result.status != "MATCHED" or not ctx.result.summary.total_keys:
        return None
    s = ctx.result.summary
    return _h(
        "Sources agree",
        f"All {s.total_keys} key(s) match within tolerance across every compared value column.",
        "agreement", 1.0, Confidence.VERIFIED,
        [f"{vs.label}: {_money(vs.total_left)} vs {_money(vs.total_right)}" for vs in ctx.result.value_summaries],
        "No action needed. Save this as a template to re-run it next period.",
    )


def _missing_rows(ctx: _Context) -> Optional[Hypothesis]:
    if not ctx.only_left and not ctx.only_right:
        return None
    b = ctx.primary.bridge
    missing_total = b.missing_from_left + b.missing_from_right
    share = ctx.share_of_variance(missing_total)
    facts = []
    if ctx.only_left:
        facts.append(f"{len(ctx.only_left)} key(s) only in {ctx.result.left_name}, worth {_money(-b.missing_from_right)} "
                     f"(e.g. {', '.join(ctx.key_text(r) for r in ctx.only_left[:3])}).")
    if ctx.only_right:
        facts.append(f"{len(ctx.only_right)} key(s) only in {ctx.result.right_name}, worth {_money(b.missing_from_left)} "
                     f"(e.g. {', '.join(ctx.key_text(r) for r in ctx.only_right[:3])}).")

    scope = _scope_dimension(ctx)
    if scope:
        facts.append(scope)
    explained_fully = ctx.abs_var > 1e-9 and abs(missing_total - ctx.total_var) <= max(0.01, ctx.abs_var * 1e-6)
    if explained_fully:
        facts.append("Missing rows account for 100% of the total variance — matched rows agree.")
    score = 0.45 + 0.5 * share + (0.1 if scope else 0.0)
    return _h(
        "Rows present in only one source (scope, filter or completeness)",
        "Part of the difference comes from records that exist on one side but not the other — typically a "
        "report/visual filter, a different scope (entity, region, period), a late-loaded batch, or an "
        "unmapped code.",
        "missing_rows", score, Confidence.VERIFIED if explained_fully else Confidence.INFERRED, facts,
        "Check the visual/page filters and the Excel extract's selection criteria for the members listed; "
        "confirm whether the missing records should be in scope.",
        missing_total,
    )


def _scope_dimension(ctx: _Context) -> Optional[str]:
    """If every missing row shares a member that never appears on the other
    side (e.g. Region = North), that member is out of scope there."""
    if len(ctx.key_labels) < 2:
        return None
    for side_rows, side_status, side_name, other_name in (
        (ctx.only_right, "ONLY_RIGHT", ctx.result.right_name, ctx.result.left_name),
        (ctx.only_left, "ONLY_LEFT", ctx.result.left_name, ctx.result.right_name),
    ):
        if len(side_rows) < 2:
            continue
        for label in ctx.key_labels:
            members = {normalize_key(r.keys.get(label)) for r in side_rows}
            if len(members) != 1:
                continue
            member = next(iter(members))
            present_elsewhere = any(
                normalize_key(r.keys.get(label)) == member for r in ctx.rows if r.status != side_status
            )
            if not present_elsewhere:
                shown = side_rows[0].keys.get(label)
                return f"All of them have {label} = '{shown}', which never appears in {other_name} — likely filtered out there."
    return None


def _period_cutoff(ctx: _Context) -> Optional[Hypothesis]:
    dim = _period_dimension(ctx)
    if not dim:
        return None
    label, ordered = dim
    latest = ordered[-1]
    for side_rows, side_name, other_name in ((ctx.only_left, ctx.result.left_name, ctx.result.right_name),
                                             (ctx.only_right, ctx.result.right_name, ctx.result.left_name)):
        if not side_rows:
            continue
        periods = {normalize_key(r.keys.get(label)) for r in side_rows}
        if periods == {latest}:
            amount = sum(r.cells[0].difference for r in side_rows)
            return _h(
                f"Cut-off / refresh timing: latest {label} missing from {other_name}",
                f"Every row missing from {other_name} belongs to the most recent {label} ({latest}). "
                f"{other_name} was probably extracted or refreshed before that period's data landed.",
                "timing", 0.55 + 0.4 * ctx.share_of_variance(amount), Confidence.INFERRED,
                [f"{len(side_rows)} row(s) for {label} = {latest} exist only in {side_name}.",
                 f"Worth {_money(amount)} of the variance."],
                f"Compare the refresh time of the dataset/semantic model with the Excel extract time, then re-run "
                f"after both include {latest} (or filter both to the same closed period).",
                amount,
            )
    return None


def _period_dimension(ctx: _Context) -> Optional[tuple[str, list[str]]]:
    for label in ctx.key_labels:
        values = sorted({normalize_key(r.keys.get(label)) for r in ctx.rows if r.keys.get(label) not in (None, "")},
                        key=_period_order)
        if len(values) < 2:
            continue
        lowered = label.lower()
        dateish = all(len(v) >= 7 and v[:4].isdigit() and v[4] == "-" for v in values)
        named = any(t in lowered for t in ("date", "period", "month", "week", "year", "quarter"))
        periodish = named and all(v[:4].isdigit() or v in _MONTH_INDEX for v in values)
        if dateish or periodish:
            return label, values
    return None


def _loose_pairs(only_left: list, only_right: list) -> list[tuple]:
    """One-sided keys that are the same record written differently (00123 vs 123)."""
    loose_right = {}
    for r in only_right:
        loose_right.setdefault(tuple(loose_key(v) for v in r.keys.values()), r)
    pairs = []
    for r in only_left:
        match = loose_right.pop(tuple(loose_key(v) for v in r.keys.values()), None)
        if match:
            pairs.append((r, match))
    return pairs


_MONTH_INDEX = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"], start=1)}


def _period_order(value: str) -> tuple:
    """Months 1..12 sort numerically and 'march' after 'february', not as text."""
    if value in _MONTH_INDEX:
        return (0, _MONTH_INDEX[value], "")
    try:
        return (0, float(value), "")
    except ValueError:
        return (1, 0.0, value)


def _key_format(ctx: _Context) -> Optional[Hypothesis]:
    pairs = ctx.key_format_pairs
    if not pairs:
        return None
    ratio = len(pairs) / min(len(ctx.only_left), len(ctx.only_right))
    # same sign convention as every other hypothesis: B - A
    amount = sum(l.cells[0].difference + rr.cells[0].difference for l, rr in pairs)
    return _h(
        "Key formatting mismatch (leading zeros, punctuation, spacing)",
        f"{len(pairs)} key(s) that look unmatched are the same record written differently in each source, so "
        "they appear as 'missing' on both sides.",
        "key_format", 0.55 + 0.4 * ratio, Confidence.VERIFIED if ratio >= 0.9 else Confidence.INFERRED,
        [f"'{ctx.key_text(l)}' ({ctx.result.left_name}) ≈ '{ctx.key_text(rr)}' ({ctx.result.right_name})" for l, rr in pairs[:5]],
        "Standardise the key (e.g. strip leading zeros / punctuation) in the source, or add a rename/cleanup "
        "transform before comparing.",
        amount,
    )


def _ratios(ctx: _Context) -> list[float]:
    out = []
    for r in ctx.matched_pairs:
        c = r.cells[0]
        if c.left and c.right and abs(c.left) > 1e-9:
            out.append(c.right / c.left)
    return out


def _scale_factor(ctx: _Context) -> Optional[Hypothesis]:
    ratios = _ratios(ctx)
    if len(ratios) < 3 or not ctx.mismatched:  # one or two rows can't show a consistent factor
        return None
    median = statistics.median(ratios)
    consistent = sum(1 for x in ratios if abs(x - median) <= abs(median) * 0.01) / len(ratios)
    if consistent < 0.6 or abs(median - 1) < 0.005 or abs(median + 1) < 0.005:  # -1 is the sign rule's job
        return None
    for scale, name in _SCALES.items():
        if abs(median - scale) <= scale * 0.02:
            side = ctx.result.right_name if scale > 1 else ctx.result.left_name
            other = ctx.result.left_name if scale > 1 else ctx.result.right_name
            fix = "scale_left" if scale > 1 else "scale_right"
            factor = scale if scale > 1 else 1 / scale
            return _h(
                f"Unit mismatch: one source is in {name}",
                f"{side} values are consistently ~{factor:,.0f}× {other} values — the same numbers expressed in "
                "different units.",
                "units", 0.6 + 0.35 * consistent, Confidence.VERIFIED if consistent >= 0.95 else Confidence.INFERRED,
                [f"Median ratio {ctx.result.right_name}/{ctx.result.left_name} = {median:,.4f} across {len(ratios)} matched rows "
                 f"({consistent:.0%} within 1%)."],
                f"Set {fix} = {factor:g} in the reconciliation config (or fix the display units) and re-run.",
                ctx.total_var,
            )
    return _h(
        f"Consistent factor of {median:.4f} between sources",
        "Values differ by the same percentage on most rows — typical of a currency conversion, a VAT/tax "
        "inclusion difference, or an allocation rate applied on one side only.",
        "factor", 0.5 + 0.35 * consistent, Confidence.INFERRED,
        [f"{consistent:.0%} of {len(ratios)} matched rows have ratio ≈ {median:.4f} ({(median - 1) * 100:+.2f}%)."],
        "Check exchange rates, tax treatment and allocation logic in the measure definition vs the Excel formula.",
        ctx.total_var,
    )


def _sign_flip(ctx: _Context) -> Optional[Hypothesis]:
    if not ctx.mismatched:
        return None
    flipped = [r for r in ctx.mismatched if r.cells[0].left and r.cells[0].right
               and abs(r.cells[0].left + r.cells[0].right) <= max(0.01, abs(r.cells[0].left) * 0.001)]
    if not flipped:
        return None
    ratio = len(flipped) / len(ctx.mismatched)
    amount = sum(r.cells[0].difference for r in flipped)
    return _h(
        "Sign convention differs (credits vs debits)",
        f"{len(flipped)} mismatched row(s) have the same magnitude with opposite signs.",
        "sign", 0.45 + 0.5 * ratio, Confidence.VERIFIED if ratio >= 0.9 else Confidence.INFERRED,
        [f"{ctx.key_text(r)}: {_money(r.cells[0].left)} vs {_money(r.cells[0].right)}" for r in flipped[:5]],
        "Align the sign convention (e.g. multiply one side by -1 using scale_left/scale_right = -1, or fix the "
        "measure).",
        amount,
    )


def _rounded_to(a: float, b: float) -> Optional[int]:
    """Smallest power of ten k such that one value is the other rounded to
    10^k (half-up, like Excel's ROUND — Python's round() is banker's).
    Large relative gaps (1499 vs 1000, 100 vs 0) are never 'rounding'."""
    if a == 0 or b == 0:
        return None
    for k in range(-2, 4):
        unit = 10.0 ** k
        if unit > 1 and abs(a - b) > 0.05 * max(abs(a), abs(b)):
            continue  # rounding to tens or more only explains small relative gaps
        for exact, rounded in ((a, b), (b, a)):
            on_grid = abs(rounded / unit - round(rounded / unit)) < 1e-6
            if on_grid and abs(exact - rounded) <= unit / 2 + 1e-9 * max(1.0, abs(exact)):
                return k
    return None


def _rounding(ctx: _Context) -> Optional[Hypothesis]:
    if not ctx.mismatched:
        return None
    levels = []
    for r in ctx.mismatched:
        c = r.cells[0]
        if c.left is None or c.right is None:
            return None
        k = _rounded_to(c.left, c.right)
        if k is None:
            return None
        levels.append(k)
    biggest = max(abs(r.cells[0].difference) for r in ctx.mismatched)
    unit = 10.0 ** max(levels)
    fine = max(levels) <= 0
    return _h(
        "Rounding / display precision",
        f"Every value mismatch ({len(levels)}) is explained by one source rounding to the nearest {unit:g} "
        "— e.g. a visual formatted without decimals, or ROUND in the Excel formula.",
        "rounding", 0.8 if fine else 0.55, Confidence.VERIFIED if fine else Confidence.INFERRED,
        [f"Largest difference on a matched row: {_money(biggest)} (rounding to {unit:g}).",
         *[f"{ctx.key_text(r)}: {_money(r.cells[0].left)} vs {_money(r.cells[0].right)}" for r in ctx.mismatched[:3]]],
        f"If this precision is acceptable, set tolerance_abs to {max(biggest, 0.01):,.2f} on this value column.",
        ctx.primary.bridge.value_differences,
    )


def _reclassification(ctx: _Context) -> Optional[Hypothesis]:
    explained = {id(r) for pair in ctx.key_format_pairs for r in pair}
    one_left = [r for r in ctx.only_left if id(r) not in explained]
    one_right = [r for r in ctx.only_right if id(r) not in explained]
    if len(ctx.mismatched) < 2 and not (one_left and one_right):
        return None
    tolerance = max(ctx.tol.tolerance_abs, 0.01)
    if ctx.abs_var > tolerance:
        return None
    moved = sum(abs(r.cells[0].difference) for r in ctx.rows if r.status != "MATCH" and id(r) not in explained) / 2
    return _h(
        "Reclassification between categories",
        "The grand totals agree but individual rows don't — amounts have moved between keys (a mapping or "
        "hierarchy change, a re-coded account, or a different allocation), rather than being lost.",
        "reclassification", 0.8, Confidence.VERIFIED,
        [f"Total difference {_money(ctx.total_var)}, but {_money(moved)} moved between rows.",
         f"{len(ctx.mismatched)} mismatched and {len(one_left) + len(one_right)} one-sided key(s)."],
        "Compare the category mapping / hierarchy tables used by each source.",
        moved,
    )


def _duplicates(ctx: _Context) -> Optional[Hypothesis]:
    if not ctx.result.config.key_columns:
        return None
    sides = [(s, n) for s, n in ((ctx.left, ctx.result.left_name), (ctx.right, ctx.result.right_name)) if s.duplicate_rows]
    if not sides:
        return None
    facts = [f"{n}: {s.duplicate_rows} extra row(s) shared a key and were aggregated." for s, n in sides]
    doubled = [r for r in ctx.mismatched if _is_double(r.cells[0].left, r.cells[0].right)]
    score = 0.3
    if doubled:
        score = 0.55 + 0.4 * len(doubled) / max(len(ctx.mismatched), 1)
        facts.append(f"{len(doubled)} mismatched row(s) are exactly double on one side — classic double counting.")
    return _h(
        "Granularity difference or double counting",
        "One source has several rows per key. That's expected when it is more detailed (it gets summed), but if "
        "the same transaction appears twice it inflates the total.",
        "duplicates", score, Confidence.INFERRED, facts,
        "Check whether the extra rows are genuine detail or duplicates (e.g. a join fan-out in the model or a "
        "copied block in the workbook).",
    )


def _concentrated(ctx: _Context) -> Optional[Hypothesis]:
    if len(ctx.mismatched) < 1:
        return None
    value_var = sum(abs(r.cells[0].difference) for r in ctx.mismatched)
    if value_var <= 1e-9:
        return None
    top = sorted(ctx.mismatched, key=lambda r: abs(r.cells[0].difference), reverse=True)[:3]
    top_share = sum(abs(r.cells[0].difference) for r in top) / value_var
    if top_share < 0.8 or len(ctx.mismatched) > 50 and top_share < 0.9:
        return None
    return _h(
        f"Specific record{'s' if len(top) > 1 else ''} changed",
        f"{len(top)} key(s) account for {top_share:.0%} of the value differences on matched rows — look at these "
        "records rather than at the whole pipeline.",
        "specific_records", 0.5 + 0.3 * top_share * ctx.share_of_variance(sum(r.cells[0].difference for r in top)),
        Confidence.VERIFIED,
        [f"{ctx.key_text(r)}: {_money(r.cells[0].left or 0)} vs {_money(r.cells[0].right or 0)} "
         f"({r.cells[0].difference:+,.2f})" for r in top],
        "Trace these records back to source (late adjustments, manual journals, overrides in Excel).",
        sum(r.cells[0].difference for r in top),
    )


def _is_double(left: Optional[float], right: Optional[float]) -> bool:
    if not left or not right:
        return False
    return abs(right / left - 2) < 0.01 or abs(left / right - 2) < 0.01
