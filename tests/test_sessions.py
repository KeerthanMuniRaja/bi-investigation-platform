"""Power BI dashboards last one sign-in session: signing out, going idle or
the sign-in expiring hard-deletes the dashboard and everything read from its
file; Excel/CSV files stay. Also: where files are kept on hosts without a
lasting disk (Render), and the memory guard for small servers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import _owner, set_owner
from app.connectors.model_file import LoadedModel, model_to_snapshot
from app.connectors.registry import ConnectionRecord
from app.models.data_reconciliation import DataReconciliation, ReconciliationConfig, ReconciliationDefinition
from app.models.dataset import Dataset, DatasetSpec
from app.models.investigation import Investigation
from app.models.snapshot import VisualSnapshot
from app.services import sessions
from app.store.database import FileRecord, ModelSnapshot
from tests.test_auth_and_cloud import bearer, client, signup, upload  # noqa: F401 - fixtures


def _sid(session: dict) -> str:
    from app.auth import read_token

    return read_token(session["token"])[2]


def add_dashboard(store, owner: str, session_id: str | None, n: int = 1) -> dict:
    """A Power BI dashboard as an upload leaves it, plus things built on it."""
    token = set_owner(owner)
    try:
        cid, fid = f"CONN{n:04d}", f"FILE{900 + n:04d}"
        (store.upload_dir / f"{fid}_x_Sales.pbix").write_bytes(b"pbix bytes")
        store.files[fid] = FileRecord(file_id=fid, name="Sales.pbix", stored_as=f"{fid}_x_Sales.pbix", size=10, kind="model")
        store.connection_records[cid] = ConnectionRecord(id=cid, name="Sales", platform="pbix", file_ids=[fid], workspace_id="file",
                                                         session_id=session_id)
        store.model_snapshots[cid] = ModelSnapshot(connection_id=cid, data=model_to_snapshot(LoadedModel(name="Sales", file_format="pbix")))
        spec = DatasetSpec(kind="model_table", connection_id=cid, table="Sales")
        store.datasets[f"DSET{n:04d}"] = Dataset(dataset_id=f"DSET{n:04d}", name="Sales rows", source=spec, rows=[{"a": 1}], row_count=1)
        store.datasets[f"DSET{n + 50:04d}"] = Dataset(dataset_id=f"DSET{n + 50:04d}", name="copy",
                                                      source=DatasetSpec(kind="dataset", dataset_id=f"DSET{n:04d}"), rows=[{"a": 1}])
        store.snapshots[f"SNAP{n:04d}"] = VisualSnapshot(snapshot_id=f"SNAP{n:04d}", connection_id=cid, report_id="report",
                                                         page_id="p", visual_id="v", visual_title="Total")
        store.data_reconciliations[f"DREC{n:04d}"] = DataReconciliation(
            reconciliation_id=f"DREC{n:04d}", name="r", left_dataset_id=f"DSET{n:04d}", right_dataset_id="DSET0999",
            config=ReconciliationConfig())
        store.definitions[f"TMPL{n:04d}"] = ReconciliationDefinition(
            definition_id=f"TMPL{n:04d}", name="t", left=spec, right=DatasetSpec(kind="file", file_id="FILE0001"),
            config=ReconciliationConfig())
        store.investigations[f"INV{n:04d}"] = Investigation(investigation_id=f"INV{n:04d}", question="why?", connection_id=cid)
        return {"cid": cid, "fid": fid}
    finally:
        _owner.reset(token)


def counts(store, owner: str) -> dict:
    token = set_owner(owner)
    try:
        return {name: len(getattr(store, name)) for name in
                ("connection_records", "model_snapshots", "datasets", "snapshots", "data_reconciliations", "definitions", "investigations")} \
            | {"model_files": sum(1 for f in store.files.values() if f.kind == "model"),
               "table_files": sum(1 for f in store.files.values() if f.kind != "model")}
    finally:
        _owner.reset(token)


EMPTY = {"connection_records": 0, "model_snapshots": 0, "datasets": 0, "snapshots": 0, "data_reconciliations": 0,
         "definitions": 0, "investigations": 0, "model_files": 0}


def test_signing_out_hard_deletes_the_sessions_dashboards(client, isolated_store):
    c, store = client, isolated_store
    s = signup(c, "ann")
    upload(c, s)                                            # an Excel/CSV file: stays
    add_dashboard(store, "ann", _sid(s))
    before = counts(store, "ann")
    assert before["connection_records"] == 1 and before["datasets"] == 2 and before["table_files"] == 1
    assert c.get("/connections", headers=bearer(s)).json()[0]["id"] == "CONN0001"

    r = c.post("/auth/logout", headers=bearer(s))
    assert r.status_code == 200 and r.json() == {"signed_out": True, "dashboards_removed": 1}
    after = counts(store, "ann")
    assert {k: after[k] for k in EMPTY} == EMPTY and after["table_files"] == 1
    assert not list(store.upload_dir.glob("*Sales.pbix"))   # the file itself is gone from disk
    # the signed-out token is dead even though it hasn't expired
    assert c.get("/connections", headers=bearer(s)).status_code == 401


def test_only_the_ending_session_loses_its_dashboards(client, isolated_store):
    c, store = client, isolated_store
    laptop = signup(c, "ann")
    phone = c.post("/auth/login", json={"username": "ann", "password": "correct horse"}).json()
    add_dashboard(store, "ann", _sid(laptop), n=1)
    add_dashboard(store, "ann", _sid(phone), n=2)
    assert c.post("/auth/logout", headers=bearer(laptop)).json()["dashboards_removed"] == 1
    ids = [x["id"] for x in c.get("/connections", headers=bearer(phone)).json()]
    assert ids == ["CONN0002"]


def test_an_idle_session_ends_and_its_dashboards_go(client, isolated_store, monkeypatch):
    c, store = client, isolated_store
    s = signup(c, "ann")
    add_dashboard(store, "ann", _sid(s))
    assert c.get("/me", headers=bearer(s)).status_code == 200
    # away for longer than the idle limit (the open website pings every few minutes)
    token = set_owner("ann")
    rec = store.sessions[_sid(s)]
    rec.last_seen_at = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    store.sessions[_sid(s)] = rec
    _owner.reset(token)
    sessions._touched.clear()
    r = c.get("/me", headers=bearer(s))
    assert r.status_code == 401 and "sign in again" in r.json()["detail"]
    assert {k: v for k, v in counts(store, "ann").items() if k in EMPTY} == EMPTY


def test_the_clean_up_removes_dashboards_of_ended_sessions_for_everyone(client, isolated_store):
    c, store = client, isolated_store
    ann, bob = signup(c, "ann"), signup(c, "bob")
    add_dashboard(store, "ann", _sid(ann), n=1)
    add_dashboard(store, "bob", _sid(bob), n=2)
    add_dashboard(store, "bob", None, n=3)                    # from before dashboards were temporary
    token = set_owner("ann")
    rec = store.sessions[_sid(ann)]
    rec.created_at = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()   # sign-in expired
    store.sessions[_sid(ann)] = rec
    _owner.reset(token)

    assert sessions.sweep(store) == {"sessions_ended": 1, "dashboards_removed": 2}
    assert counts(store, "ann")["connection_records"] == 0
    assert [x["id"] for x in c.get("/connections", headers=bearer(bob)).json()] == ["CONN0002"]   # bob's live session keeps his


def test_uploads_are_tied_to_the_session(client, isolated_store, monkeypatch):
    from app.engine.graph_store import GraphStore
    from app.services import recon_service

    c, store = client, isolated_store
    s = signup(c, "ann")
    monkeypatch.setattr(store, "sync", lambda cid, ws: (GraphStore(), []))
    from app.auth import _session

    o, t = set_owner("ann"), _session.set(_sid(s))
    try:
        store.files["FILE0001"] = FileRecord(file_id="FILE0001", name="M.pbix", stored_as="FILE0001_M.pbix", size=1, kind="model")
        cid, _ = recon_service.open_model_file(store, store.files["FILE0001"])
        assert store.connection_records[cid].session_id == _sid(s)
    finally:
        _session.reset(t)
        _owner.reset(o)


def test_dashboards_can_be_kept_and_local_mode_keeps_them(client, isolated_store, monkeypatch):
    c, store = client, isolated_store
    monkeypatch.setenv("BIP_TEMPORARY_DASHBOARDS", "false")
    s = signup(c, "ann")
    add_dashboard(store, "ann", _sid(s))
    assert c.post("/auth/logout", headers=bearer(s)).json()["dashboards_removed"] == 0
    assert counts(store, "ann")["connection_records"] == 1
    monkeypatch.delenv("BIP_AUTH")
    monkeypatch.delenv("BIP_TEMPORARY_DASHBOARDS")
    assert not sessions.temporary_dashboards()               # no sign-in: nothing is temporary
    assert c.get("/api/info").json()["temporary_dashboards"] is False


def test_info_tells_the_website(client):
    info = client.get("/api/info").json()
    assert info["temporary_dashboards"] is True and info["idle_minutes"] == 30


def test_tokens_from_before_sessions_must_sign_in_again(client, isolated_store):
    import jwt

    signup(client, "ann")
    old = jwt.encode({"sub": "ann", "ver": 0, "exp": 4102444800, "aud": "bip"}, "test-signing-key-that-is-long-enough-for-hs256", algorithm="HS256")
    assert client.get("/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401


# ------------------------------------------------------------- hosting
def test_render_needs_a_database_and_a_secret_key(monkeypatch, tmp_path):
    from fastapi import HTTPException

    from app.auth import _secret_key
    from app.store.database import ephemeral_disk, open_database

    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("BIP_DATA_DIR", raising=False)
    assert ephemeral_disk()
    with pytest.raises(RuntimeError, match="On Render the data must live in Postgres"):
        open_database(tmp_path)
    with pytest.raises(HTTPException, match="Render"):
        _secret_key()
    monkeypatch.setenv("BIP_DATA_DIR", str(tmp_path))      # a persistent disk is mounted there
    assert not ephemeral_disk()
    open_database(tmp_path).close() if hasattr(open_database(tmp_path), "close") else None


def test_files_are_kept_in_the_database_when_no_bucket_is_set(monkeypatch):
    from app.store.blobs import DatabaseBlobs, LocalBlobs, S3Blobs, blobs_from_env

    assert isinstance(blobs_from_env(), LocalBlobs)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ss@host/db")
    b = blobs_from_env()
    assert isinstance(b, DatabaseBlobs) and b.enabled and not b.direct_upload and b.name == "database"
    assert "p%40ss" in b.dsn                                 # special characters in the password handled
    monkeypatch.setenv("BIP_FILE_STORAGE", "local")
    assert isinstance(blobs_from_env(), LocalBlobs)
    monkeypatch.setenv("BIP_FILE_STORAGE", "r2")
    with pytest.raises(RuntimeError, match="BIP_FILE_STORAGE=r2"):
        blobs_from_env()
    for k, v in {"R2_BUCKET": "b", "R2_ACCESS_KEY_ID": "a", "R2_SECRET_ACCESS_KEY": "s", "R2_ACCOUNT_ID": "acct"}.items():
        monkeypatch.setenv(k, v)
    assert isinstance(blobs_from_env(), S3Blobs) and blobs_from_env().direct_upload


def test_power_bi_files_are_not_copied_into_the_database(isolated_store, monkeypatch):
    from app.services import recon_service

    store = isolated_store
    put: list[str] = []

    class FakeDb:
        enabled, direct_upload, name = True, False, "database"

        def put(self, owner, name, path):
            put.append(name)
            return f"local/{name}"

    store.blobs = FakeDb()
    rec = recon_service.save_model_upload(store, "Sales.pbix", b"pbix bytes")
    assert rec.blob_key is None and put == []               # read once, then deleted: no copy needed
    monkeypatch.setenv("BIP_KEEP_MODEL_FILES", "true")
    rec = recon_service.save_model_upload(store, "Sales.pbix", b"pbix bytes")
    assert rec.blob_key and len(put) == 1


def test_memory_limit_setting(monkeypatch):
    from app.connectors.model_file import _preview_cost, memory_limit

    monkeypatch.setenv("BIP_MEMORY_LIMIT_MB", "512")
    assert memory_limit() == 512 * 1024 * 1024
    assert _preview_cost(61_000_000, 1_000_000) > 300_000_000   # the measured case: a 61 MB segment took ~330 MB
