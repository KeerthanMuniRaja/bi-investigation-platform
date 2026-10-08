from __future__ import annotations

import pytest

from app.connectors.registry import ConnectionRecord
from app.store.database import Store, reset_store
from tests.fakes import MockConnector


CLOUD_SETTINGS = ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "BIP_STORAGE_BUCKET",
                  "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_ENDPOINT", "BIP_WEB_URL", "BIP_CORS_ORIGINS",
                  "BIP_AUTH", "BIP_SECRET_KEY", "BIP_ALLOW_SIGNUP", "BIP_SESSION_DAYS",
                  "RENDER", "BIP_FILE_STORAGE", "BIP_TEMPORARY_DASHBOARDS", "BIP_SESSION_IDLE_MINUTES", "BIP_MEMORY_LIMIT_MB",
                  "BIP_KEEP_MODEL_FILES")


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Every test gets its own SQLite database and upload/export folders —
    never the cloud database or storage, even if the shell has them set."""
    for name in CLOUD_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))
    from app.services import sessions

    sessions._touched.clear()
    monkeypatch.setattr(sessions, "maybe_sweep", lambda store: None)   # tests call sweep() themselves
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
