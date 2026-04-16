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
    #
    # We deliberately construct :class:`Config` **without** passing
    # ``alembic.ini`` — the ini file's ``[loggers]`` section would cause
    # alembic's ``env.py`` to call ``logging.config.fileConfig``, which by
    # default disables all pre-existing loggers and breaks unrelated
    # ``caplog``-based tests downstream.
    import os

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


def test_inflight_released_when_compose_fails(migrated_data_dir, monkeypatch):
    """If compose raises before ``event_gen`` starts, ``_inflight`` must not leak.

    ``event_gen``'s ``finally`` only runs once the async generator is iterated,
    so a failure between the inflight-add and the ``EventSourceResponse`` return
    would otherwise wedge the thread at 409 until server restart.
    """
    # Seed a malformed stdio MCP row (no command) so compose_mcp_config raises ValueError.
    db = Db(get_engine(migrated_data_dir))
    db.insert_mcp(user_id="u_default", name="broken", transport="stdio")

    from app.gateway.harmony_app import app
    from app.gateway.routers import messages

    # raise_server_exceptions=False so the ValueError surfaces as a 500 instead
    # of bubbling out of client.post (which would mask the post-condition check).
    client = TestClient(app, raise_server_exceptions=False)

    tid = client.post("/api/threads", json={}).json()["id"]

    r1 = client.post(f"/api/threads/{tid}/messages", json={"content": "hi"})
    assert r1.status_code == 500  # FastAPI default when handler raises non-HTTPException

    # _inflight should be empty — second attempt must NOT 409 with 'thread_busy'
    assert tid not in messages._inflight, "inflight leaked after compose failure"


def test_send_message_passes_user_default_model_to_spawn(
    migrated_data_dir, monkeypatch
):
    """Task 3.6 wiring: user_prefs.default_model flows into SpawnConfig.model
    and through CCAdapter.build_cmd as ``--model <id>``.

    The M3 exit criterion ("UI change model → next spawn uses --model
    with new value") rides on this path. We assert both layers — the
    SpawnConfig handed to run() AND the argv build_cmd produces from it —
    so a regression in either gets caught without depending on the real
    claude binary.
    """
    db = Db(get_engine(migrated_data_dir))
    db.upsert_user_prefs("u_default", default_model="opus")

    captured = _install_fake_adapter(monkeypatch)

    from app.cc_adapter.adapter import CCAdapter
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
    assert cfg is not None
    assert cfg.model == "opus"

    cmd = CCAdapter().build_cmd(cfg)
    assert "--model" in cmd
    # --model's value is the token immediately following it in argv.
    assert cmd[cmd.index("--model") + 1] == "opus"


def test_send_message_no_model_flag_when_pref_unset(
    migrated_data_dir, monkeypatch
):
    """If user_prefs has no row (or default_model IS NULL), build_cmd
    must not emit ``--model`` — CC then uses its built-in default."""
    captured = _install_fake_adapter(monkeypatch)

    from app.cc_adapter.adapter import CCAdapter
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
    assert cfg is not None
    assert cfg.model is None
    cmd = CCAdapter().build_cmd(cfg)
    assert "--model" not in cmd


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
