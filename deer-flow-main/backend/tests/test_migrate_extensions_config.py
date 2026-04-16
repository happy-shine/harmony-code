"""Tests for ``scripts.migrate_extensions_config``.

The migration script reads a legacy ``extensions_config.json`` and imports
MCP server rows into ``harmony.db`` as global rows (``user_id IS NULL``).
It is idempotent, dry-runnable, and exits 0 when the file is absent.

All fixtures follow the Task 3.3 / 3.4 / 3.5 pattern: construct
``alembic.config.Config`` WITHOUT the ini path, so alembic doesn't call
``logging.config.fileConfig`` and break caplog elsewhere in the suite.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

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
def db(tmp_path, monkeypatch):
    """Fresh harmony.db at ``tmp_path`` with alembic head applied."""
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    # Ensure no env-based override of legacy config path leaks in.
    monkeypatch.delenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", raising=False)
    _run_migrations(tmp_path)
    return Db(get_engine(tmp_path))


def _count_mcp(db: Db) -> int:
    with db.engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM mcp_servers")).scalar_one()


def _count_skills(db: Db) -> int:
    with db.engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM skills")).scalar_one()


def _write_config(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "extensions_config.json"
    p.write_text(json.dumps(payload))
    return p


# --- 1. Missing file ------------------------------------------------------


def test_missing_config_file_is_noop_and_exits_zero(db, tmp_path, caplog):
    """No file at any resolved location → log info, exit 0, no DB writes."""
    from scripts.migrate_extensions_config import main

    # Point resolver at a nonexistent path explicitly — avoids depending on
    # cwd-based default resolution finding something unexpected on the
    # developer's machine.
    missing = tmp_path / "does_not_exist.json"
    with caplog.at_level(logging.INFO):
        rc = main(["--config", str(missing)])

    assert rc == 0
    assert _count_mcp(db) == 0
    assert _count_skills(db) == 0
    assert any("no extensions config" in r.message.lower() for r in caplog.records)


# --- 2. Empty file --------------------------------------------------------


def test_empty_config_is_noop_and_logs_counts(db, tmp_path, caplog):
    """File present but no servers/skills → log "0 servers, 0 skills", no writes."""
    from scripts.migrate_extensions_config import main

    path = _write_config(tmp_path, {"mcpServers": {}, "skills": {}})
    with caplog.at_level(logging.INFO):
        rc = main(["--config", str(path)])

    assert rc == 0
    assert _count_mcp(db) == 0
    assert _count_skills(db) == 0
    # At least one log line must reference the counts (0 MCP, 0 skills).
    assert any(
        ("0" in r.message and ("mcp" in r.message.lower() or "server" in r.message.lower()))
        for r in caplog.records
    )
