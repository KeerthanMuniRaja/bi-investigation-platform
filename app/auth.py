"""
Sign-in: Google accounts through Supabase Auth.

The web app signs people in with Supabase (Google provider) and sends the
session's access token with every request. This module checks that token
and records who is asking; the store then shows each person only their
own dashboards, files, results and investigations (see `current_owner`).

Configuration (environment variables):
  SUPABASE_URL           https://<project>.supabase.co — turns sign-in on
  SUPABASE_JWT_SECRET    only for projects still on the legacy shared JWT
                         secret; otherwise tokens are checked against the
                         project's public signing keys (JWKS)
  BIP_ALLOWED_EMAILS     optional comma list of addresses allowed to sign in
  BIP_ALLOWED_DOMAINS    optional comma list of domains, e.g. jmangroup.com

Without SUPABASE_URL sign-in is off: everything belongs to one local
workspace, as before (local development and the test suite).
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
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
    email: str
    name: Optional[str] = None
    user_id: Optional[str] = None


def _csv(name: str) -> set[str]:
    return {x.strip().lower() for x in os.environ.get(name, "").split(",") if x.strip()}


def auth_enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL", "").strip())


def _supabase_url() -> str:
    return os.environ["SUPABASE_URL"].strip().rstrip("/")


@lru_cache(maxsize=4)
def _jwks_client(url: str):
    import jwt

    # signing keys are cached; a key rotation is picked up on the next unknown "kid"
    return jwt.PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json", cache_keys=True, lifespan=3600)


def verify_token(token: str) -> User:
    """Check a Supabase access token and return who it belongs to."""
    import jwt

    url = _supabase_url()
    secret = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
    options = {"require": ["exp", "sub"]}
    try:
        if secret:
            claims = jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated", options=options)
        else:
            key = _jwks_client(url).get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=["RS256", "ES256", "EdDSA"], audience="authenticated",
                                issuer=f"{url}/auth/v1", options=options)
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(401, "Your session has expired — sign in again.") from e
    except jwt.PyJWTError as e:
        raise HTTPException(401, "Not signed in (the sign-in token isn't valid).") from e
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(401, "This sign-in has no email address.")
    meta = claims.get("user_metadata") or {}
    return User(email=email, name=meta.get("full_name") or meta.get("name"), user_id=claims.get("sub"))


def check_allowed(email: str) -> None:
    emails, domains = _csv("BIP_ALLOWED_EMAILS"), _csv("BIP_ALLOWED_DOMAINS")
    if not emails and not domains:
        return
    if email in emails or email.rsplit("@", 1)[-1] in domains:
        return
    raise HTTPException(403, f"{email} isn't allowed to use this app. Ask the administrator to add your address.")


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
    user = await run_in_threadpool(verify_token, token.strip())  # may fetch signing keys over the network
    check_allowed(user.email)
    _owner.set(user.email)
    request.state.user = user
    return user
