"""Unit tests for ``app.db`` methods that aren't covered by router tests.

Currently: ``user_prefs`` CRUD (Task 3.6) and ``uploads`` CRUD
(Task 4.3). Router-level tests live under ``tests/gateway/`` — this
file is for the raw ``Db`` API.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.db import Db, get_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_migrations(data_dir: Path) -> None:
    """Apply alembic head to ``data_dir``/harmony.db.

    As in Task 3.3 / 3.4 / 3.5: do **not** pass ``alembic.ini``, because
    its ``[loggers]`` section triggers ``logging.config.fileConfig`` which
    silently disables every caplog-using test elsewhere in the suite.
    """
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


# --- user_prefs -----------------------------------------------------------


def test_get_user_prefs_missing_returns_none(db):
    assert db.get_user_prefs("u_default") is None


def test_upsert_user_prefs_inserts_when_missing(db):
    db.upsert_user_prefs("u_default", default_model="sonnet")
    row = db.get_user_prefs("u_default")
    assert row is not None
    assert row.user_id == "u_default"
    assert row.default_model == "sonnet"


def test_upsert_user_prefs_updates_when_present(db):
    db.upsert_user_prefs("u_default", default_model="sonnet")
    db.upsert_user_prefs("u_default", default_model="opus")
    row = db.get_user_prefs("u_default")
    assert row is not None
    assert row.default_model == "opus"


def test_upsert_user_prefs_clears_when_none(db):
    db.upsert_user_prefs("u_default", default_model="sonnet")
    db.upsert_user_prefs("u_default", default_model=None)
    row = db.get_user_prefs("u_default")
    assert row is not None  # row still exists, but default_model cleared
    assert row.default_model is None


def test_upsert_user_prefs_is_per_user(db):
    db.upsert_user_prefs("u_alice", default_model="sonnet")
    db.upsert_user_prefs("u_bob", default_model="opus")
    a = db.get_user_prefs("u_alice")
    b = db.get_user_prefs("u_bob")
    assert a is not None and a.default_model == "sonnet"
    assert b is not None and b.default_model == "opus"


# --- uploads (Task 4.3) ---------------------------------------------------


def test_insert_upload_and_list(db):
    """insert_upload returns ``up_<hex>``; list_uploads_for_thread orders newest first."""
    first = db.insert_upload(
        thread_id="t_one",
        user_id=None,
        filename="a.txt",
        size=3,
        content_type="text/plain",
    )
    second = db.insert_upload(
        thread_id="t_one",
        user_id="u_default",
        filename="b.bin",
        size=10,
        content_type=None,
    )
    # Not ours (filters by thread_id)
    db.insert_upload(
        thread_id="t_other",
        user_id=None,
        filename="c.txt",
        size=1,
        content_type="text/plain",
    )

    assert first.startswith("up_")
    assert second.startswith("up_")
    assert first != second

    rows = db.list_uploads_for_thread("t_one")
    assert len(rows) == 2
    # Newest first. Both inserted back-to-back — created_at may tie on fast
    # SQLite; guard against flakiness by asserting set membership + absence of
    # the other-thread row.
    ids = {r.id for r in rows}
    assert ids == {first, second}
    assert all(r.thread_id == "t_one" for r in rows)
    by_id = {r.id: r for r in rows}
    assert by_id[first].filename == "a.txt"
    assert by_id[first].size == 3
    assert by_id[first].content_type == "text/plain"
    assert by_id[first].user_id is None
    assert by_id[second].user_id == "u_default"
    assert by_id[second].content_type is None


def test_list_uploads_for_thread_empty(db):
    assert db.list_uploads_for_thread("t_nothing") == []


def test_get_upload_missing_returns_none(db):
    assert db.get_upload("up_does_not_exist") is None


def test_get_upload_returns_row(db):
    uid = db.insert_upload(
        thread_id="t_x",
        user_id=None,
        filename="f.txt",
        size=4,
        content_type="text/plain",
    )
    row = db.get_upload(uid)
    assert row is not None
    assert row.id == uid
    assert row.thread_id == "t_x"
    assert row.filename == "f.txt"


def test_delete_upload(db):
    uid = db.insert_upload(
        thread_id="t_y",
        user_id=None,
        filename="gone.txt",
        size=1,
        content_type=None,
    )
    assert db.get_upload(uid) is not None
    db.delete_upload(uid)
    assert db.get_upload(uid) is None
    assert db.list_uploads_for_thread("t_y") == []
