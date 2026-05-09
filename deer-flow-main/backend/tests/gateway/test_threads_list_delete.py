"""Tests for ``GET /api/threads`` (list) and ``DELETE /api/threads/{tid}``.

These endpoints exist so the frontend chat list can render the user's
threads and so users can remove rows from the listing. Three things we
care about:

1. The list is scoped to the caller — a second user sees only their rows.
2. Delete is ownership-aware (404 for not-mine) and refuses 409 while a
   stream is in-flight.
3. Deleted rows disappear from the list.

We follow the pattern from ``test_auth_thread_isolation.py``: run
migrations, create a ``TestClient``, and swap auth overrides between
requests via ``_login_as``.
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


def test_list_threads_returns_only_callers_rows(client):
    c, _ = client
    _login_as("u_alice")
    a1 = c.post("/api/threads", json={}).json()["id"]
    a2 = c.post("/api/threads", json={}).json()["id"]
    _login_as("u_bob")
    b1 = c.post("/api/threads", json={}).json()["id"]

    # Alice sees her two rows only.
    _login_as("u_alice")
    r = c.get("/api/threads")
    assert r.status_code == 200, r.text
    ids = {t["id"] for t in r.json()["threads"]}
    assert ids == {a1, a2}

    # Bob sees only his.
    _login_as("u_bob")
    r = c.get("/api/threads")
    assert r.status_code == 200
    ids = {t["id"] for t in r.json()["threads"]}
    assert ids == {b1}


def test_derive_title_collapses_whitespace_and_truncates():
    from app.gateway.routers.messages import _derive_title

    # Short prompt round-trips verbatim (with whitespace collapsed).
    assert _derive_title("Hello   world") == "Hello world"
    # Empty / whitespace-only falls back to a nonempty placeholder.
    assert _derive_title("") == "New chat"
    assert _derive_title("   \n  ") == "New chat"
    # Long prompt truncates on a word boundary with ellipsis.
    long_prompt = "This is a long prompt " * 10
    title = _derive_title(long_prompt, max_chars=30)
    assert title.endswith("…")
    assert len(title) <= 31  # 30 + single-char ellipsis
    # Unbreakable long token still truncates (no room for a word cut).
    assert _derive_title("x" * 200, max_chars=10) == "xxxxxxxxxx…"


def test_set_title_if_empty_is_first_write_wins(tmp_path):
    """The title must be captured once — subsequent sends don't rewrite it,
    so the sidebar stays anchored to the opening question."""
    from app.cc_adapter.session_store import SessionStore

    db_path = str(tmp_path / "s.db")
    s = SessionStore(db_path)
    s.ensure_schema()
    s.create("t_x", "/tmp", user_id="u_alice")

    s.set_title_if_empty("t_x", "First question")
    assert s.get("t_x").title == "First question"
    # Second call is a no-op.
    s.set_title_if_empty("t_x", "Second message")
    assert s.get("t_x").title == "First question"


def test_list_threads_empty_list_when_no_rows(client):
    c, _ = client
    _login_as("u_fresh")
    r = c.get("/api/threads")
    assert r.status_code == 200
    assert r.json() == {"threads": []}


def test_list_threads_shape_has_id_and_updated_at(client):
    c, _ = client
    _login_as("u_alice")
    tid = c.post("/api/threads", json={}).json()["id"]
    r = c.get("/api/threads")
    threads = r.json()["threads"]
    assert len(threads) == 1
    row = threads[0]
    assert row["id"] == tid
    # updated_at is ISO-8601 or null; in a freshly-created thread the
    # cwd exists so we expect a non-null timestamp.
    assert row["updated_at"] is not None
    assert "T" in row["updated_at"]
    assert row["has_session"] is False
    # title is null until the first user message lands and
    # ``set_title_if_empty`` populates it. The frontend falls back to
    # "New chat" for null-title rows.
    assert row["title"] is None


def test_delete_thread_removes_from_list(client):
    c, _ = client
    _login_as("u_alice")
    a1 = c.post("/api/threads", json={}).json()["id"]
    a2 = c.post("/api/threads", json={}).json()["id"]

    r = c.delete(f"/api/threads/{a1}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "id": a1}

    r = c.get("/api/threads")
    ids = {t["id"] for t in r.json()["threads"]}
    assert ids == {a2}


def test_delete_thread_other_user_404(client):
    c, _ = client
    _login_as("u_alice")
    tid = c.post("/api/threads", json={}).json()["id"]
    _login_as("u_mallory")
    r = c.delete(f"/api/threads/{tid}")
    assert r.status_code == 404, r.text


def test_delete_thread_unknown_tid_404(client):
    c, _ = client
    _login_as("u_alice")
    r = c.delete("/api/threads/t_does_not_exist")
    assert r.status_code == 404, r.text


def test_delete_thread_busy_409(client):
    """If the thread is in-flight, delete refuses with 409."""
    c, _ = client
    _login_as("u_alice")
    tid = c.post("/api/threads", json={}).json()["id"]

    # Simulate "an active runner exists" by stubbing
    # ``runner_registry().active`` rather than reaching into the
    # registry's internals. We can't actually spawn CC in unit tests,
    # and the router's busy-check goes through this exact predicate.
    from app.gateway.routers import messages as messages_mod

    real_active = messages_mod._runner_registry.active
    messages_mod._runner_registry.active = lambda t: t == tid  # type: ignore[method-assign]
    try:
        r = c.delete(f"/api/threads/{tid}")
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == "thread_busy"
    finally:
        messages_mod._runner_registry.active = real_active  # type: ignore[method-assign]

    # After the slot is released, delete works again.
    r = c.delete(f"/api/threads/{tid}")
    assert r.status_code == 200, r.text


def test_list_threads_newest_first(client):
    """Newest by ``updated_at`` (cwd mtime) sorts to index 0."""
    c, _ = client
    _login_as("u_alice")
    old_tid = c.post("/api/threads", json={}).json()["id"]
    # Force old thread's cwd mtime into the past so it sorts below the new one.
    from app.gateway.deps import session_store

    row = session_store().get(old_tid)
    assert row is not None
    past = 1_000_000  # Jan 1970-ish
    os.utime(row.cwd, (past, past))

    new_tid = c.post("/api/threads", json={}).json()["id"]
    r = c.get("/api/threads")
    threads = r.json()["threads"]
    assert [t["id"] for t in threads] == [new_tid, old_tid]
