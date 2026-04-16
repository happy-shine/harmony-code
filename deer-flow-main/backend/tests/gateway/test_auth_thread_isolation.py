"""Task 5.3: every ``/api/threads/{tid}/*`` endpoint must 404 on cross-user access.

We use 404 (not 403) to avoid leaking "that tid exists but isn't yours" —
same convention the uploads router already uses for cross-thread row lookups.

Each test creates a thread as ``u_alice``, then swaps the ``current_user_id``
/ ``current_user`` dependency overrides to ``u_mallory`` and hits each
endpoint. A 404 (not 200/500/401) means the ownership check fired.

The autouse ``_auto_login_u_default`` fixture in ``conftest.py`` installs
overrides that we re-install here — FastAPI just looks up the current
value of ``app.dependency_overrides`` at request time, so the last
assignment wins.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.db import UserRow

BACKEND_DIR = Path(__file__).resolve().parents[2]


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


def _login_as(uid: str) -> None:
    """Swap the app-level auth overrides to point at ``uid``.

    Both ``current_user_id`` and ``current_user`` are overridden — routers
    use whichever is handier, and we need cross-user isolation regardless.
    """
    from app.gateway.deps import current_user, current_user_id
    from app.gateway.harmony_app import app

    def _uid() -> str:
        return uid

    def _user() -> UserRow:
        return UserRow(
            id=uid,
            email=f"{uid}@example.com",
            password_hash="",
            created_at=None,
            is_admin=False,
        )

    app.dependency_overrides[current_user_id] = _uid
    app.dependency_overrides[current_user] = _user


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    _run_migrations(tmp_path)
    from app.gateway.harmony_app import app

    return TestClient(app), tmp_path


def _alice_creates_thread(c: TestClient) -> str:
    _login_as("u_alice")
    r = c.post("/api/threads", json={})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# --- messages router --------------------------------------------------------


def test_send_message_rejects_other_user_404(client):
    c, _ = client
    tid = _alice_creates_thread(c)
    _login_as("u_mallory")
    r = c.post(f"/api/threads/{tid}/messages", json={"content": "hi"})
    assert r.status_code == 404, r.text


def test_cancel_rejects_other_user_404(client):
    c, _ = client
    tid = _alice_creates_thread(c)
    _login_as("u_mallory")
    r = c.post(f"/api/threads/{tid}/cancel")
    assert r.status_code == 404, r.text


# --- workspace router ------------------------------------------------------


def test_workspace_tree_rejects_other_user_404(client):
    c, _ = client
    tid = _alice_creates_thread(c)
    _login_as("u_mallory")
    r = c.get(f"/api/threads/{tid}/workspace/tree")
    assert r.status_code == 404, r.text


def test_workspace_file_rejects_other_user_404(client):
    c, _ = client
    tid = _alice_creates_thread(c)
    # Alice drops a file in her workspace. Without ownership enforcement,
    # Mallory could GET it.
    from app.gateway.deps import session_store

    row = session_store().get(tid)
    assert row is not None
    (Path(row.cwd) / "secret.txt").write_text("alice-only")

    _login_as("u_mallory")
    r = c.get(f"/api/threads/{tid}/workspace/files/secret.txt")
    assert r.status_code == 404, r.text
    assert "alice-only" not in r.text


# --- uploads router --------------------------------------------------------


def test_uploads_post_rejects_other_user_404(client):
    c, _ = client
    tid = _alice_creates_thread(c)
    _login_as("u_mallory")
    r = c.post(
        f"/api/threads/{tid}/uploads",
        files=[("files", ("evil.txt", b"x", "text/plain"))],
    )
    assert r.status_code == 404, r.text


def test_uploads_list_rejects_other_user_404(client):
    c, _ = client
    tid = _alice_creates_thread(c)
    _login_as("u_mallory")
    r = c.get(f"/api/threads/{tid}/uploads")
    assert r.status_code == 404, r.text


def test_uploads_delete_rejects_other_user_404(client):
    c, _ = client
    tid = _alice_creates_thread(c)
    # Seed an upload as alice.
    up = c.post(
        f"/api/threads/{tid}/uploads",
        files=[("files", ("a.txt", b"1", "text/plain"))],
    ).json()[0]
    _login_as("u_mallory")
    r = c.delete(f"/api/threads/{tid}/uploads/{up['id']}")
    assert r.status_code == 404, r.text


# --- legacy NULL-owner row policy ------------------------------------------


def test_null_owner_legacy_row_is_404_for_everyone(client):
    """A ``cc_thread_session`` row with ``user_id IS NULL`` (legacy/hand-
    seeded) is owned by nobody. Every authenticated user gets 404."""
    c, _ = client
    # Seed a legacy row directly.
    from app.gateway.deps import session_store

    store = session_store()
    import sqlite3

    conn = sqlite3.connect(store.db_path)
    try:
        conn.execute(
            "INSERT INTO cc_thread_session(thread_id, session_id, cwd, user_id) "
            "VALUES ('t_legacy', NULL, '/tmp/legacy', NULL)"
        )
        conn.commit()
    finally:
        conn.close()

    for uid in ("u_alice", "u_mallory"):
        _login_as(uid)
        r1 = c.post("/api/threads/t_legacy/messages", json={"content": "hi"})
        assert r1.status_code == 404, (uid, r1.text)
        r2 = c.post("/api/threads/t_legacy/cancel")
        assert r2.status_code == 404, (uid, r2.text)
        r3 = c.get("/api/threads/t_legacy/workspace/tree")
        assert r3.status_code == 404, (uid, r3.text)
        r4 = c.get("/api/threads/t_legacy/uploads")
        assert r4.status_code == 404, (uid, r4.text)
