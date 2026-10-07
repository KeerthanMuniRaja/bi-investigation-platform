"""
Persistence: a small SQLite document store.

Section 37 says not to over-commit to a storage technology before the
workflow is proven, so every object is stored as a JSON document keyed by
(collection, id) — the pydantic models *are* the schema. Moving to
Postgres later means reimplementing DocumentDB's handful of methods; no
route or engine code touches SQL.

What is *not* persisted, on purpose:
  - connection secrets (client secret, PAT secret) — kept in process
    memory only, or read from environment variables;
  - the lineage graph — rebuilt from the connector on first use after a
    restart (it's derived data).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Generic, Iterator, Optional, Type, TypeVar

from pydantic import BaseModel, Field

from app.connectors.base import BIConnector
from app.connectors.registry import SECRET_FIELDS, ConnectionRecord, build_connector
from app.engine.extraction import build_full_graph
from app.engine.graph_store import GraphStore
from app.errors import NotFoundError
from app.models.data_reconciliation import DataReconciliation, ReconciliationDefinition
from app.models.dataset import Dataset
from app.models.graph import utcnow
from app.models.investigation import Investigation
from app.models.reconciliation import Reconciliation
from app.models.snapshot import VisualSnapshot

M = TypeVar("M", bound=BaseModel)


class FileRecord(BaseModel):
    file_id: str
    name: str
    stored_as: str
    size: int
    content_type: Optional[str] = None
    kind: str = "table"  # table (Excel/CSV) | model (Power BI file)
    sheets: list[dict] = Field(default_factory=list)
    uploaded_at: str = Field(default_factory=lambda: utcnow().isoformat())


class DocumentDB:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS documents (collection TEXT NOT NULL, id TEXT NOT NULL, "
                "created_at TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (collection, id))"
            )
            self._conn.execute("CREATE TABLE IF NOT EXISTS counters (prefix TEXT PRIMARY KEY, value INTEGER NOT NULL)")
            self._conn.commit()

    def put(self, collection: str, doc_id: str, data: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO documents (collection, id, created_at, data) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(collection, id) DO UPDATE SET data = excluded.data",
                (collection, doc_id, utcnow().isoformat(), data),
            )
            self._conn.commit()

    def get(self, collection: str, doc_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT data FROM documents WHERE collection = ? AND id = ?", (collection, doc_id)).fetchone()
        return row[0] if row else None

    def delete(self, collection: str, doc_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM documents WHERE collection = ? AND id = ?", (collection, doc_id))
            self._conn.commit()
        return cur.rowcount > 0

    def all(self, collection: str) -> list[tuple[str, str]]:
        with self._lock:
            return self._conn.execute(
                "SELECT id, data FROM documents WHERE collection = ? ORDER BY created_at DESC, rowid DESC", (collection,)
            ).fetchall()

    def count(self, collection: str) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM documents WHERE collection = ?", (collection,)).fetchone()[0]

    def next_counter(self, prefix: str) -> int:
        with self._lock:
            self._conn.execute(
                "INSERT INTO counters (prefix, value) VALUES (?, 1) ON CONFLICT(prefix) DO UPDATE SET value = value + 1",
                (prefix,),
            )
            self._conn.commit()
            return self._conn.execute("SELECT value FROM counters WHERE prefix = ?", (prefix,)).fetchone()[0]


class Collection(Generic[M]):
    """dict-like access to one collection of pydantic documents. Objects
    are copies: after mutating one, assign it back to save it."""

    def __init__(self, db: DocumentDB, name: str, model: Type[M], label: Optional[str] = None):
        self.db, self.name, self.model = db, name, model
        self.label = label or name.rstrip("s").replace("_", " ")

    def get(self, doc_id: str, default: Optional[M] = None) -> Optional[M]:
        raw = self.db.get(self.name, doc_id)
        return self.model.model_validate_json(raw) if raw is not None else default

    def __getitem__(self, doc_id: str) -> M:
        item = self.get(doc_id)
        if item is None:
            raise NotFoundError(f"{self.label} {doc_id} not found")
        return item

    def update(self, doc_id: str, change: Callable[[M], None]) -> M:
        """Atomic read-modify-write: concurrent requests appending to the
        same document (template runs, investigation notes) don't overwrite
        each other's changes."""
        with self.db._lock:
            obj = self[doc_id]
            change(obj)
            self[doc_id] = obj
            return obj

    def __setitem__(self, doc_id: str, obj: M) -> None:
        self.db.put(self.name, doc_id, obj.model_dump_json())

    def __delitem__(self, doc_id: str) -> None:
        if not self.db.delete(self.name, doc_id):
            raise NotFoundError(f"{self.label} {doc_id} not found")

    def delete(self, doc_id: str) -> bool:
        return self.db.delete(self.name, doc_id)

    def __contains__(self, doc_id: str) -> bool:
        return self.db.get(self.name, doc_id) is not None

    def values(self) -> list[M]:
        return [self.model.model_validate_json(data) for _, data in self.db.all(self.name)]

    def items(self) -> Iterator[tuple[str, M]]:
        for doc_id, data in self.db.all(self.name):
            yield doc_id, self.model.model_validate_json(data)

    def __len__(self) -> int:
        return self.db.count(self.name)


class Store:
    def __init__(self, data_dir: Optional[str] = None, db_path: Optional[str] = None):
        self.data_dir = Path(data_dir or os.environ.get("BIP_DATA_DIR", "./data")).resolve()
        self.upload_dir = self.data_dir / "uploads"
        self.export_dir = Path(os.environ.get("EXPORT_DIR", str(self.data_dir / "exports"))).resolve()
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.db = DocumentDB(db_path or str(self.data_dir / "bip.sqlite3"))

        self.connection_records = Collection(self.db, "connections", ConnectionRecord, "connection")
        self.files = Collection(self.db, "files", FileRecord, "file")
        self.datasets = Collection(self.db, "datasets", Dataset, "dataset")
        self.snapshots = Collection(self.db, "snapshots", VisualSnapshot, "snapshot")
        self.reconciliations = Collection(self.db, "reconciliations", Reconciliation, "reconciliation")
        self.data_reconciliations = Collection(self.db, "data_reconciliations", DataReconciliation, "reconciliation")
        self.definitions = Collection(self.db, "definitions", ReconciliationDefinition, "template")
        self.investigations = Collection(self.db, "investigations", Investigation, "investigation")

        self._connectors: dict[str, BIConnector] = {}
        self._secrets: dict[str, dict[str, str]] = {}
        self._graphs: dict[str, GraphStore] = {}
        self._overviews: dict[str, dict] = {}
        self._lock = threading.RLock()
        # one lock per connection: parallel requests after a restart must not
        # each re-read a (possibly 1 GB+) model file and rebuild its graph
        self._conn_locks: dict[str, threading.RLock] = {}

    def _conn_lock(self, connection_id: str) -> threading.RLock:
        with self._lock:
            return self._conn_locks.setdefault(connection_id, threading.RLock())

    def next_id(self, prefix: str) -> str:
        return f"{prefix}{self.db.next_counter(prefix):04d}"

    # --------------------------------------------------------- connections
    def add_connection(self, record: ConnectionRecord, secrets: dict[str, Optional[str]]) -> ConnectionRecord:
        self._secrets[record.id] = {k: v for k, v in secrets.items() if k in SECRET_FIELDS and v}
        connector = build_connector(record, self._secrets[record.id], self.resolve_file)
        self.connection_records[record.id] = record
        with self._lock:
            self._connectors[record.id] = connector
            # the connector changed (new files, new credentials): derived data is stale
            self._graphs.pop(record.id, None)
            self._overviews.pop(record.id, None)
        return record

    def remove_connection(self, connection_id: str) -> bool:
        with self._lock:
            self._connectors.pop(connection_id, None)  # also frees a parsed model held in memory
            self._secrets.pop(connection_id, None)
            self._graphs.pop(connection_id, None)
            self._overviews.pop(connection_id, None)
        return self.connection_records.delete(connection_id)

    def connector(self, connection_id: str) -> BIConnector:
        with self._lock:
            if connection_id not in self._connectors:
                record = self.connection_records[connection_id]
                self._connectors[connection_id] = build_connector(record, self._secrets.get(connection_id, {}), self.resolve_file)
            return self._connectors[connection_id]

    def has_secrets(self, connection_id: str) -> bool:
        return bool(self._secrets.get(connection_id))

    def sync(self, connection_id: str, workspace_id: str) -> tuple[GraphStore, list[str]]:
        with self._conn_lock(connection_id):
            connector = self.connector(connection_id)
            graph = GraphStore()
            warnings = build_full_graph(graph, connector, workspace_id)
            with self._lock:
                self._graphs[connection_id] = graph
                self._overviews.pop(connection_id, None)

            def mark(record: ConnectionRecord) -> None:
                record.synced_workspaces = [workspace_id]
                record.last_synced_at = utcnow().isoformat()

            self.connection_records.update(connection_id, mark)
            return graph, warnings

    def graph_for(self, connection_id: str) -> GraphStore:
        """The connection's lineage graph, rebuilt from the connector after
        a restart if the connection was synced before."""
        with self._lock:
            if connection_id in self._graphs:
                return self._graphs[connection_id]
        with self._conn_lock(connection_id):
            with self._lock:  # another request may have built it while we waited
                if connection_id in self._graphs:
                    return self._graphs[connection_id]
            record = self.connection_records.get(connection_id)
            if record is not None and record.synced_workspaces:
                graph, _ = self.sync(connection_id, record.synced_workspaces[0])
                return graph
            return GraphStore()

    def overview(self, connection_id: str) -> dict:
        """Model documentation + health check, cached until the next sync."""
        from app.engine.model_insights import build_overview

        with self._lock:
            if connection_id in self._overviews:
                return self._overviews[connection_id]
        with self._conn_lock(connection_id):
            with self._lock:
                if connection_id in self._overviews:
                    return self._overviews[connection_id]
            record = self.connection_records[connection_id]
            graph = self.graph_for(connection_id)
            connector = self.connector(connection_id)
            reports = connector.get_reports(record.synced_workspaces[0] if record.synced_workspaces else (record.workspace_id or ""))
            model_id = reports[0].model_id if reports else ""
            result = build_overview(connector, graph, model_id, record.name)
            result["connection_id"] = connection_id
            with self._lock:
                self._overviews[connection_id] = result
            return result

    # --------------------------------------------------------------- files
    def resolve_file(self, file_id: str) -> tuple[str, Path]:
        record = self.files[file_id]
        return record.name, self.upload_dir / record.stored_as

    def save_json_export(self, name: str, payload: dict) -> Path:
        path = self.export_dir / name
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path


_store: Optional[Store] = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store()
        return _store


def reset_store(data_dir: Optional[str] = None) -> Store:
    """Tests / tooling: start from a fresh data directory."""
    global _store
    with _store_lock:
        _store = Store(data_dir=data_dir)
        return _store
