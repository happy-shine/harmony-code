"""Pure event builders for the ``cc.spawn`` and ``cc.result`` audit lines.

These functions are deliberately IO-free — they return a plain dict so
unit tests can assert shape without capturing logs, and so the emitter
in :mod:`app.audit` stays a one-liner.

Event shapes are documented in
``docs/plans/2026-04-15-harmony-code-design.md`` (Section 5, "审计")
and the M5 Task 5.4 brief.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal


def _now_iso() -> str:
    """UTC timestamp in ISO 8601 — ``datetime.fromisoformat`` round-trips cleanly."""
    return datetime.now(UTC).isoformat()


def _hash_argv(argv_without_prompt: list[str]) -> str:
    """Return ``"sha256:<hex>"`` over the JSON-encoded argv list.

    The spec requires that the prompt text NOT be hashed (store a hash of
    the invocation flags, not the user's words) — callers must therefore
    drop the trailing prompt positional before passing argv here.
    ``CCAdapter.build_cmd`` appends the prompt as the final element after
    ``"--"``; dropping ``[-1]`` is sufficient.
    """
    payload = json.dumps(argv_without_prompt, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"sha256:{digest}"


def spawn_event(
    *,
    user_id: str,
    thread_id: str,
    session_id: str | None,
    model: str | None,
    argv_without_prompt: list[str],
    prompt_len: int,
    mcp_servers_enabled: list[str],
    skills_enabled: list[str],
) -> dict:
    """Build a ``cc.spawn`` audit event dict.

    Fires once per CC process spawn, before the subprocess actually
    execs — so ``session_id`` here is whatever was passed to ``--resume``
    (``None`` on the first spawn of a thread).
    """
    return {
        "event": "cc.spawn",
        "ts": _now_iso(),
        "user_id": user_id,
        "thread_id": thread_id,
        "session_id": session_id,
        "model": model,
        "cmd_args_hash": _hash_argv(argv_without_prompt),
        "prompt_len": prompt_len,
        "mcp_servers_enabled": list(mcp_servers_enabled),
        "skills_enabled": list(skills_enabled),
    }


def result_event(
    *,
    user_id: str,
    thread_id: str,
    session_id: str | None,
    duration_ms: int,
    exit_code: int,
    cost_usd: float | None,
    disposition: Literal["natural", "disconnected", "error"],
) -> dict:
    """Build a ``cc.result`` audit event dict.

    Fires once per CC run, when the event generator unwinds — naturally,
    via client disconnect, or via exception. ``cost_usd`` is ``None``
    whenever CC didn't emit a terminal ``result`` frame (e.g. tests that
    mock the stream, or runs killed before CC could summarize).
    """
    return {
        "event": "cc.result",
        "ts": _now_iso(),
        "user_id": user_id,
        "thread_id": thread_id,
        "session_id": session_id,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "cost_usd": cost_usd,
        "disposition": disposition,
    }
