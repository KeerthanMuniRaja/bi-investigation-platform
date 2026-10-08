"""
Persistence: a small document store, in SQLite (local) or Postgres (cloud).

Every object is stored as a JSON document keyed by (collection, id) — the
pydantic models *are* the schema, so no route or engine code touches SQL.
Each document also has an owner: the signed-in person's username (see
app/auth.py), or "local" when sign-in is off. Accounts themselves are in a
separate `users` table (username, display name, password hash). Every read and write is
scoped to the current owner, so people only ever see their own work.

  DATABASE_URL set    -> Postgres (e.g. Supabase), tables in the "bip" schema
  otherwise           -> SQLite file <BIP_DATA_DIR>/bip.sqlite3
  SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY set -> uploaded files are also
                         kept in Supabase Storage (see app/store/blobs.py)

What is *not* persisted, on purpose:
  - connection secrets (client secret, PAT secret) — kept in process
    memory only, or read from environment variables;
  - the lineage graph — rebuilt from the connector on first use after a
    restart (it's derived data).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Generic, Iterator, Optional, Protocol, Type, TypeVar

from pydantic import BaseModel, Field

from app.auth import current_owner
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
from app.store.blobs import Blobs, blobs_from_env

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
    blob_key: Optional[str] = None  # where the file lives in cloud storage, if used
    kept: bool = True               # False: the original was removed after reading (its snapshot has what's needed)


class ModelSnapshot(BaseModel):
    """What was read from a Power BI file (model, report, first rows of each
    table), so the app never has to read — or keep — the file again."""
    connection_id: str
    data: dict
    saved_at: str = Field(default_factory=lambda: utcnow().isoformat())


class DocumentDB(Protocol):
    """What the store needs from a database. Ids are unique across owners
    (they come from shared counters); the owner is a filter on every call."""

    def put(self, collection: str, owner: str, doc_id: str, data: str) -> None: ...
    def get(self, collection: str, owner: str, doc_id: str) -> Optional[str]: ...
    def delete(self, collection: str, owner: str, doc_id: str) -> bool: ...
    def all(self, collection: str, owner: str) -> list[tuple[str, str]]: ...
    def count(self, collection: str, owner: str) -> int: ...
    def update(self, collection: str, owner: str, doc_id: str, change: Callable[[str], str]) -> Optional[str]: ...
    def next_counter(self, prefix: str) -> int: ...
    # accounts (username + password sign-in); not owner-scoped
    def get_user(self, username: str) -> Optional[dict]: ...
    def create_user(self, user: dict) -> None: ...
    def update_user(self, username: str, **fields) -> None: ...
    def count_users(self) -> int: ...
    def list_users(self) -> list[dict]: ...
    def reassign_owner(self, old: str, new: str) -> int: ...


USER_FIELDS = ("id", "username", "display_name", "password_hash", "created_at", "last_login_at", "token_version", "disabled")


class UsernameTaken(ValueError):
    pass


class SqliteDocumentDB:
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
                "created_at TEXT NOT NULL, data TEXT NOT NULL, owner TEXT NOT NULL DEFAULT 'local', "
                "PRIMARY KEY (collection, id))"
            )
            # databases from before sign-in existed: everything in them belongs to the local workspace
            if "owner" not in [r[1] for r in self._conn.execute("PRAGMA table_info(documents)")]:
                self._conn.execute("ALTER TABLE documents ADD COLUMN owner TEXT NOT NULL DEFAULT 'local'")
            self._conn.execute("CREATE INDEX IF NOT EXISTS documents_owner ON documents (collection, owner)")
            self._conn.execute("CREATE TABLE IF NOT EXISTS counters (prefix TEXT PRIMARY KEY, value INTEGER NOT NULL)")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT, "
                "password_hash TEXT NOT NULL, created_at TEXT NOT NULL, last_login_at TEXT, "
                "token_version INTEGER NOT NULL DEFAULT 0, disabled INTEGER NOT NULL DEFAULT 0)"
            )
            self._conn.commit()

    # ------------------------------------------------------------ accounts
    def get_user(self, username: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(f"SELECT {', '.join(USER_FIELDS)} FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            return None
        user = dict(zip(USER_FIELDS, row))
        user["disabled"] = bool(user["disabled"])
        return user

    def create_user(self, user: dict) -> None:
        with self._lock:
            try:
                self._conn.execute(f"INSERT INTO users ({', '.join(USER_FIELDS)}) VALUES ({', '.join('?' * len(USER_FIELDS))})",
                                   tuple(int(user[f]) if f == "disabled" else user.get(f) for f in USER_FIELDS))
                self._conn.commit()
            except sqlite3.IntegrityError as e:
                raise UsernameTaken(user["username"]) from e

    def update_user(self, username: str, **fields) -> None:
        cols = [f for f in fields if f in USER_FIELDS and f not in ("id", "username")]
        if not cols:
            return
        with self._lock:
            self._conn.execute(f"UPDATE users SET {', '.join(f'{c} = ?' for c in cols)} WHERE username = ?",
                               (*(int(fields[c]) if c == "disabled" else fields[c] for c in cols), username))
            self._conn.commit()

    def count_users(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def list_users(self) -> list[dict]:
        with self._lock:
            names = [r[0] for r in self._conn.execute("SELECT username FROM users ORDER BY username")]
        return [self.get_user(n) for n in names]

    def reassign_owner(self, old: str, new: str) -> int:
        with self._lock:
            cur = self._conn.execute("UPDATE documents SET owner = ? WHERE owner = ?", (new, old))
            self._conn.commit()
        return cur.rowcount

    def put(self, collection: str, owner: str, doc_id: str, data: str) -> None:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO documents (collection, id, created_at, data, owner) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(collection, id) DO UPDATE SET data = excluded.data WHERE documents.owner = excluded.owner",
                (collection, doc_id, utcnow().isoformat(), data, owner),
            )
            self._conn.commit()
        if cur.rowcount == 0:  # the id exists but belongs to someone else
            raise NotFoundError(f"{collection} {doc_id} not found")

    def get(self, collection: str, owner: str, doc_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT data FROM documents WHERE collection = ? AND owner = ? AND id = ?",
                                     (collection, owner, doc_id)).fetchone()
        return row[0] if row else None

    def delete(self, collection: str, owner: str, doc_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM documents WHERE collection = ? AND owner = ? AND id = ?", (collection, owner, doc_id))
            self._conn.commit()
        return cur.rowcount > 0

    def all(self, collection: str, owner: str) -> list[tuple[str, str]]:
        with self._lock:
            return self._conn.execute(
                "SELECT id, data FROM documents WHERE collection = ? AND owner = ? ORDER BY created_at DESC, rowid DESC",
                (collection, owner),
            ).fetchall()

    def count(self, collection: str, owner: str) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM documents WHERE collection = ? AND owner = ?", (collection, owner)).fetchone()[0]

    def update(self, collection: str, owner: str, doc_id: str, change: Callable[[str], str]) -> Optional[str]:
        with self._lock:
            raw = self.get(collection, owner, doc_id)
            if raw is None:
                return None
            new = change(raw)
            self._conn.execute("UPDATE documents SET data = ? WHERE collection = ? AND owner = ? AND id = ?", (new, collection, owner, doc_id))
            self._conn.commit()
            return new

    def next_counter(self, prefix: str) -> int:
        with self._lock:
            self._conn.execute(
                "INSERT INTO counters (prefix, value) VALUES (?, 1) ON CONFLICT(prefix) DO UPDATE SET value = value + 1",
                (prefix,),
            )
            self._conn.commit()
            return self._conn.execute("SELECT value FROM counters WHERE prefix = ?", (prefix,)).fetchone()[0]


# the schema lives in a .sql file so it can also be run in Supabase's SQL editor
# (a copy is in supabase/migrations/ for the Supabase CLI)
SCHEMA_FILE = Path(__file__).with_name("schema.sql")


class PostgresDocumentDB:
    """The same store in Postgres (Supabase or any Postgres 13+). Tables are
    created on first start in a private "bip" schema."""

    def __init__(self, dsn: str, pool_size: Optional[int] = None):
        from psycopg_pool import ConnectionPool

        # prepare_threshold=None: works through Supabase's transaction pooler (PgBouncer) too
        self.pool = ConnectionPool(dsn, min_size=1, max_size=pool_size or int(os.environ.get("BIP_DB_POOL_SIZE", "10")),
                                   kwargs={"autocommit": True, "prepare_threshold": None}, open=True)
        if os.environ.get("BIP_DB_AUTO_SCHEMA", "true").strip().lower() not in ("0", "false", "no", "off"):
            with self.pool.connection() as conn:
                conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.pool.close()

    # ------------------------------------------------------------ accounts
    def get_user(self, username: str) -> Optional[dict]:
        with self.pool.connection() as conn:
            row = conn.execute(f"select {', '.join(USER_FIELDS)} from bip.users where username = %s", (username,)).fetchone()
        if not row:
            return None
        user = dict(zip(USER_FIELDS, row))
        for f in ("created_at", "last_login_at"):
            if user[f] is not None and not isinstance(user[f], str):
                user[f] = user[f].isoformat()
        return user

    def create_user(self, user: dict) -> None:
        from psycopg.errors import UniqueViolation

        try:
            with self.pool.connection() as conn:
                conn.execute(f"insert into bip.users ({', '.join(USER_FIELDS)}) values ({', '.join(['%s'] * len(USER_FIELDS))})",
                             tuple(user.get(f) for f in USER_FIELDS))
        except UniqueViolation as e:
            raise UsernameTaken(user["username"]) from e

    def update_user(self, username: str, **fields) -> None:
        cols = [f for f in fields if f in USER_FIELDS and f not in ("id", "username")]
        if not cols:
            return
        with self.pool.connection() as conn:
            conn.execute(f"update bip.users set {', '.join(f'{c} = %s' for c in cols)} where username = %s",
                         (*(fields[c] for c in cols), username))

    def count_users(self) -> int:
        with self.pool.connection() as conn:
            return conn.execute("select count(*) from bip.users").fetchone()[0]

    def list_users(self) -> list[dict]:
        with self.pool.connection() as conn:
            names = [r[0] for r in conn.execute("select username from bip.users order by username")]
        return [self.get_user(n) for n in names]

    def reassign_owner(self, old: str, new: str) -> int:
        with self.pool.connection() as conn:
            return conn.execute("update bip.documents set owner = %s where owner = %s", (new, old)).rowcount

    def put(self, collection: str, owner: str, doc_id: str, data: str) -> None:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "insert into bip.documents (collection, id, owner, data) values (%s, %s, %s, %s) "
                "on conflict (collection, id) do update set data = excluded.data where bip.documents.owner = excluded.owner",
                (collection, doc_id, owner, data),
            )
        if cur.rowcount == 0:
            raise NotFoundError(f"{collection} {doc_id} not found")

    def get(self, collection: str, owner: str, doc_id: str) -> Optional[str]:
        with self.pool.connection() as conn:
            row = conn.execute("select data from bip.documents where collection = %s and owner = %s and id = %s",
                               (collection, owner, doc_id)).fetchone()
        return row[0] if row else None

    def delete(self, collection: str, owner: str, doc_id: str) -> bool:
        with self.pool.connection() as conn:
            cur = conn.execute("delete from bip.documents where collection = %s and owner = %s and id = %s", (collection, owner, doc_id))
        return cur.rowcount > 0

    def all(self, collection: str, owner: str) -> list[tuple[str, str]]:
        with self.pool.connection() as conn:
            return conn.execute("select id, data from bip.documents where collection = %s and owner = %s "
                                "order by created_at desc, seq desc", (collection, owner)).fetchall()

    def count(self, collection: str, owner: str) -> int:
        with self.pool.connection() as conn:
            return conn.execute("select count(*) from bip.documents where collection = %s and owner = %s", (collection, owner)).fetchone()[0]

    def update(self, collection: str, owner: str, doc_id: str, change: Callable[[str], str]) -> Optional[str]:
        # row lock: concurrent appends (template runs, notes) on any number of API servers don't overwrite each other
        with self.pool.connection() as conn, conn.transaction():
            row = conn.execute("select data from bip.documents where collection = %s and owner = %s and id = %s for update",
                               (collection, owner, doc_id)).fetchone()
            if row is None:
                return None
            new = change(row[0])
            conn.execute("update bip.documents set data = %s where collection = %s and owner = %s and id = %s", (new, collection, owner, doc_id))
            return new

    def next_counter(self, prefix: str) -> int:
        with self.pool.connection() as conn:
            return conn.execute("insert into bip.counters (prefix, value) values (%s, 1) on conflict (prefix) "
                                "do update set value = bip.counters.value + 1 returning value", (prefix,)).fetchone()[0]


def on_vercel() -> bool:
    return bool(os.environ.get("VERCEL"))


def default_data_dir() -> str:
    # Vercel functions can only write to /tmp (a scratch space that doesn't last)
    return os.environ.get("BIP_DATA_DIR") or ("/tmp/bip" if on_vercel() else "./data")


def normalize_db_url(url: str) -> str:
    """Accept a database address whose password has special characters
    (@ # / ? : %) typed as-is: the last "@" separates the password from the
    host, and the password is %-encoded so the driver reads it correctly."""
    from urllib.parse import quote, unquote

    url = url.strip()
    scheme, sep, rest = url.partition("://")
    if not sep or "@" not in rest:
        return url
    userinfo, _, hostpart = rest.rpartition("@")
    user, colon, password = userinfo.partition(":")
    if not colon:
        return url
    return f"{scheme}://{user}:{quote(unquote(password), safe='')}@{hostpart}"


def open_database(data_dir: Path, db_path: Optional[str] = None) -> DocumentDB:
    url = normalize_db_url(os.environ.get("DATABASE_URL", ""))
    if url and db_path is None:
        return PostgresDocumentDB(url)
    if on_vercel() and db_path is None:
        raise RuntimeError("DATABASE_URL isn't set. On Vercel the data must live in Postgres (Supabase): "
                           "add DATABASE_URL in the project's Environment Variables.")
    return SqliteDocumentDB(db_path or str(data_dir / "bip.sqlite3"))


class Collection(Generic[M]):
    """dict-like access to the current owner's documents in one collection.
    Objects are copies: after mutating one, assign it back to save it."""

    def __init__(self, db: DocumentDB, name: str, model: Type[M], label: Optional[str] = None):
        self.db, self.name, self.model = db, name, model
        self.label = label or name.rstrip("s").replace("_", " ")

    def get(self, doc_id: str, default: Optional[M] = None) -> Optional[M]:
        raw = self.db.get(self.name, current_owner(), doc_id)
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
        changed: list[M] = []

        def apply(raw: str) -> str:
            obj = self.model.model_validate_json(raw)
            change(obj)
            changed.append(obj)
            return obj.model_dump_json()

        if self.db.update(self.name, current_owner(), doc_id, apply) is None:
            raise NotFoundError(f"{self.label} {doc_id} not found")
        return changed[0]

    def __setitem__(self, doc_id: str, obj: M) -> None:
        self.db.put(self.name, current_owner(), doc_id, obj.model_dump_json())

    def __delitem__(self, doc_id: str) -> None:
        if not self.db.delete(self.name, current_owner(), doc_id):
            raise NotFoundError(f"{self.label} {doc_id} not found")

    def delete(self, doc_id: str) -> bool:
        return self.db.delete(self.name, current_owner(), doc_id)

    def __contains__(self, doc_id: str) -> bool:
        return self.db.get(self.name, current_owner(), doc_id) is not None

    def values(self) -> list[M]:
        return [self.model.model_validate_json(data) for _, data in self.db.all(self.name, current_owner())]

    def items(self) -> Iterator[tuple[str, M]]:
        for doc_id, data in self.db.all(self.name, current_owner()):
            yield doc_id, self.model.model_validate_json(data)

    def __len__(self) -> int:
        return self.db.count(self.name, current_owner())


class Store:
    def __init__(self, data_dir: Optional[str] = None, db_path: Optional[str] = None):
        self.data_dir = Path(data_dir or default_data_dir()).resolve()
        self.upload_dir = self.data_dir / "uploads"
        self.export_dir = Path(os.environ.get("EXPORT_DIR", str(self.data_dir / "exports"))).resolve()
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.db: DocumentDB = open_database(self.data_dir, db_path)
        self.blobs: Blobs = blobs_from_env()

        self.connection_records = Collection(self.db, "connections", ConnectionRecord, "connection")
        self.files = Collection(self.db, "files", FileRecord, "file")
        self.datasets = Collection(self.db, "datasets", Dataset, "dataset")
        self.snapshots = Collection(self.db, "snapshots", VisualSnapshot, "snapshot")
        self.reconciliations = Collection(self.db, "reconciliations", Reconciliation, "reconciliation")
        self.data_reconciliations = Collection(self.db, "data_reconciliations", DataReconciliation, "reconciliation")
        self.definitions = Collection(self.db, "definitions", ReconciliationDefinition, "template")
        self.investigations = Collection(self.db, "investigations", Investigation, "investigation")
        self.model_snapshots = Collection(self.db, "model_snapshots", ModelSnapshot, "model snapshot")

        self._connectors: dict[str, BIConnector] = {}
        self._secrets: dict[str, dict[str, str]] = {}
        self._graphs: dict[str, GraphStore] = {}
        self._overviews: dict[str, dict] = {}
        self._lock = threading.RLock()
        # one lock per connection: parallel requests after a restart must not
        # each re-read a (possibly 1 GB+) model file and rebuild its graph
        self._conn_locks: dict[str, threading.RLock] = {}

    def _own(self, connection_id: str) -> None:
        """Caches below are shared by everyone: check the connection is the
        current owner's before handing out anything derived from it."""
        if connection_id not in self.connection_records:
            raise NotFoundError(f"connection {connection_id} not found")

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
        self.model_snapshots.delete(connection_id)
        with self._lock:
            self._connectors.pop(connection_id, None)  # also frees a parsed model held in memory
            self._secrets.pop(connection_id, None)
            self._graphs.pop(connection_id, None)
            self._overviews.pop(connection_id, None)
        return self.connection_records.delete(connection_id)

    def connector(self, connection_id: str) -> BIConnector:
        self._own(connection_id)
        with self._lock:
            if connection_id not in self._connectors:
                record = self.connection_records[connection_id]
                snap = self.model_snapshots.get(connection_id) if record.platform == "pbix" else None
                self._connectors[connection_id] = build_connector(record, self._secrets.get(connection_id, {}), self.resolve_file,
                                                                  snapshot=snap.data if snap else None)
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

            record = self.connection_records.update(connection_id, mark)
            # a Power BI file read for the first time: save what was read (older dashboards too)
            if record.platform == "pbix" and connection_id not in self.model_snapshots and hasattr(connector, "snapshot"):
                self.model_snapshots[connection_id] = ModelSnapshot(connection_id=connection_id, data=connector.snapshot())
            return graph, warnings

    def release_model_file(self, connection_id: str) -> bool:
        """After a Power BI file has been read and its snapshot saved, delete
        the original (cloud copy and local copy) to save storage. Returns
        whether it was removed."""
        record = self.connection_records[connection_id]
        if record.platform != "pbix" or connection_id not in self.model_snapshots:
            return False
        for file_id in record.file_ids:
            f = self.files.get(file_id)
            if f is None or not f.kept:
                continue
            if f.blob_key:
                self.blobs.delete(f.blob_key)
            local = self.upload_dir / f.stored_as
            local.unlink(missing_ok=True)
            shutil.rmtree(local.parent / f"{local.stem}_pbip", ignore_errors=True)  # an unpacked PBIP zip
            f.kept, f.blob_key = False, None
            self.files[file_id] = f
        return True

    def graph_for(self, connection_id: str) -> GraphStore:
        """The connection's lineage graph, rebuilt from the connector after
        a restart if the connection was synced before."""
        self._own(connection_id)
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

        self._own(connection_id)
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
        """The file on this server's disk; with cloud storage, a server that
        doesn't have it yet (new instance, redeploy) downloads it first."""
        record = self.files[file_id]
        path = self.upload_dir / record.stored_as
        if not record.kept:
            raise FileNotFoundError(f"{record.name} isn't kept any more (only what was read from it is).")
        if not path.exists() and record.blob_key:
            with self._conn_lock(f"file:{file_id}"):
                if not path.exists():
                    self.blobs.fetch(record.blob_key, path)
        return record.name, path

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
