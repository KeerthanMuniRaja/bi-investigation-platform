from __future__ import annotations

import pytest

from app.connectors.registry import ConnectionRecord
from app.store.database import Store, reset_store
from tests.fakes import MockConnector


CLOUD_SETTINGS = ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET",
                  "BIP_ALLOWED_EMAILS", "BIP_ALLOWED_DOMAINS", "BIP_STORAGE_BUCKET")


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Every test gets its own SQLite database and upload/export folders —
    never the cloud database or storage, even if the shell has them set."""
    for name in CLOUD_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))
    return reset_store(str(tmp_path / "data"))


def add_mock_connection(store: Store) -> str:
    """Register a connection backed by the fixture workspace (no network).
    The record is a plain Power BI one; the cached connector is the fake."""
    record = ConnectionRecord(id=store.next_id("CONN"), name="Fixture workspace", platform="powerbi", workspace_id="WS001")
    store.connection_records[record.id] = record
    store._connectors[record.id] = MockConnector()
    return record.id


@pytest.fixture()
def mock_connection(isolated_store) -> str:
    return add_mock_connection(isolated_store)
