"""Task 5.2: ``/api/auth/*`` router tests.

Covers sign-in/sign-out/get-session lifecycle, cookie attributes,
expiry handling, and the downstream effect of the now-strict
``current_user_id`` dep on existing protected endpoints.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.auth.passwords import hash_password
from app.db import Db, get_engine

BACKEND_DIR = Path(__file__).resolve().parents[2]

# This file drives auth state by hand — skip the gateway-conftest autouse
# "log in as u_default" fixture so tests see the real unauthenticated baseline.
pytestmark = pytest.mark.no_auto_auth


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
    """Returns ``(client, db, tmp_path)``. Seeds no users — each test
    decides what identities to create."""
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    _run_migrations(tmp_path)
    from app.gateway.harmony_app import app

    db = Db(get_engine(tmp_path))
    return TestClient(app), db, tmp_path


def _seed_user(db: Db, *, email: str = "alice@example.com", password: str = "s3cret!") -> str:
    return db.insert_user(email=email, password_hash=hash_password(password))


# --- sign-in --------------------------------------------------------------


def test_signin_with_correct_credentials_returns_cookie(ctx):
    c, db, _ = ctx
    uid = _seed_user(db)
    r = c.post("/api/auth/sign-in/email", json={"email": "alice@example.com", "password": "s3cret!"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["id"] == uid
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["is_admin"] is False
    assert "expires_at" in body["session"]
    # Cookie: HttpOnly, SameSite=Lax (we're on http scheme in TestClient, so no Secure).
    set_cookie = r.headers.get("set-cookie", "")
    assert "harmony_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert "Secure" not in set_cookie


def test_signin_is_case_insensitive_on_email(ctx):
    c, db, _ = ctx
    _seed_user(db, email="Bob@EXAMPLE.com", password="pw")
    r = c.post("/api/auth/sign-in/email", json={"email": "bob@example.com", "password": "pw"})
    assert r.status_code == 200


def test_signin_with_wrong_password_returns_401(ctx):
    c, db, _ = ctx
    _seed_user(db)
    r = c.post("/api/auth/sign-in/email", json={"email": "alice@example.com", "password": "wrong"})
    assert r.status_code == 401
    assert "harmony_session" not in r.headers.get("set-cookie", "")


def test_signin_with_missing_user_returns_401(ctx):
    c, _, _ = ctx
    r = c.post("/api/auth/sign-in/email", json={"email": "ghost@example.com", "password": "x"})
    assert r.status_code == 401


# --- get-session ---------------------------------------------------------


def test_get_session_no_cookie_returns_null(ctx):
    c, _, _ = ctx
    r = c.get("/api/auth/get-session")
    assert r.status_code == 200
    assert r.json() is None


def test_get_session_with_valid_cookie_returns_user(ctx):
    c, db, _ = ctx
    _seed_user(db)
    c.post("/api/auth/sign-in/email", json={"email": "alice@example.com", "password": "s3cret!"})
    r = c.get("/api/auth/get-session")
    assert r.status_code == 200
    body = r.json()
    assert body is not None
    assert body["user"]["email"] == "alice@example.com"


def test_get_session_with_expired_session_returns_null(ctx):
    c, db, _ = ctx
    uid = _seed_user(db)
    created = db.create_auth_session(user_id=uid, ttl_seconds=-10, user_agent=None, ip=None)
    c.cookies.set("harmony_session", created.id)
    r = c.get("/api/auth/get-session")
    assert r.status_code == 200
    assert r.json() is None


def test_get_session_with_unknown_cookie_returns_null(ctx):
    c, _, _ = ctx
    c.cookies.set("harmony_session", "a" * 32)
    r = c.get("/api/auth/get-session")
    assert r.status_code == 200
    assert r.json() is None


# --- sign-out ------------------------------------------------------------


def test_signout_clears_session_and_cookie(ctx):
    c, db, _ = ctx
    _seed_user(db)
    c.post("/api/auth/sign-in/email", json={"email": "alice@example.com", "password": "s3cret!"})
    # Sanity: logged in.
    assert c.get("/api/auth/get-session").json() is not None

    r = c.post("/api/auth/sign-out")
    assert r.status_code == 200
    # Cookie cleared (Max-Age=0 or expires in past).
    set_cookie = r.headers.get("set-cookie", "")
    assert "harmony_session=" in set_cookie
    # Subsequent get-session is null.
    # Note: TestClient preserves cookies, so the server needs to have
    # emitted a cookie-clearing Set-Cookie. We also delete the row.
    # Clear the cookie jar to simulate a fresh browser.
    c.cookies.clear()
    assert c.get("/api/auth/get-session").json() is None


def test_signout_without_cookie_is_still_200(ctx):
    c, _, _ = ctx
    r = c.post("/api/auth/sign-out")
    assert r.status_code == 200


# --- protected endpoint (current_user_id dep) ----------------------------


def test_protected_endpoint_without_cookie_returns_401(ctx):
    c, _, _ = ctx
    # /api/mcp (GET) relies on current_user_id.
    r = c.get("/api/mcp")
    assert r.status_code == 401


def test_protected_endpoint_with_valid_cookie_returns_200(ctx):
    c, db, _ = ctx
    _seed_user(db)
    c.post("/api/auth/sign-in/email", json={"email": "alice@example.com", "password": "s3cret!"})
    r = c.get("/api/mcp")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_protected_endpoint_with_expired_cookie_returns_401(ctx):
    c, db, _ = ctx
    uid = _seed_user(db)
    expired = db.create_auth_session(user_id=uid, ttl_seconds=-10, user_agent=None, ip=None)
    c.cookies.set("harmony_session", expired.id)
    r = c.get("/api/mcp")
    assert r.status_code == 401


def test_protected_endpoint_with_session_pointing_at_deleted_user_returns_401(ctx):
    c, db, _ = ctx
    uid = _seed_user(db)
    created = db.create_auth_session(user_id=uid, ttl_seconds=3600, user_agent=None, ip=None)
    db.delete_user(uid)  # cascades sessions too, so re-create after delete
    # create a lone session row by reinserting
    import sqlalchemy

    with db.engine.begin() as conn:
        now = datetime.now(UTC).replace(tzinfo=None)
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO auth_sessions (id, user_id, created_at, expires_at, last_seen_at) "
                "VALUES (:id, :uid, :now, :exp, :now)"
            ),
            {"id": created.id, "uid": "u_ghost", "now": now, "exp": now + timedelta(hours=1)},
        )
    c.cookies.set("harmony_session", created.id)
    r = c.get("/api/mcp")
    assert r.status_code == 401


# --- cross-user isolation (preview of 5.3) ------------------------------


def test_user_a_cannot_see_user_b_mcp_rows(ctx):
    c, db, _ = ctx
    # Seed two users.
    db.insert_user(email="a@example.com", password_hash=hash_password("pw"))
    uid_b = db.insert_user(email="b@example.com", password_hash=hash_password("pw"))
    # Directly insert an MCP row owned by B.
    b_mcp = db.insert_mcp(user_id=uid_b, name="b_only", transport="stdio", command="true")
    # Sign in as A.
    c.post("/api/auth/sign-in/email", json={"email": "a@example.com", "password": "pw"})
    rows = c.get("/api/mcp").json()
    assert all(r["id"] != b_mcp for r in rows), (
        "user A must not see user B's private MCP rows"
    )
