"""
Sign-in with a username and password, stored in the app's own database.

Accounts live in a `users` table (SQLite locally, Postgres when hosted).
Passwords are never stored: only a salted scrypt hash. Signing in returns a
signed token (JWT, HS256) that the web app sends with every request; this
module checks it and records who is asking, and the store then shows each
person only their own dashboards, files, results and investigations (see
`current_owner`).

Configuration (environment variables):
  BIP_AUTH=password      turns sign-in on
  BIP_SECRET_KEY         signs the tokens; set the same long random value on
                         every API server. Without it a key is generated and
                         kept in <BIP_DATA_DIR>/secret.key (fine for one server).
  BIP_ALLOW_SIGNUP       "false" to stop people creating their own accounts
                         (the very first account can always be created);
                         add accounts with scripts/create_user.py
  BIP_SESSION_DAYS       how long a sign-in lasts (default 7)

Without BIP_AUTH sign-in is off: everything belongs to one local workspace,
as before (local development and the test suite).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

LOCAL_OWNER = "local"

# who the current request belongs to; read by the store to scope every query
_owner: ContextVar[str] = ContextVar("bip_owner", default=LOCAL_OWNER)


def current_owner() -> str:
    return _owner.get()


def set_owner(owner: str):
    """Scripts and tests: act as this owner (returns a token for reset)."""
    return _owner.set(owner)


@dataclass(frozen=True)
class User:
    username: str
    name: Optional[str] = None


def auth_enabled() -> bool:
    return os.environ.get("BIP_AUTH", "").strip().lower() == "password"


def signup_allowed() -> bool:
    return os.environ.get("BIP_ALLOW_SIGNUP", "true").strip().lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------- passwords
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD = 8
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def check_username(username: str) -> str:
    u = normalize_username(username)
    if not USERNAME_RE.fullmatch(u):
        raise HTTPException(400, "Usernames are 3–32 characters: letters, numbers, dots, dashes or underscores, starting with a letter or number.")
    return u


def check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise HTTPException(400, f"Passwords need at least {MIN_PASSWORD} characters.")
    if len(password) > 256:
        raise HTTPException(400, "That password is too long.")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, maxmem=64 * 1024 * 1024, **_SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: Optional[str]) -> bool:
    try:
        _, n, r, p, salt, digest = (stored or "").split("$")
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(expected), maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(got, expected)
    except ValueError:
        return False


_DUMMY_HASH = hash_password(secrets.token_hex(8))  # equal work for unknown usernames (no account probing)


# -------------------------------------------------------------- throttling
class _Throttle:
    """After repeated wrong passwords for a username (or from one address),
    make them wait — slows down password guessing."""
    LIMIT, WINDOW, LOCK = 8, 15 * 60, 15 * 60

    def __init__(self):
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, *keys: str) -> None:
        now = time.time()
        with self._lock:
            for key in keys:
                recent = [t for t in self._fails.get(key, []) if now - t < self.WINDOW]
                self._fails[key] = recent
                if len(recent) >= self.LIMIT:
                    wait = int(self.LOCK - (now - recent[-self.LIMIT])) // 60 + 1
                    raise HTTPException(429, f"Too many attempts. Try again in about {wait} minute{'s' if wait != 1 else ''}.")

    def fail(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._fails.setdefault(key, []).append(time.time())

    def clear(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._fails.pop(key, None)


throttle = _Throttle()


# ------------------------------------------------------------------ tokens
_key_lock = threading.Lock()
_generated_key: Optional[str] = None


def _secret_key() -> str:
    key = os.environ.get("BIP_SECRET_KEY", "").strip()
    if key:
        return key
    if os.environ.get("VERCEL"):
        # each Vercel instance would invent its own key and sign everyone out
        raise HTTPException(500, "BIP_SECRET_KEY isn't set: add it in the Vercel project's Environment Variables.")
    global _generated_key
    with _key_lock:
        if _generated_key is None:
            path = Path(os.environ.get("BIP_DATA_DIR") or "./data").resolve() / "secret.key"
            if path.exists():
                _generated_key = path.read_text(encoding="utf-8").strip()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                _generated_key = secrets.token_urlsafe(48)
                path.write_text(_generated_key, encoding="utf-8")
        return _generated_key


def issue_token(username: str, version: int) -> str:
    import jwt

    days = float(os.environ.get("BIP_SESSION_DAYS", "7") or 7)
    now = int(time.time())
    return jwt.encode({"sub": username, "ver": version, "iat": now, "exp": now + int(days * 86400), "aud": "bip"},
                      _secret_key(), algorithm="HS256")


def read_token(token: str) -> tuple[str, int]:
    import jwt

    try:
        claims = jwt.decode(token, _secret_key(), algorithms=["HS256"], audience="bip", options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(401, "Your session has expired — sign in again.") from e
    except jwt.PyJWTError as e:
        raise HTTPException(401, "Not signed in.") from e
    return str(claims["sub"]), int(claims.get("ver", 0))


# --------------------------------------------------------------- dependency
async def require_user(request: Request) -> Optional[User]:
    """FastAPI dependency on every data route: checks the bearer token and
    scopes the request to its owner. (Async on purpose: the owner set here
    must be visible to the endpoint, which runs in a copy of this context.)"""
    if not auth_enabled():
        _owner.set(LOCAL_OWNER)
        return None
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Sign in to use this app.", headers={"WWW-Authenticate": "Bearer"})
    username, version = read_token(token.strip())
    from app.store.database import get_store

    account = await run_in_threadpool(get_store().db.get_user, username)
    # a password change (or a disabled account) ends every earlier sign-in
    if account is None or account["disabled"] or int(account["token_version"]) != version:
        raise HTTPException(401, "Your session has ended — sign in again.")
    _owner.set(username)
    user = User(username=username, name=account.get("display_name"))
    request.state.user = user
    return user
