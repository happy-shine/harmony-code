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


# --- 3. Populated file, stdio server --------------------------------------


def test_stdio_server_inserts_global_row_with_json_fields(db, tmp_path):
    """A single stdio server imports as a global row with args+env JSON-serialized."""
    from scripts.migrate_extensions_config import main

    path = _write_config(
        tmp_path,
        {
            "mcpServers": {
                "fs": {
                    "enabled": True,
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    "env": {"LOG_LEVEL": "debug"},
                }
            },
            "skills": {},
        },
    )
    rc = main(["--config", str(path)])

    assert rc == 0
    rows = db.list_mcp_for_user(user_id="u_default")
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id is None  # global
    assert row.name == "fs"
    assert row.transport == "stdio"
    assert row.command == "npx"
    assert row.enabled is True
    # args + env round-trip through JSON columns.
    assert json.loads(row.args_json) == [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/tmp",
    ]
    assert json.loads(row.env_json) == {"LOG_LEVEL": "debug"}
    # Unused stdio fields stay NULL.
    assert row.url is None
    assert row.headers_json is None


# --- 4. Populated file, http server with headers --------------------------


def test_http_server_with_headers_inserts_url_and_headers_json(db, tmp_path):
    """An http server imports with transport='http', url, and headers_json set."""
    from scripts.migrate_extensions_config import main

    path = _write_config(
        tmp_path,
        {
            "mcpServers": {
                "remote": {
                    "enabled": False,  # disabled preserved
                    "type": "http",
                    "url": "https://mcp.example.com/v1",
                    "headers": {
                        "Authorization": "Bearer abc123",
                        "X-Client": "harmony",
                    },
                }
            },
            "skills": {},
        },
    )
    rc = main(["--config", str(path)])

    assert rc == 0
    rows = db.list_mcp_for_user(user_id="u_default")
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "remote"
    assert row.transport == "http"
    assert row.url == "https://mcp.example.com/v1"
    assert row.enabled is False  # preserved from legacy config
    assert json.loads(row.headers_json) == {
        "Authorization": "Bearer abc123",
        "X-Client": "harmony",
    }
    assert row.command is None
    assert row.args_json is None


# --- 5. Idempotency -------------------------------------------------------


def test_migration_is_idempotent_on_rerun(db, tmp_path, caplog):
    """Running the migration twice yields the same row count — no duplicates,
    no IntegrityError escaping out of the script."""
    from scripts.migrate_extensions_config import main

    path = _write_config(
        tmp_path,
        {
            "mcpServers": {
                "fs": {"type": "stdio", "command": "true", "args": ["--once"]},
                "remote": {"type": "http", "url": "https://x/y"},
            },
            "skills": {},
        },
    )

    rc1 = main(["--config", str(path)])
    assert rc1 == 0
    count_after_first = _count_mcp(db)
    assert count_after_first == 2

    # Second run must not raise and must not grow the table.
    with caplog.at_level(logging.INFO):
        rc2 = main(["--config", str(path)])
    assert rc2 == 0
    assert _count_mcp(db) == count_after_first

    # And a "skipping" log line should have been emitted for each name.
    skip_msgs = [r for r in caplog.records if "skip" in r.message.lower()]
    assert len(skip_msgs) >= 2
