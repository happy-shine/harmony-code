"""Integration: /messages emits cc.spawn + cc.result audit lines per request.

Mocks ``CCAdapter.run`` so we don't need the real ``claude`` binary and
so we can drive the stream to exercise all three ``disposition`` paths.
Captures log lines via ``caplog.set_level(..., logger="harmony.audit")``
and parses them as JSON.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.db import Db, get_engine

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _run_migrations(data_dir: Path) -> None:
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
    yield
    try:
        from sse_starlette.sse import AppStatus

        AppStatus.should_exit_event = None
        AppStatus.should_exit = False
    except Exception:
        pass


@pytest.fixture
def migrated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    _run_migrations(tmp_path)
    return tmp_path


def _install_fake_adapter(monkeypatch, *, events: list[dict]) -> None:
    """Replace CCAdapter.run with a stub that yields the given events."""

    async def fake_run(self, cfg):
        for ev in events:
            yield ev

    monkeypatch.setattr("app.cc_adapter.adapter.CCAdapter.run", fake_run)


def _parse_audit_lines(caplog) -> list[dict]:
    """Pull every ``harmony.audit`` record from caplog and JSON-decode it."""
    out: list[dict] = []
    for record in caplog.records:
        if record.name != "harmony.audit":
            continue
        out.append(json.loads(record.getMessage()))
    return out


def test_audit_emits_spawn_and_result_on_natural_exit(migrated_data_dir, monkeypatch, caplog):
    """Happy path: stream ends naturally — we log spawn then result
    with disposition=natural, cost_usd from the terminal result frame,
    and session_id captured from system.init."""
    # Seed one enabled MCP + one enabled skill so the spawn event can
    # report them by name.
    db = Db(get_engine(migrated_data_dir))
    db.insert_mcp(user_id="u_default", name="my_mcp", transport="stdio", command="echo")
    skill_src = migrated_data_dir / "skill_src" / "my_skill"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("---\nname: my_skill\n---")
    db.insert_skill(user_id="u_default", name="my_skill", source="upload", path=str(skill_src))

    _install_fake_adapter(
        monkeypatch,
        events=[
            {"type": "system", "subtype": "init", "session_id": "s_fake"},
            {"type": "result", "total_cost_usd": 0.042, "duration_ms": 1},
        ],
    )

    caplog.set_level(logging.INFO, logger="harmony.audit")

    from app.gateway.harmony_app import app

    client = TestClient(app)
    tid = client.post("/api/threads", json={}).json()["id"]

    with client.stream("POST", f"/api/threads/{tid}/messages", json={"content": "hi there"}) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_text():
            pass

    lines = _parse_audit_lines(caplog)
    assert len(lines) == 2, f"expected exactly 2 audit lines, got {lines!r}"

    spawn, result = lines
    # Spawn shape + content
    assert spawn["event"] == "cc.spawn"
    assert spawn["user_id"] == "u_default"
    assert spawn["thread_id"] == tid
    assert spawn["session_id"] is None  # first spawn, no resume
    assert spawn["prompt_len"] == len("hi there")
    assert "my_mcp" in spawn["mcp_servers_enabled"]
    assert "my_skill" in spawn["skills_enabled"]
    assert spawn["cmd_args_hash"].startswith("sha256:")

    # Result shape + content
    assert result["event"] == "cc.result"
    assert result["user_id"] == "u_default"
    assert result["thread_id"] == tid
    assert result["disposition"] == "natural"
    assert result["exit_code"] == 0
    assert result["cost_usd"] == 0.042
    assert result["session_id"] == "s_fake"  # captured from system.init
    assert isinstance(result["duration_ms"], int)
    assert result["duration_ms"] >= 0


def test_audit_result_has_null_cost_when_no_result_frame(migrated_data_dir, monkeypatch, caplog):
    """If CC never emits a terminal ``result`` frame, cost_usd must be null
    — not a crash, not a 0.0. This is the common case in tests that mock
    the stream."""
    _install_fake_adapter(
        monkeypatch,
        events=[
            {"type": "system", "subtype": "init", "session_id": "s_nocost"},
        ],
    )

    caplog.set_level(logging.INFO, logger="harmony.audit")

    from app.gateway.harmony_app import app

    client = TestClient(app)
    tid = client.post("/api/threads", json={}).json()["id"]

    with client.stream("POST", f"/api/threads/{tid}/messages", json={"content": "x"}) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_text():
            pass

    lines = _parse_audit_lines(caplog)
    assert len(lines) == 2
    _, result = lines
    assert result["event"] == "cc.result"
    assert result["cost_usd"] is None
    assert result["disposition"] == "natural"
    assert result["session_id"] == "s_nocost"


def test_audit_result_captures_exit_code_from_adapter_error(migrated_data_dir, monkeypatch, caplog):
    """An ``_adapter.error`` frame with ``cc_nonzero_exit`` carries the real
    CC exit code — the result event's exit_code should reflect it."""
    _install_fake_adapter(
        monkeypatch,
        events=[
            {"type": "system", "subtype": "init", "session_id": "s_err"},
            {
                "type": "_adapter",
                "subtype": "error",
                "code": "cc_nonzero_exit",
                "exit_code": 7,
                "stderr_tail": "boom",
            },
        ],
    )

    caplog.set_level(logging.INFO, logger="harmony.audit")

    from app.gateway.harmony_app import app

    client = TestClient(app)
    tid = client.post("/api/threads", json={}).json()["id"]

    with client.stream("POST", f"/api/threads/{tid}/messages", json={"content": "x"}) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_text():
            pass

    lines = _parse_audit_lines(caplog)
    assert len(lines) == 2
    _, result = lines
    # Natural EOF (generator exhausted) but CC reported nonzero exit.
    assert result["disposition"] == "natural"
    assert result["exit_code"] == 7


@pytest.mark.asyncio
async def test_audit_result_disposition_cancelled(migrated_data_dir, monkeypatch, caplog):
    """``POST /cancel`` while a runner is active → audit ``cc.result`` with
    ``disposition="cancelled"`` and ``exit_code=-1``. Replaces the old
    "disconnected" path: SSE disconnects no longer terminate the runner,
    so an explicit cancel is now the only way to stop a run early.

    The test drives the runner directly (no httpx round-trip) so the
    fake adapter's hang-until-cancel reads cleanly under the same
    pytest-asyncio event loop the registry binds its lock to.
    """
    import asyncio

    from app.audit import emit as audit_emit
    from app.audit_events import result_event, spawn_event
    from app.cc_adapter.adapter import CCAdapter

    # Fresh registry bound to this loop so module-level state from prior
    # tests can't bleed in.
    from app.cc_adapter.runner import RunnerOutcome, RunnerRegistry
    from app.cc_adapter.types import SpawnConfig
    from app.gateway.routers import messages as messages_mod

    messages_mod._runner_registry = RunnerRegistry()

    async def fake_run(self, cfg):  # noqa: ARG001
        yield {"type": "system", "subtype": "init", "session_id": "s_cancel"}
        # Hang until task cancel unwinds us. The runner's CancelledError
        # path will publish the synthetic ``cancelled`` frame and fire
        # the on_terminate hook with disposition="cancelled".
        await asyncio.sleep(3600)

    monkeypatch.setattr("app.cc_adapter.adapter.CCAdapter.run", fake_run)
    caplog.set_level(logging.INFO, logger="harmony.audit")

    cfg = SpawnConfig(cwd=str(migrated_data_dir), user_prompt="x")

    # Mirror what the router does: emit cc.spawn before start, then
    # register an on_terminate hook that emits cc.result.
    audit_emit(
        spawn_event(
            user_id="u_default",
            thread_id="t_cancel",
            session_id=None,
            model=None,
            argv_without_prompt=[],
            prompt_len=1,
            mcp_servers_enabled=[],
            skills_enabled=[],
        )
    )

    async def _on_terminate(outcome: RunnerOutcome) -> None:
        audit_emit(
            result_event(
                user_id="u_default",
                thread_id="t_cancel",
                session_id=outcome.session_id,
                duration_ms=outcome.duration_ms,
                exit_code=outcome.exit_code,
                cost_usd=outcome.cost_usd,
                disposition=outcome.disposition,
            )
        )

    runner = await messages_mod._runner_registry.start(
        thread_id="t_cancel",
        cfg=cfg,
        adapter=CCAdapter(),
        on_terminate=_on_terminate,
    )
    # Give the fake adapter time to yield the init frame and reach the sleep.
    await asyncio.sleep(0.05)
    canceled = await messages_mod._runner_registry.cancel("t_cancel")
    assert canceled is True
    assert runner.disposition == "cancelled"

    lines = _parse_audit_lines(caplog)
    assert len(lines) == 2, f"expected exactly 2 audit lines, got {lines!r}"
    _, result = lines
    assert result["event"] == "cc.result"
    assert result["disposition"] == "cancelled"
    assert result["exit_code"] == -1
    assert result["session_id"] == "s_cancel"


def test_audit_result_disposition_error(migrated_data_dir, monkeypatch, caplog):
    """Adapter raises mid-stream: ``event_gen``'s ``finally`` still fires,
    disposition stays ``"error"`` (never flipped to "natural" or
    "disconnected"), and exit_code is normalized to -1."""

    async def fake_run_that_raises(self, cfg):
        yield {"type": "system", "subtype": "init", "session_id": "s_boom"}
        raise RuntimeError("boom")

    monkeypatch.setattr("app.cc_adapter.adapter.CCAdapter.run", fake_run_that_raises)

    caplog.set_level(logging.INFO, logger="harmony.audit")

    from app.gateway.harmony_app import app

    client = TestClient(app)
    tid = client.post("/api/threads", json={}).json()["id"]

    # The exception propagates out of event_gen. sse-starlette / TestClient
    # may surface it as an exception on the client side OR just terminate
    # the stream — the observable signal we care about is the audit line
    # emitted from event_gen's ``finally`` block, which runs regardless.
    try:
        with client.stream("POST", f"/api/threads/{tid}/messages", json={"content": "x"}) as resp:
            # status may or may not reach 200 depending on how soon the
            # exception surfaces; drain whatever we can and move on.
            for _ in resp.iter_text():
                pass
    except Exception:
        pass

    lines = _parse_audit_lines(caplog)
    # spawn fires before event_gen starts, result fires from the finally.
    assert len(lines) == 2, f"expected exactly 2 audit lines, got {lines!r}"
    _, result = lines
    assert result["event"] == "cc.result"
    assert result["disposition"] == "error"
    assert result["exit_code"] == -1
