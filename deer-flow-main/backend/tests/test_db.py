"""Unit tests for ``app.db`` methods that aren't covered by router tests.

Currently: ``user_prefs`` CRUD (Task 3.6). Router-level tests live under
``tests/gateway/`` — this file is for the raw ``Db`` API.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

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
    db.upsert_user_prefs("u_default", default_model="claude-sonnet-4-5")
    row = db.get_user_prefs("u_default")
    assert row is not None
    assert row.user_id == "u_default"
    assert row.default_model == "claude-sonnet-4-5"


def test_upsert_user_prefs_updates_when_present(db):
    db.upsert_user_prefs("u_default", default_model="claude-sonnet-4-5")
    db.upsert_user_prefs("u_default", default_model="claude-opus-4-5")
    row = db.get_user_prefs("u_default")
    assert row is not None
    assert row.default_model == "claude-opus-4-5"


def test_upsert_user_prefs_clears_when_none(db):
    db.upsert_user_prefs("u_default", default_model="claude-sonnet-4-5")
    db.upsert_user_prefs("u_default", default_model=None)
    row = db.get_user_prefs("u_default")
    assert row is not None  # row still exists, but default_model cleared
    assert row.default_model is None


def test_upsert_user_prefs_is_per_user(db):
    db.upsert_user_prefs("u_alice", default_model="claude-sonnet-4-5")
    db.upsert_user_prefs("u_bob", default_model="claude-opus-4-5")
    a = db.get_user_prefs("u_alice")
    b = db.get_user_prefs("u_bob")
    assert a is not None and a.default_model == "claude-sonnet-4-5"
    assert b is not None and b.default_model == "claude-opus-4-5"
