"""
Copy the local workspace (SQLite + uploads folder) into the cloud setup,
owned by one account.

    python scripts/migrate_to_cloud.py --owner keerthan            # dry run: shows what would move
    python scripts/migrate_to_cloud.py --owner keerthan --apply

Reads DATABASE_URL and the storage settings (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY, R2_BUCKET) from the environment or the project's .env. Records keep their ids; record counters
in the cloud database are moved past the local ones so new ids never clash.
Uploaded files are copied to the storage bucket. Running it twice is safe:
records already in the cloud are skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True, help="username of the account that should own the records")
    ap.add_argument("--data-dir", default=os.environ.get("BIP_DATA_DIR", str(ROOT / "data")), help="local data folder (default ./data)")
    ap.add_argument("--from-owner", default="local", help="owner of the local records to copy (default: local)")
    ap.add_argument("--apply", action="store_true", help="actually copy (otherwise only report)")
    args = ap.parse_args()
    owner = args.owner.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", owner):
        ap.error("--owner must be a username (3–32 letters, numbers, dots, dashes or underscores)")
    if not os.environ.get("DATABASE_URL"):
        ap.error("DATABASE_URL isn't set (put it in .env) — that's where the records go")

    from app.store.blobs import blobs_from_env
    from app.store.database import PostgresDocumentDB

    data_dir = Path(args.data_dir).resolve()
    src = sqlite3.connect(data_dir / "bip.sqlite3")
    cols = [r[1] for r in src.execute("PRAGMA table_info(documents)")]
    where, params = ("WHERE owner = ?", (args.from_owner,)) if "owner" in cols else ("", ())
    docs = src.execute(f"SELECT collection, id, data FROM documents {where} ORDER BY created_at, rowid", params).fetchall()
    counters = dict(src.execute("SELECT prefix, value FROM counters").fetchall())
    files = [(i, json.loads(d)) for c, i, d in docs if c == "files"]
    print(f"Local workspace in {data_dir}: {len(docs)} records ({len(files)} files) to copy to {owner}")
    for c in sorted({c for c, _, _ in docs}):
        print(f"  {c:22s} {sum(1 for x, _, _ in docs if x == c)}")

    blobs = blobs_from_env()
    if files and not blobs.enabled:
        print("! No storage bucket set (R2_* variables): the files would stay on this machine only.")
    if not args.apply:
        print("Dry run — nothing copied. Add --apply to copy.")
        return 0

    db = PostgresDocumentDB(os.environ["DATABASE_URL"])
    copied = skipped = 0
    for collection, doc_id, data in docs:
        if db.get(collection, owner, doc_id) is not None:
            skipped += 1
            continue
        if collection == "files" and blobs.enabled:
            record = json.loads(data)
            path = data_dir / "uploads" / record["stored_as"]
            if path.exists():
                print(f"  uploading {record['name']} ({path.stat().st_size // 1024:,} KB)…")
                record["blob_key"] = blobs.put(owner, path.name, path)
                data = json.dumps(record)
            else:
                print(f"  ! {record['name']} is missing from {path.parent} — record copied without the file")
        db.put(collection, owner, doc_id, data)
        copied += 1
    # keep new ids clear of the copied ones
    with db.pool.connection() as conn:
        for prefix, value in counters.items():
            conn.execute("insert into bip.counters (prefix, value) values (%s, %s) on conflict (prefix) "
                         "do update set value = greatest(bip.counters.value, excluded.value)", (prefix, value))
    db.close()
    print(f"Done: {copied} copied, {skipped} already there. Sign in as {owner} to see them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
