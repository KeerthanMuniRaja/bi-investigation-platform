"""
Orchestration between the store, connectors and engines. API routes stay
thin: they validate input and call these functions.
"""
from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Optional, Union

from app.auth import current_owner
from app.engine import investigation as inv_engine
from app.engine.data_reconciliation import reconcile_datasets
from app.engine.datasets import build_dataset, rows_from_query_result
from app.engine.mapping import suggest_mapping
from app.models.data_reconciliation import (
    DataReconciliation,
    MappingSuggestion,
    ReconciliationConfig,
    ReconciliationDefinition,
)
from app.models.dataset import Dataset, DatasetSpec
from app.models.graph import utcnow
from app.models.investigation import Investigation
from app.sources.files import SUPPORTED_EXTENSIONS, FileReadError, list_sheets, read_table
from app.store.database import FileRecord, Store

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_MODEL_BYTES = 2 * 1024 * 1024 * 1024
Content = Union[bytes, BinaryIO]


# ------------------------------------------------------------------- files
def _write_upload(store: Store, name: str, content: Content, limit: int) -> tuple[str, int]:
    """Write an upload to the uploads folder in 1 MB chunks (a 1.5 GB .pbix
    must not be held in memory), enforcing the size limit while copying."""
    file_id = store.next_id("FILE")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    stored_as = f"{file_id}_{uuid.uuid4().hex[:8]}_{safe}"
    path = store.upload_dir / stored_as
    size = 0
    try:
        with open(path, "wb") as out:
            if isinstance(content, (bytes, bytearray)):
                size = len(content)
                if size > limit:
                    raise FileReadError(f"File is larger than {limit // (1024 * 1024)} MB.")
                out.write(content)
            else:
                while chunk := content.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise FileReadError(f"File is larger than {limit // (1024 * 1024)} MB.")
                    out.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return stored_as, size


def save_upload(store: Store, filename: str, content: Content, content_type: Optional[str] = None) -> FileRecord:
    name = Path(filename or "upload").name
    ext = Path(name).suffix.lower()
    if ext == ".xls":
        raise FileReadError("Legacy .xls files are not supported — save the workbook as .xlsx and upload again.")
    if ext not in SUPPORTED_EXTENSIONS:
        raise FileReadError(f"Unsupported file type '{ext or name}'. Upload .xlsx, .xlsm, .csv or .tsv.")
    stored_as, size = _write_upload(store, name, content, MAX_UPLOAD_BYTES)
    path = store.upload_dir / stored_as
    try:
        sheets = [s.__dict__ for s in list_sheets(path)]
    except Exception as e:  # noqa: BLE001 — unreadable file: don't keep it
        path.unlink(missing_ok=True)
        raise FileReadError(f"Could not read '{name}': {e}") from e
    record = FileRecord(file_id=stored_as.split("_", 1)[0], name=name, stored_as=stored_as, size=size,
                        content_type=content_type, sheets=sheets, blob_key=_to_cloud(store, path))
    store.files[record.file_id] = record
    return record


def _to_cloud(store: Store, path: Path) -> Optional[str]:
    """Copy an upload to cloud storage (when configured) so every API server can read it."""
    try:
        return store.blobs.put(current_owner(), path.name, path)
    except Exception:
        path.unlink(missing_ok=True)
        raise


MODEL_EXTENSIONS = {".pbix", ".pbit", ".bim", ".zip"}


def save_model_upload(store: Store, filename: str, content: Content) -> FileRecord:
    """Store a Power BI file (.pbix/.pbit/.bim/zipped PBIP) for model analysis."""
    name = Path(filename or "model").name
    ext = Path(name).suffix.lower()
    if ext not in MODEL_EXTENSIONS:
        raise FileReadError(f"'{name}' isn't a Power BI file. Upload a .pbix, .pbit, .bim or a zipped PBIP project.")
    stored_as, size = _write_upload(store, name, content, MAX_MODEL_BYTES)
    # files kept in the database: a Power BI file that is deleted right after reading isn't copied there
    copy = not (store.blobs.name == "database" and not keep_model_files(store))
    record = FileRecord(file_id=stored_as.split("_", 1)[0], name=name, stored_as=stored_as, size=size, kind="model",
                        blob_key=_to_cloud(store, store.upload_dir / stored_as) if copy else None)
    store.files[record.file_id] = record
    return record


def start_direct_upload(store: Store, filename: str, kind: str, size: Optional[int] = None) -> dict:
    """Step 1 of an upload that goes straight from the browser to cloud
    storage: check the file and hand out a one-time upload URL in the
    person's own folder."""
    name = Path(filename or "upload").name
    ext = Path(name).suffix.lower()
    allowed, limit = (MODEL_EXTENSIONS, MAX_MODEL_BYTES) if kind == "model" else (SUPPORTED_EXTENSIONS, MAX_UPLOAD_BYTES)
    if ext not in allowed:
        raise FileReadError(f"'{name}' can't be uploaded here. Use " + ", ".join(sorted(allowed)) + ".")
    if size and size > limit:
        raise FileReadError(f"File is larger than {limit // (1024 * 1024)} MB.")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    key = f"{store.blobs.folder(current_owner())}/incoming/{uuid.uuid4().hex[:12]}_{safe}"
    signed = store.blobs.sign_upload(key)
    return {"key": key, "upload_url": signed["url"], "upload_headers": signed["headers"]}


def finish_direct_upload(store: Store, key: str, filename: str, kind: str) -> FileRecord:
    """Step 2: the browser has put the file in storage; fetch it, check it
    and record it (the stored object stays the copy of record)."""
    if not key.startswith(store.blobs.folder(current_owner()) + "/") or ".." in key:
        raise FileReadError("That upload belongs to someone else.")
    name = Path(filename or key.rsplit("/", 1)[-1]).name
    file_id = store.next_id("FILE")
    stored_as = f"{file_id}_{uuid.uuid4().hex[:8]}_{re.sub(r'[^A-Za-z0-9._-]', '_', name)}"
    path = store.upload_dir / stored_as
    store.blobs.fetch(key, path)
    size = path.stat().st_size
    limit = MAX_MODEL_BYTES if kind == "model" else MAX_UPLOAD_BYTES
    sheets: list[dict] = []
    try:
        if size > limit:
            raise FileReadError(f"File is larger than {limit // (1024 * 1024)} MB.")
        if kind != "model":
            try:
                sheets = [s.__dict__ for s in list_sheets(path)]
            except Exception as e:  # noqa: BLE001
                raise FileReadError(f"Could not read '{name}': {e}") from e
    except Exception:
        path.unlink(missing_ok=True)
        store.blobs.delete(key)
        raise
    record = FileRecord(file_id=file_id, name=name, stored_as=stored_as, size=size, kind="model" if kind == "model" else "table",
                        sheets=sheets, blob_key=key)
    store.files[record.file_id] = record
    return record


def open_model_file(store: Store, record: FileRecord) -> tuple[str, list[str]]:
    """Create a connection for an uploaded Power BI file and build its lineage graph."""
    from app.connectors.registry import ConnectionRecord

    from app.services.sessions import current_session_id, temporary_dashboards

    conn = ConnectionRecord(id=store.next_id("CONN"), name=Path(record.name).stem, platform="pbix",
                            file_ids=[record.file_id], workspace_id="file",
                            session_id=current_session_id() if temporary_dashboards() else None)
    try:
        store.add_connection(conn, {})
        _, warnings = store.sync(conn.id, "file")   # also saves the snapshot of what was read
    except Exception:
        store.remove_connection(conn.id)
        raise
    if not keep_model_files(store):
        store.release_model_file(conn.id)
    return conn.id, warnings


def keep_model_files(store: Store) -> bool:
    """Keep uploaded Power BI files after reading them? By default only when
    they cost nothing extra (no cloud storage); BIP_KEEP_MODEL_FILES overrides."""
    import os

    value = os.environ.get("BIP_KEEP_MODEL_FILES", "").strip().lower()
    if value:
        return value not in ("0", "false", "no", "off")
    return not store.blobs.enabled


def file_users(store: Store, file_id: str) -> list[str]:
    """Connections and templates that read this file."""
    users = [f"connection '{c.name}' ({c.id})" for c in store.connection_records.values() if file_id in c.file_ids]
    users += [f"template '{d.name}' ({d.definition_id})" for d in store.definitions.values()
              if file_id in (d.left.file_id, d.right.file_id)]
    return users


def delete_file(store: Store, file_id: str, force: bool = False) -> bool:
    record = store.files.get(file_id)
    if record is None:
        return False
    users = [] if force else file_users(store, file_id)
    if users:
        raise ValueError(f"{record.name} is still used by {', '.join(users[:3])} — delete or change those first.")
    path = store.upload_dir / record.stored_as
    path.unlink(missing_ok=True)
    if record.blob_key:
        store.blobs.delete(record.blob_key)
    # a zipped PBIP project is extracted next to the upload
    shutil.rmtree(path.parent / f"{path.stem}_pbip", ignore_errors=True)
    return store.files.delete(file_id)


# ---------------------------------------------------------------- datasets
def materialize(store: Store, spec: DatasetSpec, name: Optional[str] = None, persist: bool = True) -> Dataset:
    """Run a DatasetSpec: read the file tab / pull the visual's data / run
    the query, apply transforms, and (optionally) save the result."""
    dataset_id = store.next_id("DS")
    if spec.kind == "dataset":
        base = store.datasets[spec.dataset_id or ""]
        ds = build_dataset(dataset_id, name or spec.name or base.name, spec, base.column_names(), base.rows,
                           platform=base.platform, header_row=base.header_row, warnings=list(base.warnings))
    elif spec.kind == "file":
        file_name, path = store.resolve_file(spec.file_id or "")
        table = read_table(path, spec.sheet, spec.header_row)
        spec = spec.model_copy(update={"sheet": table.sheet, "header_row": table.header_row})
        label = name or spec.name or f"{file_name} › {table.sheet}"
        ext = Path(file_name).suffix.lower().lstrip(".")
        ds = build_dataset(dataset_id, label, spec, table.columns, table.rows,
                           platform="excel" if ext.startswith("xl") else ext, header_row=table.header_row, warnings=table.warnings)
    elif spec.kind == "bi_visual":
        connector = store.connector(spec.connection_id or "")
        result = connector.get_visual_data(spec.report_id or "", spec.page_id or "", spec.visual_id or "", spec.filters)
        columns, rows = rows_from_query_result(result)
        title = _visual_title(connector, spec)
        filters = ", ".join(
            f"{_short_field(f.field)} {'=' if (f.operator or 'in') == 'in' else '≠' if f.operator == 'not_in' else f.operator} "
            f"{'/'.join('(Blank)' if v is None else str(v) for v in f.values)}"
            for f in spec.filters if f.values)
        label = name or spec.name or (f"{title} ({filters})" if filters else title)
        ds = build_dataset(dataset_id, label, spec, columns, rows, platform=connector.platform_name, warnings=result.warnings)
    elif spec.kind == "model_table":
        connector = store.connector(spec.connection_id or "")
        if not hasattr(connector, "get_table_data") or not spec.table:
            raise ValueError("model_table datasets need a Power BI file connection and a table name")
        columns, rows = rows_from_query_result(connector.get_table_data(spec.table))
        ds = build_dataset(dataset_id, name or spec.name or f"{spec.table} (model table)", spec, columns, rows, platform="powerbi")
    elif spec.kind == "bi_query":
        connector = store.connector(spec.connection_id or "")
        if not spec.query:
            raise ValueError("query is required for a bi_query dataset")
        result = connector.execute_query(spec.model_id or "", spec.query)
        columns, rows = rows_from_query_result(result)
        ds = build_dataset(dataset_id, name or spec.name or "Custom query", spec, columns, rows, platform=connector.platform_name)
    else:
        raise ValueError(f"Datasets of kind '{spec.kind}' are created with POST /datasets/inline")
    if persist:
        store.datasets[dataset_id] = ds
    return ds


def _short_field(field: str) -> str:
    return field[field.rindex("[") + 1:-1] if field.endswith("]") and "[" in field else field


def _visual_title(connector, spec: DatasetSpec) -> str:
    try:
        for v in connector.get_visuals(spec.report_id or "", spec.page_id or ""):
            if v.id == spec.visual_id:
                return v.title
    except Exception:  # noqa: BLE001 — a title is cosmetic
        pass
    return spec.visual_id or "Visual"


def inline_dataset(store: Store, name: str, columns: list[str], rows: list[dict]) -> Dataset:
    dataset_id = store.next_id("DS")
    ds = build_dataset(dataset_id, name, DatasetSpec(kind="inline", name=name), columns, rows, platform="api")
    store.datasets[dataset_id] = ds
    return ds


# ---------------------------------------------------------- reconciliation
def suggest(store: Store, left_id: str, right_id: str) -> MappingSuggestion:
    return suggest_mapping(_dataset(store, left_id), _dataset(store, right_id))


def run(
    store: Store,
    left_id: str,
    right_id: str,
    config: Optional[ReconciliationConfig] = None,
    name: Optional[str] = None,
    definition_id: Optional[str] = None,
    create_investigation: bool = False,
    question: Optional[str] = None,
) -> DataReconciliation:
    left, right = _dataset(store, left_id), _dataset(store, right_id)
    if config is None or not config.value_columns:
        suggestion = suggest_mapping(left, right)
        if config is not None:
            # keep everything the caller chose (including key columns); only fill in the value columns
            keep = config.model_dump(exclude={"value_columns"} | (set() if config.key_columns else {"key_columns"}))
            suggestion.config = suggestion.config.model_copy(update=keep)
        config = suggestion.config
    rec_id = store.next_id("DREC")
    rec = reconcile_datasets(left, right, config, rec_id, name)
    rec.definition_id = definition_id
    if create_investigation:
        inv = investigate(store, rec, question)
        rec.investigation_id = inv.investigation_id
    store.data_reconciliations[rec_id] = rec
    return rec


def investigate(store: Store, rec: DataReconciliation, question: Optional[str] = None,
                connection_id: Optional[str] = None) -> Investigation:
    inv_id = store.next_id("INV")
    inv = inv_engine.create_investigation(inv_id, question or f"Why don't {rec.left_name} and {rec.right_name} agree?")
    inv.connection_id = connection_id or _connection_of(store, rec)
    inv_engine.attach_data_reconciliation(inv, rec)
    inv_engine.finalize(inv)
    store.investigations[inv_id] = inv
    return inv


def _connection_of(store: Store, rec: DataReconciliation) -> Optional[str]:
    for ds_id in (rec.left_dataset_id, rec.right_dataset_id):
        ds = store.datasets.get(ds_id)
        if ds and ds.source.connection_id:
            return ds.source.connection_id
    return None


def run_definition(
    store: Store,
    definition_id: str,
    left_file_id: Optional[str] = None,
    right_file_id: Optional[str] = None,
) -> DataReconciliation:
    """Re-run a saved reconciliation: re-extract both sides fresh (new
    visual data, or a newly uploaded version of the Excel file) and
    compare with the saved mapping."""
    definition = store.definitions[definition_id]
    left_spec, right_spec = definition.left, definition.right
    for side, spec, new_id in (("A", left_spec, left_file_id), ("B", right_spec, right_file_id)):
        if new_id and spec.kind != "file":
            raise ValueError(f"Side {side} of this template isn't a file, so it can't take a new file version.")
        if new_id and new_id not in store.files:
            raise ValueError(f"file {new_id} not found — upload it first")
    if left_file_id:
        left_spec = left_spec.model_copy(update={"file_id": left_file_id})
    if right_file_id:
        right_spec = right_spec.model_copy(update={"file_id": right_file_id})
    left = materialize(store, left_spec)
    right = materialize(store, right_spec)
    rec = run(store, left.dataset_id, right.dataset_id, definition.config,
              name=f"{definition.name} — {utcnow().strftime('%Y-%m-%d %H:%M')}",
              definition_id=definition_id, create_investigation=definition.create_investigation)

    def record_run(d: ReconciliationDefinition) -> None:
        d.run_ids.append(rec.reconciliation_id)
        d.updated_at = utcnow().isoformat()
        # the newer file version becomes the template's file for the next "Run now"
        if left_file_id:
            d.left = d.left.model_copy(update={"file_id": left_file_id})
        if right_file_id:
            d.right = d.right.model_copy(update={"file_id": right_file_id})

    store.definitions.update(definition_id, record_run)
    return rec


def definition_from_run(store: Store, rec: DataReconciliation, name: str, description: str = "",
                        create_investigation: bool = False) -> ReconciliationDefinition:
    left, right = _dataset(store, rec.left_dataset_id), _dataset(store, rec.right_dataset_id)
    definition = ReconciliationDefinition(
        definition_id=store.next_id("TPL"),
        name=name,
        description=description,
        left=left.source if left.source.kind != "inline" else DatasetSpec(kind="dataset", dataset_id=left.dataset_id),
        right=right.source if right.source.kind != "inline" else DatasetSpec(kind="dataset", dataset_id=right.dataset_id),
        config=rec.config,
        create_investigation=create_investigation,
        run_ids=[rec.reconciliation_id],
    )
    store.definitions[definition.definition_id] = definition
    return definition


def _dataset(store: Store, dataset_id: str) -> Dataset:
    return store.datasets[dataset_id]
