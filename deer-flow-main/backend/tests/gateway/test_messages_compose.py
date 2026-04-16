"""Task 3.3 integration: send_message composes MCP config + skills before spawning.

These tests monkeypatch ``CCAdapter.run`` so they don't need the real ``claude``
CLI on PATH. The goal is to observe the ``SpawnConfig`` handed to the adapter
and confirm that (a) ``mcp_config_path`` points at a JSON file listing the
user's enabled MCP servers and (b) the per-thread ``.claude/skills`` directory
is populated with symlinks to each enabled skill.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.db import Db, get_engine


BACKEND_DIR = Path(__file__).resolve().parents[2]


def _run_migrations(data_dir: Path) -> None:
    # alembic/env.py reads HARMONY_DATA_DIR at import time and clobbers any
    # sqlalchemy.url we pass on Config — so we route the env var instead.
    import os

    prev = os.environ.get("HARMONY_DATA_DIR")
    os.environ["HARMONY_DATA_DIR"] = str(data_dir)
    try:
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        if prev is None:
            os.environ.pop("HARMONY_DATA_DIR", None)
        else:
            os.environ["HARMONY_DATA_DIR"] = prev


@pytest.fixture(autouse=True)
def _reset_sse_starlette_exit_event():
    """sse_starlette caches a module-level asyncio.Event at first use; each
    TestClient instance runs on a fresh event loop, so we clear the cached
    event between tests to avoid ``bound to a different event loop`` errors.
    """
    yield
    try:
        from sse_starlette.sse import AppStatus

        AppStatus.should_exit_event = None
        AppStatus.should_exit = False
    except Exception:
        pass


@pytest.fixture
def migrated_data_dir(tmp_path, monkeypatch):
    """Point HARMONY_DATA_DIR at an isolated tmp dir with migrations applied."""
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    _run_migrations(tmp_path)
    return tmp_path


class _CapturedCfg:
    """Holder for the SpawnConfig the handler hands to CCAdapter.run."""

    value = None


def _install_fake_adapter(monkeypatch) -> _CapturedCfg:
    """Replace CCAdapter.run with a stub that captures cfg and yields a minimal stream."""
    captured = _CapturedCfg()

    async def fake_run(self, cfg):
        captured.value = cfg
        yield {"type": "system", "subtype": "init", "session_id": "s_fake"}
        yield {"type": "result", "duration_ms": 1}

    monkeypatch.setattr("app.cc_adapter.adapter.CCAdapter.run", fake_run)
    return captured


def test_send_message_composes_mcp_and_skills(migrated_data_dir, monkeypatch):
    # Seed: one enabled MCP server and one enabled skill for u_default.
    db = Db(get_engine(migrated_data_dir))
    db.insert_mcp(
        user_id="u_default",
        name="test_fs",
        transport="stdio",
        command="echo",
        args=["hello"],
    )
    skill_src = migrated_data_dir / "skill_src" / "testskill"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("---\nname: testskill\n---\nbody")
    db.insert_skill(
        user_id="u_default",
        name="testskill",
        source="upload",
        path=str(skill_src),
    )

    captured = _install_fake_adapter(monkeypatch)

    # Import AFTER monkeypatching env so _data_dir() resolves correctly.
    from app.gateway.harmony_app import app

    client = TestClient(app)

    r = client.post("/api/threads", json={})
    assert r.status_code == 200
    tid = r.json()["id"]

    with client.stream(
        "POST",
        f"/api/threads/{tid}/messages",
        json={"content": "hi"},
    ) as resp:
        assert resp.status_code == 200
        # Drain the stream so the handler fully runs.
        for _ in resp.iter_text():
            pass

    cfg = captured.value
    assert cfg is not None, "CCAdapter.run was not invoked"

    # --- MCP config composed and pointed at by SpawnConfig ---
    assert cfg.mcp_config_path is not None
    mcp_path = Path(cfg.mcp_config_path)
    assert mcp_path.exists()
    mcp_data = json.loads(mcp_path.read_text())
    assert "test_fs" in mcp_data["mcpServers"]
    assert mcp_data["mcpServers"]["test_fs"]["command"] == "echo"
    assert mcp_data["mcpServers"]["test_fs"]["args"] == ["hello"]
    # File lives under <data>/tmp/ and encodes the thread id in its name.
    assert mcp_path.parent == migrated_data_dir / "tmp"
    assert tid in mcp_path.name

    # --- Skills dir populated with symlink into DB-recorded source ---
    thread_root = migrated_data_dir / "threads" / tid / "user-data"
    skill_link = thread_root / ".claude" / "skills" / "testskill"
    assert skill_link.is_symlink()
    assert (skill_link / "SKILL.md").exists()
    assert (skill_link / "SKILL.md").read_text().startswith("---")

    # --- uploads added to cfg.add_dirs so CC can read attachments ---
    assert str(thread_root / "uploads") in cfg.add_dirs


def test_send_message_skips_disabled_rows(migrated_data_dir, monkeypatch):
    """Disabled MCP/skills must not leak into the composed config."""
    db = Db(get_engine(migrated_data_dir))
    db.insert_mcp(
        user_id="u_default",
        name="enabled_mcp",
        transport="stdio",
        command="true",
    )
    db.insert_mcp(
        user_id="u_default",
        name="disabled_mcp",
        transport="stdio",
        command="false",
        enabled=False,
    )
    enabled_skill = migrated_data_dir / "skills_src" / "enabled"
    enabled_skill.mkdir(parents=True)
    (enabled_skill / "SKILL.md").write_text("---\nname: enabled\n---")
    disabled_skill = migrated_data_dir / "skills_src" / "disabled"
    disabled_skill.mkdir(parents=True)
    (disabled_skill / "SKILL.md").write_text("---\nname: disabled\n---")
    db.insert_skill(
        user_id="u_default", name="enabled", source="upload", path=str(enabled_skill)
    )
    db.insert_skill(
        user_id="u_default",
        name="disabled",
        source="upload",
        path=str(disabled_skill),
        enabled=False,
    )

    captured = _install_fake_adapter(monkeypatch)

    from app.gateway.harmony_app import app

    client = TestClient(app)
    tid = client.post("/api/threads", json={}).json()["id"]

    with client.stream(
        "POST", f"/api/threads/{tid}/messages", json={"content": "hi"}
    ) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_text():
            pass

    cfg = captured.value
    mcp_data = json.loads(Path(cfg.mcp_config_path).read_text())
    assert "enabled_mcp" in mcp_data["mcpServers"]
    assert "disabled_mcp" not in mcp_data["mcpServers"]

    skills_dir = migrated_data_dir / "threads" / tid / "user-data" / ".claude" / "skills"
    assert (skills_dir / "enabled").is_symlink()
    assert not (skills_dir / "disabled").exists()
