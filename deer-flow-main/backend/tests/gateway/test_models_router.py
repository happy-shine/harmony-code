"""Task 3.6: ``/api/models`` router tests.

Covers the catalog list endpoint, per-user preference GET/PUT (upsert),
and input validation on unknown model IDs. Migration helper follows the
same pattern as ``test_mcp_router.py`` and ``test_skills_router.py`` —
construct :class:`alembic.config.Config` **without** the ini path to
avoid ``logging.config.fileConfig`` nuking caplog in other tests.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.db import Db, get_engine
from app.model_catalog import MODELS


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


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    _run_migrations(tmp_path)
    from app.gateway.harmony_app import app

    return TestClient(app), tmp_path


# --- GET /api/models ------------------------------------------------------


def test_list_models_returns_catalog(client):
    c, _ = client
    r = c.get("/api/models")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == len(MODELS)
    ids = {row["id"] for row in rows}
    assert ids == {m.id for m in MODELS}
    # Shape check: every row carries id + (optional) name + description fields.
    for row in rows:
        assert "id" in row
        # name/description may be None, but the keys must be present.
        assert "name" in row
        assert "description" in row


# --- GET /api/models/me ---------------------------------------------------


def test_get_my_model_never_set_returns_null(client):
    c, _ = client
    r = c.get("/api/models/me")
    assert r.status_code == 200, r.text
    assert r.json() == {"default_model": None}


# --- PUT /api/models/me ---------------------------------------------------


def test_put_my_model_persists_and_round_trips(client):
    c, _ = client
    r = c.put("/api/models/me", json={"default_model": "claude-sonnet-4-5"})
    assert r.status_code == 200, r.text
    assert r.json() == {"default_model": "claude-sonnet-4-5"}
    # Independent follow-up GET must see the persisted value.
    r2 = c.get("/api/models/me")
    assert r2.status_code == 200
    assert r2.json() == {"default_model": "claude-sonnet-4-5"}


def test_put_my_model_null_clears_previously_set_pref(client):
    c, _ = client
    c.put("/api/models/me", json={"default_model": "claude-sonnet-4-5"})
    r = c.put("/api/models/me", json={"default_model": None})
    assert r.status_code == 200, r.text
    assert r.json() == {"default_model": None}
    assert c.get("/api/models/me").json() == {"default_model": None}


def test_put_my_model_rejects_unknown_id(client):
    c, _ = client
    r = c.put("/api/models/me", json={"default_model": "not-a-real-model"})
    assert r.status_code == 422, r.text
    # Pref must remain unset after rejection.
    assert c.get("/api/models/me").json() == {"default_model": None}


def test_put_my_model_overwrites_existing(client):
    c, _ = client
    c.put("/api/models/me", json={"default_model": "claude-sonnet-4-5"})
    r = c.put("/api/models/me", json={"default_model": "claude-opus-4-5"})
    assert r.status_code == 200, r.text
    assert r.json() == {"default_model": "claude-opus-4-5"}
    assert c.get("/api/models/me").json() == {"default_model": "claude-opus-4-5"}


# --- Cross-check: router reads the same DB the adapter wiring will ---------


def test_put_my_model_writes_row_visible_to_db_api(client):
    c, tmp = client
    c.put("/api/models/me", json={"default_model": "claude-haiku-4-5"})
    db = Db(get_engine(tmp))
    row = db.get_user_prefs("u_default")
    assert row is not None
    assert row.default_model == "claude-haiku-4-5"
