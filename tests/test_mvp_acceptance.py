"""
These tests are written directly against Section 66 (MVP Success Criteria)
and Section 67 (MVP Acceptance Test Example) of the product spec — the
spec's own definition of done, not a definition I invented separately.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.engine import investigation as inv_engine
from app.engine.extraction import build_full_graph
from app.engine.graph_store import GraphStore
from app.engine.impact import analyze_impact
from app.engine.lineage import trace_upstream
from app.engine.reconciliation import reconcile
from app.main import app
from app.models.graph import Confidence
from app.models.reconciliation import RiskLevel
from app.models.snapshot import FilterContext
from tests.fakes import MockConnector


@pytest.fixture()
def graph() -> GraphStore:
    store = GraphStore()
    build_full_graph(store, MockConnector(), "WS001")
    return store


@pytest.fixture()
def snapshots():
    connector = MockConnector()
    filters_a = [
        FilterContext(field="Calendar[Year]", operator="equals", values=["2025"]),
        FilterContext(field="Calendar[Month]", operator="equals", values=["March"]),
    ]
    filters_b = [
        FilterContext(field="Calendar[Year]", operator="equals", values=["2026"]),
        FilterContext(field="Calendar[Month]", operator="equals", values=["March"]),
    ]
    snap_a = connector.get_visual_snapshot("R001", "P001", "V001", filters_a, "SNAP_A")
    snap_b = connector.get_visual_snapshot("R001", "P001", "V001", filters_b, "SNAP_B")
    return snap_a, snap_b


# --- Scenario: reproduce a visual's value and context (Section 66, A) -----
def test_snapshot_reproduces_visual_value_and_context(snapshots):
    snap_a, snap_b = snapshots
    assert snap_a.primary_value() == 3_615_900.0
    assert snap_b.primary_value() == 3_644_128.0
    assert snap_a.context_fingerprint() != snap_b.context_fingerprint()


# --- Scenario: compare across periods, detect the variance (B) -----------
def test_reconciliation_detects_variance_matching_spec_example(snapshots):
    snap_a, snap_b = snapshots
    rec = reconcile(snap_a, snap_b, "REC_TEST")
    assert rec.variance.difference == pytest.approx(28_228.0)
    assert rec.variance.difference_pct == pytest.approx(0.7807, abs=0.001)
    assert rec.context_matches is False
    assert any("Calendar[Year]" in d for d in rec.context_diff)


# --- Scenario: trace the calculation chain (C) ----------------------------
def test_lineage_traces_measure_to_source_columns(graph):
    lineage = trace_upstream(graph, "V001")
    chain_names = {step.to_name for step in lineage.steps} | {lineage.steps[0].from_name if lineage.steps else ""}
    assert "Stock Value" in chain_names
    assert "Buying Price" in chain_names
    assert "Quantity" in chain_names
    assert "Stock" in chain_names
    # The connector never claimed source-system lineage it can't back up:
    # every recorded edge resolves (coverage 100%), but what lies upstream of
    # the data source is reported as a gap — not hidden, not counted as a failure.
    assert lineage.coverage_pct == 100.0
    assert lineage.untraced_sources and any("not traced" in g for g in lineage.gaps)
    assert lineage.overall_confidence != Confidence.KNOWN


# --- Scenario: impact analysis on a candidate change (Section 67 delete) -
def test_impact_analysis_flags_dependents_of_shared_column(graph):
    impact = analyze_impact(graph, "C_BUYPRICE")
    assert set(impact.affected_measure_ids) == {"M_AVGBUY", "M_STOCKVALUE"}
    assert impact.risk == RiskLevel.HIGH


# --- Scenario: save evidence-backed findings as an investigation case ----
def test_investigation_saves_evidence_and_never_overclaims_confidence(graph, snapshots):
    snap_a, snap_b = snapshots
    rec = reconcile(snap_a, snap_b, "REC_TEST")
    investigation = inv_engine.create_investigation("INV_TEST", "Why did Stock Value change?")
    inv_engine.attach_reconciliation(investigation, rec, snap_a, snap_b)
    lineage = trace_upstream(graph, "V001")
    inv_engine.attach_lineage(investigation, lineage)
    inv_engine.finalize(investigation)

    assert len(investigation.evidence) >= 4
    assert len(investigation.findings) == 2
    # Confidence must be capped by the weakest finding (the lineage gap),
    # never rounded up to KNOWN/VERIFIED just because most evidence was good.
    assert investigation.confidence == Confidence.PARTIALLY_VERIFIED


# --- Full API surface, exercised the way a client actually would ---------
def test_full_investigation_flow_through_the_http_api(mock_connection):
    client = TestClient(app)

    conn = {"id": mock_connection}
    assert client.post(f"/connections/{conn['id']}/sync", params={"workspace_id": "WS001"}).status_code == 200

    snap_a = client.post(
        "/snapshots",
        json={
            "connection_id": conn["id"], "report_id": "R001", "page_id": "P001", "visual_id": "V001",
            "filters": [
                {"field": "Calendar[Year]", "operator": "equals", "values": ["2025"]},
                {"field": "Calendar[Month]", "operator": "equals", "values": ["March"]},
            ],
        },
    ).json()
    snap_b = client.post(
        "/snapshots",
        json={
            "connection_id": conn["id"], "report_id": "R001", "page_id": "P001", "visual_id": "V001",
            "filters": [
                {"field": "Calendar[Year]", "operator": "equals", "values": ["2026"]},
                {"field": "Calendar[Month]", "operator": "equals", "values": ["March"]},
            ],
        },
    ).json()

    inv = client.post(
        "/investigations",
        json={
            "question": "Why does Stock Value differ between March 2025 and March 2026?",
            "connection_id": conn["id"],
            "snapshot_id_a": snap_a["snapshot_id"],
            "snapshot_id_b": snap_b["snapshot_id"],
            "lineage_root_id": "V001",
            "impact_target_id": "C_BUYPRICE",
        },
    ).json()

    assert inv["confidence"] in (Confidence.PARTIALLY_VERIFIED.value, Confidence.VERIFIED.value)

    export = client.post("/exports/excel", json={"investigation_id": inv["investigation_id"]}).json()
    assert os.path.exists(export["path"])
    os.remove(export["path"])
