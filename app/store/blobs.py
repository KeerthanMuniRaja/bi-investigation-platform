"""
Where uploaded files live.

The API always reads files from its own disk (pbixray needs a real file
path). With cloud storage configured, every upload is also copied to a
private Supabase Storage bucket, so any API server (a new instance, or the
same one after a redeploy wiped its disk) can fetch the file back on first
use. Without it, files stay in the uploads folder only.

  SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY   turn cloud storage on
  BIP_STORAGE_BUCKET                         bucket name (default "bip-files"),
                                             created as private if missing

The service-role key bypasses all access rules: it belongs on the API
server only — never in the web app or the browser.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Protocol
from urllib.parse import quote

CHUNK = 1024 * 1024


class Blobs(Protocol):
    enabled: bool

    def folder(self, owner: str) -> str: ...
    def sign_upload(self, key: str) -> str: ...
    def put(self, owner: str, name: str, path: Path) -> Optional[str]: ...
    def fetch(self, key: str, path: Path) -> None: ...
    def delete(self, key: str) -> None: ...


class LocalBlobs:
    """No cloud storage: the uploads folder is the only copy."""
    enabled = False

    def folder(self, owner: str) -> str:
        return SupabaseBlobs.folder(owner)

    def sign_upload(self, key: str) -> str:
        raise RuntimeError("Direct uploads need cloud storage (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY).")

    def put(self, owner: str, name: str, path: Path) -> Optional[str]:
        return None

    def fetch(self, key: str, path: Path) -> None:
        raise FileNotFoundError(f"{path.name} isn't on this server and no cloud storage is configured.")

    def delete(self, key: str) -> None:
        return None


class SupabaseBlobs:
    """Private Supabase Storage bucket, through its REST API."""
    enabled = True

    def __init__(self, url: str, service_key: str, bucket: str = "bip-files", client=None, timeout: float = 600.0):
        import httpx

        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0))
        self.headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
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

    def sign_upload(self, key: str) -> str:
        """A one-time URL the browser can PUT a file to directly — big uploads
        then never pass through the API (Vercel caps requests at ~4.5 MB)."""
        self._ensure_bucket()
        r = self.client.post(f"{self.base}/object/upload/sign/{self.bucket}/{quote(key, safe='/')}", headers=self.headers, json={})
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Couldn't prepare the upload: {r.status_code} {r.text[:200]}")
        url = r.json().get("url") or ""
        return url if url.startswith("http") else f"{self.base}{url if url.startswith('/') else '/' + url}"

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


def blobs_from_env() -> Blobs:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if url and key:
        return SupabaseBlobs(url, key, os.environ.get("BIP_STORAGE_BUCKET", "bip-files").strip() or "bip-files")
    return LocalBlobs()
