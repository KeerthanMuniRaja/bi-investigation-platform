"""
Reconciliation engine (Section 14, Module 3). Answers spec Question 2:
"Why is this number different?" — the product must investigate the
variance, not just report that one exists.
"""
from __future__ import annotations

from app.models.graph import Confidence
from app.models.reconciliation import Reconciliation, Variance
from app.models.snapshot import VisualSnapshot


def reconcile(snapshot_a: VisualSnapshot, snapshot_b: VisualSnapshot, reconciliation_id: str) -> Reconciliation:
    value_a = snapshot_a.primary_value()
    value_b = snapshot_b.primary_value()
    if value_a is None or value_b is None:
        variance = Variance(value_a=value_a or 0.0, value_b=value_b or 0.0, difference=0.0, difference_pct=None)
        confidence = Confidence.UNAVAILABLE
    else:
        variance = Variance.compute(value_a, value_b)
        confidence = Confidence.VERIFIED

    fp_a = snapshot_a.context_fingerprint()
    fp_b = snapshot_b.context_fingerprint()
    context_matches = fp_a == fp_b

    context_diff: list[str] = []
    fields_a = {f.field: f for f in snapshot_a.filters}
    fields_b = {f.field: f for f in snapshot_b.filters}
    for field in sorted(set(fields_a) | set(fields_b)):
        fa, fb = fields_a.get(field), fields_b.get(field)
        va = fa.values if fa else None
        vb = fb.values if fb else None
        if va != vb:
            context_diff.append(f"{field}: {va} (A) vs {vb} (B)")

    return Reconciliation(
        reconciliation_id=reconciliation_id,
        snapshot_id_a=snapshot_a.snapshot_id,
        snapshot_id_b=snapshot_b.snapshot_id,
        variance=variance,
        context_matches=context_matches,
        context_diff=context_diff,
        confidence=confidence,
    )
