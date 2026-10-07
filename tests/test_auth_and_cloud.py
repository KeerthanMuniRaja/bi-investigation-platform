"""Username + password sign-in: accounts in the database, passwords hashed,
tokens checked, every person sees only their own work, password changes
end other sessions, guessing is throttled; older databases are upgraded;
uploads go to cloud storage when it's configured."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password, set_owner, throttle, verify_password
from app.errors import NotFoundError
from app.store.blobs import SupabaseBlobs
from app.store.database import SqliteDocumentDB, reset_store
from tests.conftest import add_mock_connection

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture()
def client(monkeypatch, isolated_store):
    monkeypatch.setenv("BIP_AUTH", "password")
    monkeypatch.setenv("BIP_SECRET_KEY", "test-signing-key-that-is-long-enough-for-hs256")
    throttle._fails.clear()
    from app.main import app

    return TestClient(app)


def signup(c: TestClient, username: str, password: str = "correct horse", **extra) -> dict:
    r = c.post("/auth/signup", json={"username": username, "password": password, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(session: dict) -> dict:
    return {"Authorization": f"Bearer {session['token']}"}


def upload(c: TestClient, session: dict, name: str = "tableau_sales_by_region.csv") -> dict:
    with open(SAMPLES / name, "rb") as f:
        r = c.post("/files", files={"files": (name, f, "text/csv")}, headers=bearer(session))
    assert r.status_code == 200, r.text
    return r.json()[0]


def test_passwords_are_hashed_never_stored():
    stored = hash_password("correct horse")
    assert "correct horse" not in stored and stored.startswith("scrypt$")
    assert verify_password("correct horse", stored) and not verify_password("wrong horse", stored)
    assert hash_password("correct horse") != stored          # salted


def test_create_account_sign_in_and_use_the_app(client, isolated_store):
    c = client
    info = c.get("/api/info").json()
    assert info["auth"] is True and info["signup"] is True
    assert c.get("/connections").status_code == 401                     # data needs a sign-in
    s = signup(c, "Keerthan", display_name="Keerthan M")
    assert s["user"] == {"username": "keerthan", "name": "Keerthan M"}  # usernames are case-insensitive
    row = isolated_store.db.get_user("keerthan")
    assert row["password_hash"].startswith("scrypt$") and "correct horse" not in row["password_hash"]
    assert c.get("/me", headers=bearer(s)).json() == {"auth": True, "username": "keerthan", "name": "Keerthan M"}
    assert c.get("/connections", headers=bearer(s)).status_code == 200
    # sign in again later
    again = c.post("/auth/login", json={"username": "KEERTHAN", "password": "correct horse"})
    assert again.status_code == 200 and c.get("/connections", headers=bearer(again.json())).status_code == 200


def test_bad_sign_ins_are_refused(client):
    c = client
    signup(c, "ann")
    assert c.post("/auth/signup", json={"username": "ann", "password": "another one"}).status_code == 409   # taken
    assert c.post("/auth/signup", json={"username": "x", "password": "long enough"}).status_code == 400    # too short a name
    assert c.post("/auth/signup", json={"username": "bob", "password": "short"}).status_code == 400        # weak password
    wrong = c.post("/auth/login", json={"username": "ann", "password": "not it at all"})
    nobody = c.post("/auth/login", json={"username": "ghost", "password": "not it at all"})
    assert wrong.status_code == nobody.status_code == 401 and wrong.json() == nobody.json()   # no account probing
    assert c.get("/connections", headers={"Authorization": "Bearer forged.token.here"}).status_code == 401


def test_guessing_is_throttled(client):
    c = client
    signup(c, "ann")
    for _ in range(8):
        assert c.post("/auth/login", json={"username": "ann", "password": "guess guess"}).status_code == 401
    blocked = c.post("/auth/login", json={"username": "ann", "password": "correct horse"})
    assert blocked.status_code == 429 and "Try again" in blocked.json()["detail"]


def test_each_person_sees_only_their_own_work(client):
    c = client
    ann, bob = signup(c, "ann"), signup(c, "bob")
    mine = upload(c, ann)
    assert [f["file_id"] for f in c.get("/files", headers=bearer(ann)).json()] == [mine["file_id"]]
    assert c.get("/files", headers=bearer(bob)).json() == []
    assert c.get(f"/files/{mine['file_id']}/preview", headers=bearer(bob)).status_code == 404
    assert c.delete(f"/files/{mine['file_id']}", headers=bearer(bob)).status_code == 404
    assert c.get("/stats", headers=bearer(bob)).json()["files"] == 0
    assert c.get(f"/files/{mine['file_id']}/preview", headers=bearer(ann)).status_code == 200


def test_changing_the_password_ends_other_sessions(client, isolated_store):
    c = client
    first = signup(c, "ann")
    other = c.post("/auth/login", json={"username": "ann", "password": "correct horse"}).json()
    assert c.post("/auth/password", json={"current_password": "nope nope", "new_password": "brand new pass"}, headers=bearer(first)).status_code == 400
    r = c.post("/auth/password", json={"current_password": "correct horse", "new_password": "brand new pass"}, headers=bearer(first))
    assert r.status_code == 200
    assert c.get("/connections", headers=bearer(other)).status_code == 401       # old sessions are over
    assert c.get("/connections", headers=bearer(r.json())).status_code == 200    # the new one works
    assert c.post("/auth/login", json={"username": "ann", "password": "brand new pass"}).status_code == 200
    # a switched-off account can't sign in, and its sessions stop working
    isolated_store.db.update_user("ann", disabled=True, token_version=99)
    assert c.post("/auth/login", json={"username": "ann", "password": "brand new pass"}).status_code == 403
    assert c.get("/connections", headers=bearer(r.json())).status_code == 401


def test_closed_sign_up_still_allows_the_first_account(client, monkeypatch):
    monkeypatch.setenv("BIP_ALLOW_SIGNUP", "false")
    c = client
    assert c.get("/api/info").json()["signup"] is False
    signup(c, "admin")                                                            # the very first account
    closed = c.post("/auth/signup", json={"username": "someone", "password": "long enough"})
    assert closed.status_code == 403


def test_shared_caches_never_leak_another_persons_connection(isolated_store):
    store = isolated_store
    reset = set_owner("ann")
    try:
        cid = add_mock_connection(store)       # also warms the shared connector cache
        assert store.connector(cid) is not None
        set_owner("bob")
        for call in (store.connector, store.graph_for, store.overview):
            with pytest.raises(NotFoundError):
                call(cid)
        with pytest.raises(NotFoundError):     # can't overwrite it either
            store.connection_records[cid] = store.connection_records.model(id=cid, name="hijack", platform="powerbi")
    finally:
        set_owner("local")
        del reset


def test_sign_in_off_keeps_the_single_local_workspace():
    from app.main import app

    c = TestClient(app)
    assert c.get("/api/info").json()["auth"] is False
    assert c.get("/me").json() == {"auth": False, "username": None, "name": "Local workspace"}
    assert c.get("/connections").status_code == 200


def test_databases_from_before_sign_in_are_upgraded(tmp_path):
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE documents (collection TEXT NOT NULL, id TEXT NOT NULL, created_at TEXT NOT NULL, "
                 "data TEXT NOT NULL, PRIMARY KEY (collection, id))")
    conn.execute("INSERT INTO documents VALUES ('files', 'FILE0001', '2026-01-01', '{}')")
    conn.commit()
    conn.close()
    db = SqliteDocumentDB(str(path))
    assert db.get("files", "local", "FILE0001") == "{}"          # kept, in the local workspace
    assert db.get("files", "ann", "FILE0001") is None
    assert db.count_users() == 0                                 # and it has an accounts table now


# ------------------------------------------------------------ cloud storage
class FakeStorage:
    """Just enough of the Supabase Storage REST API."""

    def __init__(self):
        self.buckets: set[str] = set()
        self.objects: dict[str, bytes] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json

        assert request.headers["authorization"] == "Bearer service-key"
        path = request.url.path.removeprefix("/storage/v1")
        if request.method == "GET" and path.startswith("/bucket/"):
            return httpx.Response(200 if path.split("/")[2] in self.buckets else 404, json={})
        if request.method == "POST" and path == "/bucket":
            self.buckets.add(json.loads(request.content)["id"])
            return httpx.Response(200, json={})
        if path.startswith("/object/"):
            key = path.removeprefix("/object/")
            if request.method == "POST":
                self.objects[key] = request.read()
                return httpx.Response(200, json={"Key": key})
            if request.method == "GET":
                return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
            if request.method == "DELETE":
                for k in json.loads(request.content)["prefixes"]:
                    self.objects.pop(f"{key}/{k}", None)
                return httpx.Response(200, json=[])
        return httpx.Response(400)


def test_uploads_are_kept_in_cloud_storage_and_fetched_back(tmp_path):
    fake = FakeStorage()
    blobs = SupabaseBlobs("https://example.supabase.co", "service-key", "bip-files", client=httpx.Client(transport=httpx.MockTransport(fake.handler)))
    store = reset_store(str(tmp_path / "data"))
    store.blobs = blobs
    from app.services import recon_service

    reset = set_owner("ann")
    try:
        with open(SAMPLES / "tableau_sales_by_region.csv", "rb") as f:
            record = recon_service.save_upload(store, "tableau_sales_by_region.csv", f, "text/csv")
        assert "bip-files" in fake.buckets                               # private bucket created on first use
        assert record.blob_key == f"ann/{record.stored_as}"
        assert fake.objects[f"bip-files/{record.blob_key}"] == (SAMPLES / "tableau_sales_by_region.csv").read_bytes()
        # a fresh server (empty disk) downloads it on first use
        (store.upload_dir / record.stored_as).unlink()
        _, path = store.resolve_file(record.file_id)
        assert path.read_bytes() == (SAMPLES / "tableau_sales_by_region.csv").read_bytes()
        # deleting the file removes the cloud copy too
        assert recon_service.delete_file(store, record.file_id)
        assert f"bip-files/{record.blob_key}" not in fake.objects
    finally:
        set_owner("local")
        del reset
