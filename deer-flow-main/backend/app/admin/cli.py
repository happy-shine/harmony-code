"""Admin CLI for harmony-code user management (M5 Task 5.2).

Invocation::

    python -m app.admin create-user --email x@y.com --password ...
    python -m app.admin list-users
    python -m app.admin delete-user --email x@y.com
    python -m app.admin reset-password --email x@y.com --password ...

Uses ``argparse`` (no extra dep) and operates on whatever ``harmony.db``
``$HARMONY_DATA_DIR`` resolves to — so the same env var that controls
the gateway's data location also controls where the CLI reads/writes.
All commands exit 0 on success, 1 on a "business" error (e.g. duplicate
email, user not found), and print a short human-readable message to
stdout (success) or stderr (error).

No interactive prompts — passwords are passed on argv. Yes, this means
the password lands in shell history and ``ps``; for a single-tenant
homelab CLI that's acceptable and matches the spec. Future: read from a
file path (``--password-file``) or stdin.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from app.auth.passwords import hash_password
from app.db import Db, UserExistsError, get_engine


def _db() -> Db:
    return Db(get_engine())


def cmd_create_user(args: argparse.Namespace) -> int:
    db = _db()
    try:
        uid = db.insert_user(
            email=args.email,
            password_hash=hash_password(args.password),
            is_admin=bool(args.admin),
        )
    except UserExistsError:
        print(f"user already exists: {args.email}", file=sys.stderr)
        return 1
    print(f"created user: id={uid} email={args.email.strip().lower()} admin={bool(args.admin)}")
    return 0


def cmd_list_users(args: argparse.Namespace) -> int:
    db = _db()
    rows = db.list_users()
    if not rows:
        print("(no users)")
        return 0
    # Aligned two-column output. id column is fixed-width (u_ + 12 hex).
    for r in rows:
        admin_mark = "  [admin]" if r.is_admin else ""
        print(f"{r.id}  {r.email}{admin_mark}")
    return 0


def cmd_delete_user(args: argparse.Namespace) -> int:
    db = _db()
    row = db.get_user_by_email(args.email)
    if row is None:
        print(f"user not found: {args.email}", file=sys.stderr)
        return 1
    db.delete_user(row.id)
    print(f"deleted user: id={row.id} email={row.email}")
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    db = _db()
    row = db.get_user_by_email(args.email)
    if row is None:
        print(f"user not found: {args.email}", file=sys.stderr)
        return 1
    db.update_user_password(row.id, password_hash=hash_password(args.password))
    # Invalidate existing sessions so the new password takes effect everywhere.
    # (We don't have delete_auth_sessions_for_user — inline it.)
    from sqlalchemy import text

    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM auth_sessions WHERE user_id = :uid"), {"uid": row.id})
    print(f"reset password for: id={row.id} email={row.email}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="app.admin",
        description="harmony-code admin CLI (user management for session-cookie auth).",
    )
    sub = p.add_subparsers(dest="cmd")

    s_create = sub.add_parser("create-user", help="create a new user")
    s_create.add_argument("--email", required=True)
    s_create.add_argument("--password", required=True)
    s_create.add_argument(
        "--admin",
        action="store_true",
        help="mark the user as an admin (is_admin=true)",
    )
    s_create.set_defaults(func=cmd_create_user)

    s_list = sub.add_parser("list-users", help="list all users")
    s_list.set_defaults(func=cmd_list_users)

    s_del = sub.add_parser("delete-user", help="delete a user by email")
    s_del.add_argument("--email", required=True)
    s_del.set_defaults(func=cmd_delete_user)

    s_pw = sub.add_parser("reset-password", help="reset a user's password (invalidates sessions)")
    s_pw.add_argument("--email", required=True)
    s_pw.add_argument("--password", required=True)
    s_pw.set_defaults(func=cmd_reset_password)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
