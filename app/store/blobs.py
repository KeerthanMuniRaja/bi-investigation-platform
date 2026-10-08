"""
Where uploaded files live.

The API always reads files from its own disk (pbixray needs a real file
path). With cloud storage configured, uploads are kept in a private bucket,
so any API server (a new instance, or the same one after a redeploy wiped
its disk) can fetch a file back on first use, and the browser can upload big
files straight to the bucket through one-time links. Without it, files stay
in the uploads folder only.

  Postgres (no extra service): with DATABASE_URL set and no bucket below,
    files are kept in the database itself (table bip.file_parts). Right for
    hosts whose disk is wiped on restart (Render, Railway) and modest files.
  Cloudflare R2 (or any S3-compatible store):
    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
    R2_ENDPOINT        optional, e.g. https://<account>.eu.r2.cloudflarestorage.com
  Supabase Storage:
    SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY, BIP_STORAGE_BUCKET (default "bip-files")

  BIP_FILE_STORAGE   force one: local | database | r2 | supabase (default: the first configured)

Storage keys belong on the API server only — never in the web app or browser.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Protocol
from urllib.parse import quote

CHUNK = 1024 * 1024


class Blobs(Protocol):
    enabled: bool                  # files survive this server's disk being wiped
    direct_upload: bool            # the browser can upload straight to storage (sign_upload)
    name: str

    def folder(self, owner: str) -> str: ...
    def sign_upload(self, key: str) -> dict: ...   # {"url": ..., "headers": {...}} for a browser PUT
    def check(self) -> str: ...
    def put(self, owner: str, name: str, path: Path) -> Optional[str]: ...
    def fetch(self, key: str, path: Path) -> None: ...
    def delete(self, key: str) -> None: ...


class LocalBlobs:
    """No cloud storage: the uploads folder is the only copy."""
    enabled = False
    direct_upload = False
    name = "local disk"

    def folder(self, owner: str) -> str:
        return SupabaseBlobs.folder(owner)

    def sign_upload(self, key: str) -> dict:
        raise RuntimeError("Direct uploads need cloud storage (Cloudflare R2 or Supabase Storage).")

    def check(self) -> str:
        return "ok"

    def put(self, owner: str, name: str, path: Path) -> Optional[str]:
        return None

    def fetch(self, key: str, path: Path) -> None:
        raise FileNotFoundError(f"{path.name} isn't on this server and no cloud storage is configured.")

    def delete(self, key: str) -> None:
        return None


class SupabaseBlobs:
    """Private Supabase Storage bucket, through its REST API."""
    enabled = True
    direct_upload = True
    name = "supabase"

    def __init__(self, url: str, service_key: str, bucket: str = "bip-files", client=None, timeout: float = 600.0):
        import httpx

        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0))
        # new-style secret keys (sb_secret_...) go in the apikey header only; legacy
        # service_role keys are JWTs and are also sent as the bearer token
        self.headers = {"apikey": service_key}
        if not service_key.startswith("sb_"):
            self.headers["Authorization"] = f"Bearer {service_key}"
        self._ready = False

    def _ensure_bucket(self) -> None:
        if self._ready:
            return
        r = self.client.get(f"{self.base}/bucket/{self.bucket}", headers=self.headers)
        if r.status_code != 200:
            created = self.client.post(f"{self.base}/bucket", headers=self.headers,
                                       json={"id": self.bucket, "name": self.bucket, "public": False})
            if created.status_code not in (200, 201) and "already exists" not in created.text.lower():
                raise RuntimeError(f"Couldn't create the storage bucket '{self.bucket}': {created.status_code} {created.text[:200]}")
        self._ready = True

    @staticmethod
    def folder(owner: str) -> str:
        """Each person's files sit under their own folder in the bucket."""
        return re.sub(r"[^a-z0-9._-]", "_", owner.lower()) or "local"

    @classmethod
    def key_for(cls, owner: str, name: str) -> str:
        return f"{cls.folder(owner)}/{name}"

    def sign_upload(self, key: str) -> dict:
        """A one-time URL the browser can PUT a file to directly — big uploads
        then never pass through the API (Vercel caps requests at ~4.5 MB)."""
        self._ensure_bucket()
        r = self.client.post(f"{self.base}/object/upload/sign/{self.bucket}/{quote(key, safe='/')}", headers=self.headers, json={})
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Couldn't prepare the upload: {r.status_code} {r.text[:200]}")
        url = r.json().get("url") or ""
        url = url if url.startswith("http") else f"{self.base}{url if url.startswith('/') else '/' + url}"
        return {"url": url, "headers": {"Content-Type": "application/octet-stream", "x-upsert": "true"}}

    def check(self) -> str:
        try:
            self._ensure_bucket()
            return "ok"
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {e}"

    def _object_url(self, key: str) -> str:
        return f"{self.base}/object/{self.bucket}/{quote(key, safe='/')}"

    def put(self, owner: str, name: str, path: Path) -> Optional[str]:
        self._ensure_bucket()
        key = self.key_for(owner, name)

        def chunks():
            with open(path, "rb") as f:
                while chunk := f.read(CHUNK):
                    yield chunk

        r = self.client.post(self._object_url(key), content=chunks(),
                             headers={**self.headers, "x-upsert": "true", "Content-Type": "application/octet-stream",
                                      "Content-Length": str(path.stat().st_size)})
        if r.status_code not in (200, 201):
            detail = r.text[:300]
            if r.status_code == 413 or "maximum allowed size" in detail.lower():
                detail = ("the file is bigger than the storage bucket allows — raise the upload size limit in "
                          "Supabase (Storage → Settings) or upload a smaller file")
            raise RuntimeError(f"Couldn't save {path.name} to cloud storage: {detail}")
        return key

    def fetch(self, key: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        with self.client.stream("GET", self._object_url(key), headers=self.headers) as r:
            if r.status_code != 200:
                raise FileNotFoundError(f"{path.name} couldn't be downloaded from cloud storage ({r.status_code}).")
            with open(part, "wb") as out:
                for chunk in r.iter_bytes(CHUNK):
                    out.write(chunk)
        part.replace(path)

    def delete(self, key: str) -> None:
        # best effort: a leftover object costs storage, a failed delete shouldn't block the user
        try:
            self.client.request("DELETE", f"{self.base}/object/{self.bucket}", headers=self.headers, json={"prefixes": [key]})
        except Exception:  # noqa: BLE001
            pass


class S3Blobs:
    """Cloudflare R2 (or any S3-compatible store), through boto3."""
    enabled = True
    direct_upload = True
    name = "r2"

    def __init__(self, bucket: str, endpoint_url: Optional[str], access_key: str, secret_key: str,
                 region: str = "auto", cors_origins: tuple[str, ...] = (), client=None):
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("s3", endpoint_url=endpoint_url, aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                                  region_name=region, config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                                                                    retries={"max_attempts": 3, "mode": "standard"}))
        self.client = client
        self.bucket = bucket
        self.cors_origins = cors_origins
        self._ready = False

    folder = staticmethod(SupabaseBlobs.folder)

    @classmethod
    def key_for(cls, owner: str, name: str) -> str:
        return f"{cls.folder(owner)}/{name}"

    def _ensure_bucket(self) -> None:
        """The bucket must exist; also allow the web app to upload to it from the
        browser (CORS). Setting CORS needs an admin key: if the key can't, it's
        done once in the Cloudflare dashboard instead (see docs/DEPLOY.md)."""
        if self._ready:
            return
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchBucket", "NotFound"):
                try:
                    self.client.create_bucket(Bucket=self.bucket)
                except ClientError as e2:
                    raise RuntimeError(f"The storage bucket '{self.bucket}' doesn't exist and couldn't be created: {e2}") from e2
            else:
                raise RuntimeError(f"Can't open the storage bucket '{self.bucket}': {e}") from e
        if self.cors_origins:
            try:
                self.client.put_bucket_cors(Bucket=self.bucket, CORSConfiguration={"CORSRules": [{
                    "AllowedOrigins": list(self.cors_origins), "AllowedMethods": ["PUT", "GET", "HEAD"],
                    "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600}]})
            except ClientError:
                pass  # key without admin rights: CORS is set in the dashboard
        self._ready = True

    def sign_upload(self, key: str) -> dict:
        self._ensure_bucket()
        content_type = "application/octet-stream"
        url = self.client.generate_presigned_url("put_object", Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
                                                 ExpiresIn=3600)
        return {"url": url, "headers": {"Content-Type": content_type}}

    def put(self, owner: str, name: str, path: Path) -> Optional[str]:
        self._ensure_bucket()
        key = self.key_for(owner, name)
        try:
            self.client.upload_file(str(path), self.bucket, key)  # multipart for big files
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Couldn't save {path.name} to cloud storage: {e}") from e
        return key

    def fetch(self, key: str, path: Path) -> None:
        from botocore.exceptions import ClientError

        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        try:
            self.client.download_file(self.bucket, key, str(part))
        except ClientError as e:
            part.unlink(missing_ok=True)
            raise FileNotFoundError(f"{path.name} couldn't be downloaded from cloud storage ({e}).") from e
        part.replace(path)

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:  # noqa: BLE001 - best effort
            pass

    def check(self) -> str:
        try:
            self._ensure_bucket()
            return "ok"
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {e}"


class DatabaseBlobs:
    """Files kept in Postgres, in parts of a few MB (bip.file_parts): no
    storage service to set up. Uploads go through the API (no size cap on
    hosts like Render); mind the database plan's storage (Neon free: 0.5 GB)."""
    enabled = True
    direct_upload = False
    name = "database"
    PART = 4 * 1024 * 1024
    TABLE = """
        create schema if not exists bip;
        create table if not exists bip.file_parts (
            key        text not null,
            part       integer not null,
            owner      text not null,
            data       bytea not null,
            created_at timestamptz not null default now(),
            primary key (key, part)
        );
        alter table bip.file_parts enable row level security;
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._ready = False

    folder = staticmethod(SupabaseBlobs.folder)

    def _connect(self):
        import psycopg

        # prepare_threshold=None: works through connection poolers (Neon's -pooler host, PgBouncer)
        conn = psycopg.connect(self.dsn, prepare_threshold=None, connect_timeout=20)
        if not self._ready:
            with conn.transaction():
                conn.execute(self.TABLE)
            self._ready = True
        return conn

    def sign_upload(self, key: str) -> dict:
        raise RuntimeError("Files kept in the database are uploaded through the API, not with upload links.")

    def check(self) -> str:
        try:
            with self._connect() as conn:
                n, size = conn.execute("select count(distinct key), coalesce(sum(length(data)), 0) from bip.file_parts").fetchone()
            return "ok" if not n else f"ok ({n} files, {size / 1024 / 1024:.1f} MB)"
        except Exception as e:  # noqa: BLE001
            return f"{type(e).__name__}: {e}"

    def put(self, owner: str, name: str, path: Path) -> Optional[str]:
        key = f"{self.folder(owner)}/{name}"
        try:
            with self._connect() as conn, conn.transaction(), open(path, "rb") as f:
                conn.execute("delete from bip.file_parts where key = %s", (key,))
                part = 0
                while chunk := f.read(self.PART):
                    conn.execute("insert into bip.file_parts (key, part, owner, data) values (%s, %s, %s, %s)", (key, part, owner, chunk))
                    part += 1
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Couldn't save {path.name} in the database: {e}") from e
        return key

    def fetch(self, key: str, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        part_path = path.with_name(path.name + ".part")
        with self._connect() as conn:
            parts = [r[0] for r in conn.execute("select part from bip.file_parts where key = %s order by part", (key,))]
            if not parts:
                raise FileNotFoundError(f"{path.name} isn't in the database any more.")
            with open(part_path, "wb") as out:
                for n in parts:  # one part at a time: memory stays at a few MB
                    out.write(conn.execute("select data from bip.file_parts where key = %s and part = %s", (key, n)).fetchone()[0])
        part_path.replace(path)

    def delete(self, key: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute("delete from bip.file_parts where key = %s", (key,))
                conn.commit()
        except Exception:  # noqa: BLE001 - best effort
            pass


def _web_origins() -> tuple[str, ...]:
    extra = os.environ.get("BIP_CORS_ORIGINS", "").split(",") + [os.environ.get("BIP_WEB_URL", "")]
    return tuple(dict.fromkeys(["http://localhost:3000", "http://127.0.0.1:3000", *[o.strip().rstrip("/") for o in extra if o.strip()]]))


def blobs_from_env() -> Blobs:
    choice = os.environ.get("BIP_FILE_STORAGE", "").strip().lower()
    bucket = os.environ.get("R2_BUCKET", "").strip()
    access, secret = os.environ.get("R2_ACCESS_KEY_ID", "").strip(), os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    account, endpoint = os.environ.get("R2_ACCOUNT_ID", "").strip(), os.environ.get("R2_ENDPOINT", "").strip()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if choice in ("", "r2", "s3") and bucket and access and secret and (account or endpoint):
        return S3Blobs(bucket, endpoint or f"https://{account}.r2.cloudflarestorage.com", access, secret, cors_origins=_web_origins())
    if choice in ("", "supabase") and url and key:
        return SupabaseBlobs(url, key, os.environ.get("BIP_STORAGE_BUCKET", "bip-files").strip() or "bip-files")
    if choice in ("", "database", "postgres") and dsn:
        from app.store.database import normalize_db_url

        return DatabaseBlobs(normalize_db_url(dsn))
    if choice not in ("", "local", "disk"):
        raise RuntimeError(f"BIP_FILE_STORAGE={choice} but its settings are missing (see app/store/blobs.py).")
    return LocalBlobs()
