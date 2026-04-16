"""Tests for ``python -m app.admin`` subcommands (M5 Task 5.2).

Invokes the CLI's ``main`` function directly rather than
``subprocess.run`` so we can use the shared migrations fixture and keep
the tests fast. End-to-end "does ``-m app.admin`` actually resolve?"
coverage is provided by a single smoke test near the end.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.admin.cli import main
from app.auth.passwords import verify_password
from app.db import Db, get_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_migrations(data_dir: Path) -> None:
    prev = os.environ.get("HARMONY_DATA_DIR")
    os.environ["HARMONY_DATA_DIR"] = str(data_dir)
    try:
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        if prev is None:
            os.environ.pop("HARMONY_DATA_DIR", None)
        else:
            os.environ["HARMONY_DATA_DIR"] = prev


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    _run_migrations(tmp_path)
    return Db(get_engine(tmp_path)), tmp_path


# --- create-user ---------------------------------------------------------


def test_create_user_inserts_row(ctx, capsys):
    db, _ = ctx
    rc = main(["create-user", "--email", "alice@example.com", "--password", "pw"])
    assert rc == 0
    out = capsys.readouterr().out
    user = db.get_user_by_email("alice@example.com")
    assert user is not None
    assert user.id in out  # printed the created user id
    assert verify_password("pw", user.password_hash)


def test_create_user_duplicate_email_fails(ctx, capsys):
    db, _ = ctx
    db.insert_user(email="dup@example.com", password_hash="x")
    rc = main(["create-user", "--email", "dup@example.com", "--password", "pw"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "exists" in err.lower() or "duplicate" in err.lower()


def test_create_user_with_admin_flag(ctx):
    db, _ = ctx
    rc = main(["create-user", "--email", "admin@example.com", "--password", "pw", "--admin"])
    assert rc == 0
    row = db.get_user_by_email("admin@example.com")
    assert row is not None
    assert row.is_admin is True


# --- list-users ----------------------------------------------------------


def test_list_users_outputs_emails(ctx, capsys):
    db, _ = ctx
    db.insert_user(email="a@example.com", password_hash="x")
    db.insert_user(email="b@example.com", password_hash="x")
    rc = main(["list-users"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "a@example.com" in out
    assert "b@example.com" in out


def test_list_users_empty_is_still_zero(ctx, capsys):
    rc = main(["list-users"])
    assert rc == 0


# --- delete-user ---------------------------------------------------------


def test_delete_user_removes_row(ctx):
    db, _ = ctx
    uid = db.insert_user(email="gone@example.com", password_hash="x")
    rc = main(["delete-user", "--email", "gone@example.com"])
    assert rc == 0
    assert db.get_user_by_id(uid) is None


def test_delete_user_missing_is_error(ctx, capsys):
    rc = main(["delete-user", "--email", "ghost@example.com"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err.lower() or "no such" in err.lower()


# --- reset-password ------------------------------------------------------


def test_reset_password_changes_hash_and_verifies(ctx):
    db, _ = ctx
    uid = db.insert_user(email="r@example.com", password_hash="OLD_HASH_STRING")
    rc = main(["reset-password", "--email", "r@example.com", "--password", "newpw"])
    assert rc == 0
    row = db.get_user_by_id(uid)
    assert row is not None
    assert row.password_hash != "OLD_HASH_STRING"
    assert verify_password("newpw", row.password_hash)


def test_reset_password_missing_user_is_error(ctx):
    rc = main(["reset-password", "--email", "ghost@example.com", "--password", "x"])
    assert rc == 1


# --- no-subcommand -------------------------------------------------------


def test_no_subcommand_prints_help(capsys):
    rc = main([])
    # argparse's print_help() returns None and sys.exit is not invoked;
    # our main returns 0 for the help path (nothing to do, not an error).
    assert rc in (0, 2)
    out = capsys.readouterr().out + capsys.readouterr().err
    # Either stdout or stderr contains the usage line depending on how
    # we chose to surface help.
    # Loose assertion: at least one known subcommand name is mentioned.
    assert "create-user" in out or "usage" in out.lower()


# --- smoke: module resolves via -m app.admin -----------------------------


def test_python_dash_m_resolves(ctx):
    """Guard against the package accidentally losing its __main__.py."""
    _, data_dir = ctx
    env = os.environ.copy()
    env["HARMONY_DATA_DIR"] = str(data_dir)
    r = subprocess.run(
        [sys.executable, "-m", "app.admin", "list-users"],
        cwd=str(BACKEND_DIR),
        env={**env, "PYTHONPATH": str(BACKEND_DIR)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, f"stderr={r.stderr}"
