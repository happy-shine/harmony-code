"""Unit tests for the users + auth_sessions Db methods (Task 5.2).

Focuses on the raw ``Db`` API; router- and CLI-level tests live
elsewhere. We verify uniqueness, expiry, touch, delete, and sweep
semantics.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.db import Db, UserExistsError, get_engine

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
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    _run_migrations(tmp_path)
    return Db(get_engine(tmp_path))


# --- users ---------------------------------------------------------------


def test_insert_user_returns_prefixed_id(db):
    uid = db.insert_user(email="a@b.com", password_hash="$argon2id$v=19$...hash")
    assert uid.startswith("u_")
    assert len(uid) > len("u_")


def test_get_user_by_email_roundtrip(db):
    uid = db.insert_user(email="alice@example.com", password_hash="H")
    row = db.get_user_by_email("alice@example.com")
    assert row is not None
    assert row.id == uid
    assert row.email == "alice@example.com"
    assert row.password_hash == "H"
    assert row.is_admin is False


def test_get_user_by_id_roundtrip(db):
    uid = db.insert_user(email="bob@example.com", password_hash="H")
    row = db.get_user_by_id(uid)
    assert row is not None
    assert row.email == "bob@example.com"


def test_get_user_by_email_missing_returns_none(db):
    assert db.get_user_by_email("nobody@example.com") is None


def test_get_user_by_id_missing_returns_none(db):
    assert db.get_user_by_id("u_nope") is None


def test_insert_user_duplicate_email_raises(db):
    db.insert_user(email="dup@example.com", password_hash="H1")
    with pytest.raises(UserExistsError):
        db.insert_user(email="dup@example.com", password_hash="H2")


def test_insert_user_with_admin_flag(db):
    uid = db.insert_user(email="admin@example.com", password_hash="H", is_admin=True)
    row = db.get_user_by_id(uid)
    assert row is not None
    assert row.is_admin is True


def test_list_users_returns_all(db):
    db.insert_user(email="a@example.com", password_hash="H")
    db.insert_user(email="b@example.com", password_hash="H")
    rows = db.list_users()
    emails = sorted(r.email for r in rows)
    assert emails == ["a@example.com", "b@example.com"]


def test_delete_user_removes_row(db):
    uid = db.insert_user(email="gone@example.com", password_hash="H")
    db.delete_user(uid)
    assert db.get_user_by_id(uid) is None


def test_update_user_password_changes_hash(db):
    uid = db.insert_user(email="pw@example.com", password_hash="OLD")
    db.update_user_password(uid, password_hash="NEW")
    row = db.get_user_by_id(uid)
    assert row is not None
    assert row.password_hash == "NEW"


# --- auth_sessions -------------------------------------------------------


def test_create_auth_session_returns_row_with_token(db):
    uid = db.insert_user(email="sess@example.com", password_hash="H")
    row = db.create_auth_session(user_id=uid, ttl_seconds=3600, user_agent="UA", ip="1.2.3.4")
    assert row.id  # opaque token
    assert len(row.id) >= 32  # 32 hex chars from token_hex(16)
    assert row.user_id == uid
    assert row.user_agent == "UA"
    assert row.ip == "1.2.3.4"
    assert row.expires_at > row.created_at


def test_get_auth_session_returns_row_when_valid(db):
    uid = db.insert_user(email="v@example.com", password_hash="H")
    created = db.create_auth_session(user_id=uid, ttl_seconds=3600, user_agent=None, ip=None)
    row = db.get_auth_session(created.id)
    assert row is not None
    assert row.user_id == uid


def test_get_auth_session_returns_none_for_unknown_token(db):
    assert db.get_auth_session("token_does_not_exist") is None


def test_get_auth_session_returns_none_for_expired(db):
    uid = db.insert_user(email="exp@example.com", password_hash="H")
    created = db.create_auth_session(user_id=uid, ttl_seconds=-10, user_agent=None, ip=None)
    # ttl_seconds < 0 → expires_at already in the past
    assert db.get_auth_session(created.id) is None


def test_touch_auth_session_updates_last_seen(db):
    uid = db.insert_user(email="touch@example.com", password_hash="H")
    created = db.create_auth_session(user_id=uid, ttl_seconds=3600, user_agent=None, ip=None)
    original_last_seen = created.last_seen_at
    # Force the touched value to be >= original by passing an explicit now
    later = datetime.now(timezone.utc) + timedelta(seconds=60)
    db.touch_auth_session(created.id, now=later)
    row = db.get_auth_session(created.id)
    assert row is not None
    # SQLite stores last_seen_at as text; compare string or datetime forms.
    touched = row.last_seen_at
    if isinstance(touched, str):
        touched_dt = datetime.fromisoformat(touched.replace("Z", "+00:00"))
    else:
        touched_dt = touched
    if isinstance(original_last_seen, str):
        orig_dt = datetime.fromisoformat(original_last_seen.replace("Z", "+00:00"))
    else:
        orig_dt = original_last_seen
    # Normalize tzinfo
    if touched_dt.tzinfo is None:
        touched_dt = touched_dt.replace(tzinfo=timezone.utc)
    if orig_dt.tzinfo is None:
        orig_dt = orig_dt.replace(tzinfo=timezone.utc)
    assert touched_dt >= orig_dt


def test_delete_auth_session_removes_row(db):
    uid = db.insert_user(email="out@example.com", password_hash="H")
    created = db.create_auth_session(user_id=uid, ttl_seconds=3600, user_agent=None, ip=None)
    db.delete_auth_session(created.id)
    assert db.get_auth_session(created.id) is None


def test_sweep_expired_sessions_removes_expired_leaves_fresh(db):
    uid = db.insert_user(email="sweep@example.com", password_hash="H")
    fresh = db.create_auth_session(user_id=uid, ttl_seconds=3600, user_agent=None, ip=None)
    expired = db.create_auth_session(user_id=uid, ttl_seconds=-10, user_agent=None, ip=None)
    db.sweep_expired_sessions()
    assert db.get_auth_session(fresh.id) is not None
    assert db.get_auth_session(expired.id) is None
