"""
Investigation is the fundamental product object (Section 10). This module
is where reconciliation + lineage + impact results get turned into
evidence, findings and ranked hypotheses attached to a case, which is what
actually gets saved (Section 25 Evidence Store, Section 52 Investigation
History) so the same problem is never investigated from scratch twice.
"""
from __future__ import annotations

from app.models.data_reconciliation import DataReconciliation, RateVolumeResult
from app.models.graph import CONFIDENCE_RANK, Confidence
from app.models.investigation import EvidenceItem, Finding, Hypothesis, Investigation, InvestigationStatus
from app.models.lineage import LineagePath
from app.models.reconciliation import ImpactResult, Reconciliation
from app.models.snapshot import VisualSnapshot

_STRONG = (Confidence.VERIFIED, Confidence.KNOWN)
_WEAK = (Confidence.UNAVAILABLE, Confidence.UNSUPPORTED, Confidence.UNKNOWN)


def create_investigation(investigation_id: str, question: str) -> Investigation:
    return Investigation(investigation_id=investigation_id, question=question, status=InvestigationStatus.INVESTIGATING)


def _evidence(investigation: Investigation, **fields) -> EvidenceItem:
    item = EvidenceItem(evidence_id=investigation.next_evidence_id(), **fields)
    investigation.add_evidence(item)
    return item


def _finding(investigation: Investigation, summary: str, confidence: Confidence, evidence_ids: list[str]) -> Finding:
    finding = Finding(finding_id=investigation.next_finding_id(), summary=summary, confidence=confidence,
                      evidence_ids=evidence_ids)
    investigation.findings.append(finding)
    return finding


def _hypothesis(investigation: Investigation, h: Hypothesis, evidence_ids: list[str]) -> None:
    investigation.hypotheses.append(h.model_copy(update={
        "hypothesis_id": investigation.next_hypothesis_id(),
        "supporting_evidence_ids": evidence_ids,
    }))


def attach_reconciliation(
    investigation: Investigation,
    reconciliation: Reconciliation,
    snapshot_a: VisualSnapshot,
    snapshot_b: VisualSnapshot,
) -> None:
    investigation.snapshot_ids += [snapshot_a.snapshot_id, snapshot_b.snapshot_id]
    investigation.reconciliation_id = reconciliation.reconciliation_id

    ids = []
    for label, snap in (("A", snapshot_a), ("B", snapshot_b)):
        ids.append(_evidence(
            investigation,
            kind="snapshot_value",
            description=f"Snapshot {label}: {snap.visual_title} = {snap.primary_value()} "
                        f"(context: {snap.context_fingerprint() or 'none'})",
            source_ref=snap.snapshot_id,
            confidence=snap.confidence,
            payload=snap.model_dump(),
        ).evidence_id)

    v = reconciliation.variance
    pct = f"{v.difference_pct:.2f}%" if v.difference_pct is not None else "n/a"
    context = _evidence(
        investigation,
        kind="filter_context",
        description=(
            f"Context {'matches' if reconciliation.context_matches else 'differs'}: "
            + ("; ".join(reconciliation.context_diff) if reconciliation.context_diff else "no filter differences detected")
        ),
        source_ref=reconciliation.reconciliation_id,
        confidence=reconciliation.confidence,
    )
    ids.append(context.evidence_id)

    if reconciliation.context_matches:
        explanation = "Filter context is identical between snapshots, so the variance is not explained by a filter change."
    else:
        explanation = "Filter context differs between snapshots — see context evidence."
    _finding(
        investigation,
        f"Value moved from {v.value_a} to {v.value_b} (difference {v.difference:+.2f}, {pct}). {explanation}",
        reconciliation.confidence,
        ids,
    )

    if not reconciliation.context_matches and reconciliation.confidence in _STRONG:
        _hypothesis(investigation, Hypothesis(
            hypothesis_id="",
            title="Different filter context between the two values",
            description="The two values were captured under different filters, so part or all of the variance is "
                        "the filter change itself (e.g. a different period) rather than a data or logic problem.",
            category="filter_context",
            score=0.5,
            supported=None,
            confidence=Confidence.INFERRED,
            facts=list(reconciliation.context_diff),
            suggested_action="If the comparison is meant to be like-for-like, align the filters; otherwise break the "
                             "change down by driver (rate/volume, dimension members).",
            affected_amount=v.difference,
        ), [context.evidence_id])
    investigation.recompute_confidence()


def attach_rate_volume(investigation: Investigation, result: RateVolumeResult, label: str = "value") -> None:
    ev = _evidence(
        investigation,
        kind="variance_decomposition",
        description=result.explanation,
        confidence=Confidence.VERIFIED,
        payload=result.model_dump(),
    )
    effects = sorted(
        [("Rate / price", result.rate_effect), ("Volume", result.volume_effect),
         ("Rate × volume interaction", result.interaction), ("New / discontinued items", result.new_or_lost)],
        key=lambda e: abs(e[1]), reverse=True,
    )
    top_lines = [l for l in result.lines if abs(l.total_change) > 0.005][:5]
    _hypothesis(investigation, Hypothesis(
        hypothesis_id="",
        title=f"{label} change decomposed into rate and volume effects",
        description=result.explanation,
        category="rate_volume",
        score=0.9,
        supported=True,
        confidence=Confidence.VERIFIED,
        facts=[f"{name}: {amount:+,.2f}" for name, amount in effects if abs(amount) > 0.005]
        + [f"{l.key}: {l.total_change:+,.2f} (rate {l.rate_effect:+,.2f}, volume {l.volume_effect:+,.2f})" for l in top_lines],
        suggested_action="Confirm the largest rate and volume movements with the business owners of those items.",
        affected_amount=result.total_change,
    ), [ev.evidence_id])
    _finding(investigation, result.explanation, Confidence.VERIFIED, [ev.evidence_id])
    investigation.recompute_confidence()


def attach_data_reconciliation(investigation: Investigation, rec: DataReconciliation) -> None:
    if rec.reconciliation_id not in investigation.data_reconciliation_ids:
        investigation.data_reconciliation_ids.append(rec.reconciliation_id)
    s = rec.summary
    summary_ev = _evidence(
        investigation,
        kind="data_reconciliation",
        description=(f"{rec.left_name} vs {rec.right_name}: {s.matched}/{s.total_keys} keys match "
                     f"({s.match_rate_pct}%), {s.mismatched} differ, {s.only_left} only in {rec.left_name}, "
                     f"{s.only_right} only in {rec.right_name}."),
        source_ref=rec.reconciliation_id,
        confidence=rec.confidence,
        payload={"summary": s.model_dump(), "warnings": rec.warnings},
    )
    ids = [summary_ev.evidence_id]
    for vs in rec.value_summaries:
        b = vs.bridge
        pct = f"{vs.difference_pct:+.2f}%" if vs.difference_pct is not None else "n/a"
        ev = _evidence(
            investigation,
            kind="variance_bridge",
            description=(f"{vs.label}: {vs.total_left:,.2f} vs {vs.total_right:,.2f} (difference {vs.difference:+,.2f}, {pct}). "
                         f"Bridge: missing from {rec.right_name} {b.missing_from_right:+,.2f}, missing from {rec.left_name} "
                         f"{b.missing_from_left:+,.2f}, value differences {b.value_differences:+,.2f}."),
            source_ref=rec.reconciliation_id,
            confidence=rec.confidence,
            payload=vs.model_dump(),
        )
        ids.append(ev.evidence_id)
        _finding(investigation, ev.description, rec.confidence, [summary_ev.evidence_id, ev.evidence_id])
    for h in rec.hypotheses:
        _hypothesis(investigation, h, ids)
    investigation.recompute_confidence()


def attach_lineage(investigation: Investigation, lineage: LineagePath) -> None:
    investigation.lineage = lineage
    if lineage.root_id not in investigation.object_ids:
        investigation.object_ids.append(lineage.root_id)
    chain = " -> ".join(lineage.as_chain_strings())
    ev = _evidence(
        investigation,
        kind="lineage_path",
        description=f"Dependency chain: {chain}" if chain else "No lineage chain resolved.",
        source_ref=lineage.root_id,
        confidence=lineage.overall_confidence,
        payload={"coverage_pct": lineage.coverage_pct, "gaps": lineage.gaps},
    )
    _finding(
        investigation,
        f"Lineage coverage {lineage.coverage_pct}%" + (f"; known gaps: {', '.join(lineage.gaps)}" if lineage.gaps else ""),
        lineage.overall_confidence,
        [ev.evidence_id],
    )
    investigation.recompute_confidence()


def attach_impact(investigation: Investigation, impact: ImpactResult) -> None:
    investigation.impact = impact
    ev = _evidence(
        investigation,
        kind="relationship",
        description=impact.explanation,
        source_ref=impact.target_node_id,
        confidence=impact.confidence,
        payload=impact.model_dump(),
    )
    _finding(investigation, f"Impact risk: {impact.risk.value}. {impact.explanation}", impact.confidence, [ev.evidence_id])
    investigation.recompute_confidence()


def finalize(investigation: Investigation) -> Investigation:
    """A root cause is only claimed when a hypothesis is actually supported
    by strong evidence — a computed variance on its own is a finding, not
    an explanation."""
    investigation.hypotheses.sort(key=lambda h: h.score, reverse=True)
    proven = [h for h in investigation.hypotheses if h.supported and h.confidence in _STRONG and h.category != "agreement"]
    agreed = any(h.category == "agreement" for h in investigation.hypotheses)
    if investigation.confidence in _WEAK and not proven:
        investigation.status = InvestigationStatus.INCONCLUSIVE
    elif agreed and not proven:
        investigation.status = InvestigationStatus.RESOLVED
    elif proven and CONFIDENCE_RANK[investigation.confidence] >= CONFIDENCE_RANK[Confidence.PARTIALLY_VERIFIED]:
        investigation.status = InvestigationStatus.ROOT_CAUSE_FOUND
    else:
        investigation.status = InvestigationStatus.INVESTIGATING
    investigation.touch()
    return investigation
