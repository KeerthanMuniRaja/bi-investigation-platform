"""Accounts: create one, sign in, change the password (username + password)."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.auth import (
    _DUMMY_HASH, User, auth_enabled, check_password, check_username, hash_password, issue_token, normalize_username,
    require_user, signup_allowed, throttle, verify_password,
)
from app.models.graph import utcnow
from app.store.database import Store, UsernameTaken, get_store

router = APIRouter(prefix="/auth", tags=["account"])


class Credentials(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=256)


class SignupRequest(Credentials):
    display_name: Optional[str] = Field(None, max_length=80)


class PasswordChange(BaseModel):
    current_password: str = Field(..., max_length=256)
    new_password: str = Field(..., max_length=256)


def _session(user: dict) -> dict:
    return {"token": issue_token(user["username"], int(user["token_version"])),
            "user": {"username": user["username"], "name": user.get("display_name") or user["username"]}}


def _require_on() -> None:
    if not auth_enabled():
        raise HTTPException(400, "Sign-in isn't switched on for this server (set BIP_AUTH=password).")


@router.post("/signup")
def signup(req: SignupRequest, store: Store = Depends(get_store)) -> dict:
    _require_on()
    # the first account can always be created, so a closed server can be set up
    if not signup_allowed() and store.db.count_users() > 0:
        raise HTTPException(403, "New accounts can't be created here. Ask the administrator for one.")
    username = check_username(req.username)
    check_password(req.password)
    user = {"id": str(uuid.uuid4()), "username": username, "display_name": (req.display_name or "").strip() or None,
            "password_hash": hash_password(req.password), "created_at": utcnow().isoformat(), "last_login_at": utcnow().isoformat(),
            "token_version": 0, "disabled": False}
    try:
        store.db.create_user(user)
    except UsernameTaken as e:
        raise HTTPException(409, "That username is taken — pick another one.") from e
    return _session(user)


@router.post("/login")
def login(req: Credentials, request: Request, store: Store = Depends(get_store)) -> dict:
    _require_on()
    username = normalize_username(req.username)
    ip = request.client.host if request.client else "?"
    keys = (f"user:{username}", f"ip:{ip}")
    throttle.check(*keys)
    user = store.db.get_user(username) if username else None
    ok = verify_password(req.password, user["password_hash"] if user else _DUMMY_HASH)
    if not user or not ok:
        throttle.fail(*keys)
        raise HTTPException(401, "That username and password don't match.")
    if user["disabled"]:
        raise HTTPException(403, "This account is switched off. Ask the administrator.")
    throttle.clear(f"user:{username}")
    store.db.update_user(username, last_login_at=utcnow().isoformat())
    return _session(user)


@router.post("/password")
async def change_password(req: PasswordChange, user: Optional[User] = Depends(require_user), store: Store = Depends(get_store)) -> dict:
    _require_on()
    account = await run_in_threadpool(store.db.get_user, user.username)
    if not account or not verify_password(req.current_password, account["password_hash"]):
        raise HTTPException(400, "Your current password isn't right.")
    check_password(req.new_password)
    version = int(account["token_version"]) + 1  # every other sign-in of this account ends
    await run_in_threadpool(store.db.update_user, user.username, password_hash=hash_password(req.new_password), token_version=version)
    return _session({**account, "token_version": version})
