"""
Manage sign-in accounts from the command line (for when self-sign-up is off).

    python scripts/create_user.py add keerthan --name "Keerthan"     # asks for the password
    python scripts/create_user.py reset-password keerthan            # new password, ends all sessions
    python scripts/create_user.py disable keerthan                   # can't sign in any more
    python scripts/create_user.py enable keerthan
    python scripts/create_user.py list
    python scripts/create_user.py claim-local keerthan               # move the no-sign-in workspace into this account

Works on whichever database the API uses: DATABASE_URL (from the
environment or .env) or the local SQLite file in BIP_DATA_DIR / ./data.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def ask_password() -> str:
    from app.auth import MIN_PASSWORD

    while True:
        pw = getpass.getpass("Password: ")
        if len(pw) < MIN_PASSWORD:
            print(f"  at least {MIN_PASSWORD} characters, please")
            continue
        if getpass.getpass("Repeat it: ") != pw:
            print("  the two didn't match — try again")
            continue
        return pw


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass
    from fastapi import HTTPException

    from app.auth import check_username, hash_password, normalize_username
    from app.models.graph import utcnow
    from app.store.database import UsernameTaken, open_database

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="create an account")
    add.add_argument("username")
    add.add_argument("--name", help="display name")
    for name in ("reset-password", "disable", "enable"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("list", help="show all accounts")
    sub.add_parser("claim-local", help="give the work made before sign-in was on to this account").add_argument("username")
    args = ap.parse_args()

    data_dir = Path(os.environ.get("BIP_DATA_DIR", str(ROOT / "data"))).resolve()
    db = open_database(data_dir)
    where = "Postgres (DATABASE_URL)" if os.environ.get("DATABASE_URL") else f"SQLite in {data_dir}"

    if args.cmd == "list":
        users = db.list_users()
        print(f"{len(users)} account(s) in {where}")
        for u in users:
            print(f"  {u['username']:24s} {u['display_name'] or '':20s} created {str(u['created_at'])[:16]}  "
                  f"last sign-in {str(u['last_login_at'] or '—')[:16]}{'  (disabled)' if u['disabled'] else ''}")
        return 0

    username = normalize_username(args.username)
    if args.cmd == "add":
        try:
            check_username(username)
        except HTTPException as e:
            print(e.detail)
            return 1
        password = ask_password()
        try:
            db.create_user({"id": str(uuid.uuid4()), "username": username, "display_name": args.name, "password_hash": hash_password(password),
                            "created_at": utcnow().isoformat(), "last_login_at": None, "token_version": 0, "disabled": False})
        except UsernameTaken:
            print(f"'{username}' already exists.")
            return 1
        print(f"Created '{username}' in {where}.")
        return 0

    user = db.get_user(username)
    if not user:
        print(f"No account called '{username}' in {where}.")
        return 1
    if args.cmd == "claim-local":
        moved = db.reassign_owner("local", username)
        print(f"Moved {moved} record(s) from the local workspace to '{username}'.")
        return 0
    if args.cmd == "reset-password":
        db.update_user(username, password_hash=hash_password(ask_password()), token_version=int(user["token_version"]) + 1)
        print(f"Password changed for '{username}'; their other sessions have ended.")
    else:
        off = args.cmd == "disable"
        db.update_user(username, disabled=off, token_version=int(user["token_version"]) + (1 if off else 0))
        print(f"'{username}' is now {'disabled' if off else 'enabled'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
