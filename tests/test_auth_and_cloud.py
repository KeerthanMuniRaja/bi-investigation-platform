"""Sign-in with Supabase (Google): tokens are checked, every person sees only
their own work, the allow-list is enforced; older databases are upgraded;
uploads go to cloud storage when it's configured."""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth import set_owner
from app.errors import NotFoundError
from app.store.blobs import SupabaseBlobs
from app.store.database import SqliteDocumentDB, reset_store
from tests.conftest import add_mock_connection

SECRET = "test-jwt-secret-with-enough-length-for-hs256"
SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def token(email: str, *, expires_in: int = 3600, secret: str = SECRET) -> str:
    claims = {"sub": str(uuid.uuid4()), "email": email, "aud": "authenticated", "role": "authenticated",
              "exp": int(time.time()) + expires_in, "user_metadata": {"full_name": email.split("@")[0].title()}}
    return jwt.encode(claims, secret, algorithm="HS256")


def auth(email: str) -> dict:
    return {"Authorization": f"Bearer {token(email)}"}


@pytest.fixture()
def signed_in_app(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    from app.main import app

    return TestClient(app)


def upload(client: TestClient, email: str, name: str = "tableau_sales_by_region.csv") -> dict:
    with open(SAMPLES / name, "rb") as f:
        r = client.post("/files", files={"files": (name, f, "text/csv")}, headers=auth(email))
    assert r.status_code == 200, r.text
    return r.json()[0]


def test_data_routes_need_a_valid_sign_in(signed_in_app):
    c = signed_in_app
    assert c.get("/health").status_code == 200                       # open
    assert c.get("/api/info").json()["auth"] is True
    assert c.get("/connections").status_code == 401                   # no token
    assert c.get("/stats").status_code == 401
    assert c.get("/connections", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    expired = c.get("/connections", headers={"Authorization": f"Bearer {token('a@x.com', expires_in=-60)}"})
    assert expired.status_code == 401 and "expired" in expired.json()["detail"]
    forged = c.get("/connections", headers={"Authorization": f"Bearer {token('a@x.com', secret='wrong-secret-of-similar-length-xx')}"})
    assert forged.status_code == 401
    ok = c.get("/me", headers=auth("Ann@Example.com"))
    assert ok.status_code == 200 and ok.json()["email"] == "ann@example.com" and ok.json()["auth"] is True


def test_each_person_sees_only_their_own_work(signed_in_app):
    c = signed_in_app
    mine = upload(c, "ann@example.com")
    assert [f["file_id"] for f in c.get("/files", headers=auth("ann@example.com")).json()] == [mine["file_id"]]
    # someone else: nothing listed, and the id doesn't work either
    assert c.get("/files", headers=auth("bob@example.com")).json() == []
    assert c.get(f"/files/{mine['file_id']}/preview", headers=auth("bob@example.com")).status_code == 404
    assert c.delete(f"/files/{mine['file_id']}", headers=auth("bob@example.com")).status_code == 404
    assert c.get("/stats", headers=auth("bob@example.com")).json()["files"] == 0
    # ...and Ann's file is untouched
    assert c.get(f"/files/{mine['file_id']}/preview", headers=auth("ann@example.com")).status_code == 200
    assert c.get("/stats", headers=auth("ann@example.com")).json()["files"] == 1


def test_shared_caches_never_leak_another_persons_connection(isolated_store):
    store = isolated_store
    reset = set_owner("ann@example.com")
    try:
        cid = add_mock_connection(store)       # also warms the shared connector cache
        assert store.connector(cid) is not None
        set_owner("bob@example.com")
        for call in (store.connector, store.graph_for, store.overview):
            with pytest.raises(NotFoundError):
                call(cid)
        with pytest.raises(NotFoundError):     # can't overwrite it either
            store.connection_records[cid] = store.connection_records.model(id=cid, name="hijack", platform="powerbi")
    finally:
        set_owner("local")
        del reset


def test_allow_list_limits_who_can_sign_in(signed_in_app, monkeypatch):
    monkeypatch.setenv("BIP_ALLOWED_DOMAINS", "jmangroup.com")
    monkeypatch.setenv("BIP_ALLOWED_EMAILS", "friend@gmail.com")
    c = signed_in_app
    assert c.get("/connections", headers=auth("someone@jmangroup.com")).status_code == 200
    assert c.get("/connections", headers=auth("friend@gmail.com")).status_code == 200
    denied = c.get("/connections", headers=auth("stranger@gmail.com"))
    assert denied.status_code == 403 and "isn't allowed" in denied.json()["detail"]


def test_sign_in_off_keeps_the_single_local_workspace():
    from app.main import app

    c = TestClient(app)
    assert c.get("/me").json() == {"auth": False, "email": None, "name": "Local workspace"}
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
    assert db.get("files", "ann@example.com", "FILE0001") is None


# ------------------------------------------------------------ cloud storage
class FakeStorage:
    """Just enough of the Supabase Storage REST API."""

    def __init__(self):
        self.buckets: set[str] = set()
        self.objects: dict[str, bytes] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer service-key"
        path = request.url.path.removeprefix("/storage/v1")
        if request.method == "GET" and path.startswith("/bucket/"):
            return httpx.Response(200 if path.split("/")[2] in self.buckets else 404, json={})
        if request.method == "POST" and path == "/bucket":
            self.buckets.add(__import__("json").loads(request.content)["id"])
            return httpx.Response(200, json={})
        if path.startswith("/object/"):
            key = path.removeprefix("/object/")
            if request.method == "POST":
                self.objects[key] = request.read()
                return httpx.Response(200, json={"Key": key})
            if request.method == "GET":
                return httpx.Response(200, content=self.objects[key]) if key in self.objects else httpx.Response(404)
            if request.method == "DELETE":
                for k in __import__("json").loads(request.content)["prefixes"]:
                    self.objects.pop(f"{key}/{k}", None)
                return httpx.Response(200, json=[])
        return httpx.Response(400)


def test_uploads_are_kept_in_cloud_storage_and_fetched_back(tmp_path, monkeypatch):
    fake = FakeStorage()
    blobs = SupabaseBlobs("https://example.supabase.co", "service-key", "bip-files", client=httpx.Client(transport=httpx.MockTransport(fake.handler)))
    store = reset_store(str(tmp_path / "data"))
    store.blobs = blobs
    from app.services import recon_service

    reset = set_owner("ann@example.com")
    try:
        with open(SAMPLES / "tableau_sales_by_region.csv", "rb") as f:
            record = recon_service.save_upload(store, "tableau_sales_by_region.csv", f, "text/csv")
        assert "bip-files" in fake.buckets                               # private bucket created on first use
        assert record.blob_key == f"ann_example.com/{record.stored_as}"
        assert fake.objects[f"bip-files/{record.blob_key}"] == (SAMPLES / "tableau_sales_by_region.csv").read_bytes()
        # a fresh server (empty disk) downloads it on first use
        local = store.upload_dir / record.stored_as
        local.unlink()
        _, path = store.resolve_file(record.file_id)
        assert path.read_bytes() == (SAMPLES / "tableau_sales_by_region.csv").read_bytes()
        # deleting the file removes the cloud copy too
        assert recon_service.delete_file(store, record.file_id)
        assert f"bip-files/{record.blob_key}" not in fake.objects
    finally:
        set_owner("local")
        del reset
