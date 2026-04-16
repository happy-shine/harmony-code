"""Shared fixtures for ``tests/cc_adapter``."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.db import Db, get_engine

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture
def db_with_rows(tmp_path, monkeypatch):
    """Fresh ``harmony.db`` in ``tmp_path`` migrated to head, wrapped in :class:`Db`.

    We deliberately construct :class:`Config` **without** passing
    ``alembic.ini`` — the ini file's ``[loggers]`` section would cause
    alembic's ``env.py`` to call ``logging.config.fileConfig``, which by
    default disables all pre-existing loggers and breaks unrelated
    ``caplog``-based tests downstream.
    """
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")
    return Db(get_engine(tmp_path))
