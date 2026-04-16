"""Unit tests for the pure audit event builders.

These are pure builders with no IO — they should be easy to exercise
without touching the logging layer. The emitter lives in ``app.audit``
and is exercised separately in the gateway integration suite.
"""

from __future__ import annotations

from datetime import datetime

from app.audit_events import result_event, spawn_event


def test_spawn_event_shape():
    ev = spawn_event(
        user_id="u_default",
        thread_id="t_abc123",
        session_id=None,
        model="sonnet",
        argv_without_prompt=["claude", "-p", "--output-format", "stream-json"],
        prompt_len=234,
        mcp_servers_enabled=["everything", "fs"],
        skills_enabled=["web-search", "notes"],
    )

    # All expected top-level fields
    expected_keys = {
        "event",
        "ts",
        "user_id",
        "thread_id",
        "session_id",
        "model",
        "cmd_args_hash",
        "prompt_len",
        "mcp_servers_enabled",
        "skills_enabled",
    }
    assert set(ev.keys()) == expected_keys

    assert ev["event"] == "cc.spawn"
    assert ev["user_id"] == "u_default"
    assert ev["thread_id"] == "t_abc123"
    assert ev["session_id"] is None
    assert ev["model"] == "sonnet"
    assert ev["prompt_len"] == 234

    # ts parseable as ISO 8601
    parsed = datetime.fromisoformat(ev["ts"])
    assert parsed is not None

    # hash shape
    assert isinstance(ev["cmd_args_hash"], str)
    assert ev["cmd_args_hash"].startswith("sha256:")
    # 64 hex chars after prefix
    assert len(ev["cmd_args_hash"]) == len("sha256:") + 64

    # lists of strings
    assert ev["mcp_servers_enabled"] == ["everything", "fs"]
    assert ev["skills_enabled"] == ["web-search", "notes"]
    assert all(isinstance(s, str) for s in ev["mcp_servers_enabled"])
    assert all(isinstance(s, str) for s in ev["skills_enabled"])


def test_spawn_event_excludes_prompt_text_from_hash():
    """The hash is over ``argv_without_prompt`` only — the prompt text
    must never affect the hash because the spec says "不存 prompt 原文, 存 hash".
    """
    base_argv = ["claude", "-p", "--output-format", "stream-json", "--"]

    ev_a = spawn_event(
        user_id="u",
        thread_id="t",
        session_id=None,
        model=None,
        argv_without_prompt=base_argv,
        prompt_len=5,
        mcp_servers_enabled=[],
        skills_enabled=[],
    )
    ev_b = spawn_event(
        user_id="u",
        thread_id="t",
        session_id=None,
        model=None,
        argv_without_prompt=base_argv,
        prompt_len=9999,  # different prompt_len — hash should NOT depend on this
        mcp_servers_enabled=[],
        skills_enabled=[],
    )
    assert ev_a["cmd_args_hash"] == ev_b["cmd_args_hash"], (
        "hash must be stable across different prompts with identical argv"
    )

    # Now change the argv slightly — hash must differ.
    ev_c = spawn_event(
        user_id="u",
        thread_id="t",
        session_id=None,
        model=None,
        argv_without_prompt=[*base_argv, "--extra-flag"],
        prompt_len=5,
        mcp_servers_enabled=[],
        skills_enabled=[],
    )
    assert ev_a["cmd_args_hash"] != ev_c["cmd_args_hash"]


def test_result_event_shape():
    ev = result_event(
        user_id="u_default",
        thread_id="t_abc123",
        session_id="sess_xyz",
        duration_ms=15212,
        exit_code=0,
        cost_usd=0.0123,
        disposition="natural",
    )

    expected_keys = {
        "event",
        "ts",
        "user_id",
        "thread_id",
        "session_id",
        "duration_ms",
        "exit_code",
        "cost_usd",
        "disposition",
    }
    assert set(ev.keys()) == expected_keys
    assert ev["event"] == "cc.result"
    assert ev["disposition"] in {"natural", "disconnected", "error"}
    assert ev["duration_ms"] == 15212
    assert ev["exit_code"] == 0
    assert ev["cost_usd"] == 0.0123
    assert ev["session_id"] == "sess_xyz"

    # ts parseable
    datetime.fromisoformat(ev["ts"])


def test_result_event_allows_null_cost_and_session():
    ev = result_event(
        user_id="u",
        thread_id="t",
        session_id=None,
        duration_ms=0,
        exit_code=-1,
        cost_usd=None,
        disposition="error",
    )
    assert ev["session_id"] is None
    assert ev["cost_usd"] is None
    assert ev["exit_code"] == -1
    assert ev["disposition"] == "error"
