"""Thread messages router: POST /api/threads/{tid}/messages streams CC output as SSE.

Requires ``alembic upgrade head`` to have run against ``$HARMONY_DATA_DIR``
before starting the server — :func:`send_message` composes an MCP config and
skills directory from ``harmony.db`` on every spawn and will fail if the
schema is missing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.audit import emit as audit_emit
from app.audit_events import result_event, spawn_event
from app.cc_adapter.adapter import CCAdapter
from app.cc_adapter.compose import compose_mcp_config, compose_skills_dir
from app.cc_adapter.runner import RunnerOutcome, RunnerRegistry
from app.cc_adapter.types import SpawnConfig
from app.gateway.deps import (
    current_user_id,
    get_db,
)
from app.gateway.deps import (
    data_dir as _data_dir,
)
from app.gateway.deps import (
    session_store as _store,
)
from app.gateway.deps import (
    thread_cwd as _thread_cwd,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class SendMessageBody(BaseModel):
    content: str
    attachments: list[str] = []


# Admission-control limits per design Section 2 concurrency table.
# Overridable via env vars for ops: ``HARMONY_MAX_PER_USER`` /
# ``HARMONY_MAX_SERVER``. Per-thread is always 1 — the design table pins
# it to 1, and ``--resume`` semantics assume strict serialization per
# thread. Tests monkeypatch the module attrs directly (the module-level
# constants are bound at import time, so env-var reloads mid-test do not
# take effect without that).
_MAX_PER_USER = int(os.environ.get("HARMONY_MAX_PER_USER", "3"))
_MAX_SERVER = int(os.environ.get("HARMONY_MAX_SERVER", "20"))

# Per-user / server-wide counters guarded by ``_inflight_lock``. The
# server total is wrapped in a one-element list to avoid ``global`` +
# reassignment ceremony at every mutation site. Per-thread admission is
# delegated to ``_runner_registry`` — there is at most one ThreadRunner
# per thread_id, and ``registry.active(tid)`` is the source of truth.
_user_inflight: dict[str, int] = {}
_server_inflight: list[int] = [0]
_inflight_lock = asyncio.Lock()

# Registry of in-flight (and recently-finished) ThreadRunners. Module
# scope so it persists for the lifetime of the gateway process. The bus
# replay buffer inside each runner gives reconnecting clients up to
# ``RunnerRegistry.DEFAULT_IDLE_RETENTION_SECONDS`` to catch up on a run
# whose SSE link dropped.
_runner_registry: RunnerRegistry = RunnerRegistry()


def runner_registry() -> RunnerRegistry:
    """Test-friendly accessor — tests can monkeypatch this to swap in a
    fresh registry per test, avoiding cross-test runner leakage."""
    return _runner_registry


def _build_memory_system_prompt(db, user_id: str) -> str | None:
    """Format the user's memory facts into an appended system prompt.

    Returns ``None`` when there are no facts, which the adapter treats as
    "don't add the flag" — avoiding an empty-string prompt that would
    still cost a token on every turn. Ordering is newest-first (matches
    the UI) so recent edits get stronger priority in the model's
    attention.
    """
    rows = db.list_memory_facts_for_user(user_id)
    if not rows:
        return None
    # Keep each line compact; the prompt is passed as a single argv
    # argument and CC argv is bounded by ARG_MAX. A dozen short facts is
    # ~1KB — well under the ~256KB ARG_MAX limit on macOS — but we still
    # truncate each fact to 800 chars defensively.
    lines: list[str] = []
    for r in rows:
        content = r.content.strip().replace("\n", " ")
        if len(content) > 800:
            content = content[:800] + "…"
        lines.append(f"- {content}")
    joined = "\n".join(lines)
    return (
        "User memory (facts this user has asked you to remember across "
        "conversations). Treat these as persistent context:\n\n"
        f"{joined}"
    )


def _derive_title(prompt: str, *, max_chars: int = 80) -> str:
    """Produce a human-readable thread title from the first user prompt.

    Collapses whitespace, strips, and truncates on a word boundary with a
    trailing ellipsis when too long. Falls back to a nonempty placeholder
    if the prompt is all whitespace (the UI never shows an empty title).
    """
    collapsed = " ".join(prompt.split())
    if not collapsed:
        return "New chat"
    if len(collapsed) <= max_chars:
        return collapsed
    # Cut at the last space within the budget so we don't slice mid-word.
    cut = collapsed[:max_chars].rsplit(" ", 1)[0]
    if not cut:
        cut = collapsed[:max_chars]
    return f"{cut}…"


async def _release_admission(user_id: str) -> None:
    """Release per-user + server-wide slots.

    Idempotent — safe to call multiple times. All counters clamp at 0.
    Per-thread "is something running" lives on the runner registry, so
    there is no per-thread counter to release here.
    """
    async with _inflight_lock:
        if user_id in _user_inflight:
            _user_inflight[user_id] -= 1
            if _user_inflight[user_id] <= 0:
                del _user_inflight[user_id]
        _server_inflight[0] = max(0, _server_inflight[0] - 1)


@router.post("/threads")
def create_thread(user_id: str = Depends(current_user_id)) -> dict:
    from uuid import uuid4

    tid = f"t_{uuid4().hex[:12]}"
    cwd = _thread_cwd(tid)
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd.parent / "uploads").mkdir(parents=True, exist_ok=True)
    (cwd.parent / "outputs").mkdir(parents=True, exist_ok=True)
    _store().create(tid, str(cwd), user_id=user_id)
    return {"id": tid, "cwd": str(cwd)}


@router.get("/threads")
def list_threads(user_id: str = Depends(current_user_id)) -> dict:
    """List the caller's threads.

    Returns newest-first by creation time (derived from the thread's
    cwd mtime, since ``session_store`` doesn't carry a created_at column
    yet). Response shape::

        { "threads": [ { "id": "t_...", "updated_at": "2026-04-16T..." }, ... ] }

    An ISO-8601 ``updated_at`` keeps the frontend shape stable even though
    the field is derived on-the-fly. Missing cwds (e.g. directory deleted
    out-of-band) fall back to 0.0 so the row still appears at the bottom.
    """
    from datetime import UTC, datetime
    from os import stat as _stat

    rows = _store().list_for_user(user_id)
    out: list[dict] = []
    for r in rows:
        try:
            ts = _stat(r.cwd).st_mtime
        except OSError:
            ts = 0.0
        out.append(
            {
                "id": r.thread_id,
                "title": r.title,
                "updated_at": datetime.fromtimestamp(ts, UTC).isoformat() if ts else None,
                "has_session": r.session_id is not None,
            }
        )
    # Newest first. ``None`` updated_at sorts last.
    out.sort(key=lambda d: d["updated_at"] or "", reverse=True)
    return {"threads": out}


@router.get("/threads/{tid}/history")
def get_thread_history(
    tid: str,
    user_id: str = Depends(current_user_id),
) -> dict:
    """Return the thread's past turns so the UI can rehydrate on reload.

    Reads CC's own session jsonl (one line per event), filters out
    bookkeeping frames (queue-operation / attachment / last-prompt),
    and normalizes each surviving ``user``/``assistant`` row into a
    shape the frontend reducer already consumes.

    We deliberately do NOT return adapter/system/result frames — those
    are per-spawn and would pollute the transcript with stale "system
    init" banners and cost footers from previous turns. Only the pure
    conversation turns make it through.
    """
    import json
    from pathlib import Path

    row = _store().get(tid)
    # Mirror the ownership check used everywhere else: unknown-or-not-yours
    # collapses to a single 404.
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "thread_not_found")

    if row.session_id is None:
        # Thread was created but never sent a message — no history.
        return {"messages": []}

    # Claude Code stores session jsonl under
    # ``~/.claude/projects/<cwd-slug>/<session_id>.jsonl``, where
    # ``<cwd-slug>`` is the absolute cwd with every "/", ".", and "_"
    # replaced by "-". For example,
    # ``/Users/shine/.harmony-code/data/threads/t_abc/user-data/workspace``
    # maps to
    # ``-Users-shine--harmony-code-data-threads-t-abc-user-data-workspace``
    # (note the doubled dashes where dots and underscores were).
    import re as _re

    home = Path.home()
    cwd_slug = _re.sub(r"[._/]", "-", row.cwd)
    jsonl_path = home / ".claude" / "projects" / cwd_slug / f"{row.session_id}.jsonl"
    if not jsonl_path.exists():
        # Session id is known but CC hasn't written the file yet (or it was
        # pruned). Fall back to empty rather than 500 so the UI stays usable.
        return {"messages": []}

    messages: list[dict] = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = evt.get("type")
            if t not in ("user", "assistant"):
                # Skip queue-operation / attachment / last-prompt / etc.
                continue
            msg = evt.get("message")
            if not isinstance(msg, dict):
                continue
            if t == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    # Plain user prompt. Emit as a synthetic user bubble.
                    messages.append(
                        {
                            "kind": "user_turn",
                            "id": evt.get("uuid") or f"u-{len(messages)}",
                            "text": content,
                        }
                    )
                elif isinstance(content, list):
                    # tool_result batch — forward as-is so the reducer's
                    # backfill logic attaches them to the right tool_use.
                    messages.append(
                        {
                            "kind": "event",
                            "event": {
                                "type": "user",
                                "message": {"content": content},
                                "parent_tool_use_id": evt.get("parent_tool_use_id"),
                            },
                        }
                    )
            elif t == "assistant":
                # Forward the whole assistant message verbatim. The client-side
                # reducer's ``handleAssistant`` merges on ``message.id`` so
                # multiple partials of the same reply (rare in history, common
                # during live streaming) still coalesce.
                messages.append(
                    {
                        "kind": "event",
                        "event": {
                            "type": "assistant",
                            "message": msg,
                            "parent_tool_use_id": evt.get("parent_tool_use_id"),
                        },
                    }
                )
    return {"messages": messages}


@router.delete("/threads/{tid}")
async def delete_thread(tid: str, user_id: str = Depends(current_user_id)) -> dict:
    """Delete a thread row.

    Ownership-aware: unknown or not-yours both 404 (same rule as every
    other ``/api/threads/*`` endpoint). Refuses to delete a thread with
    an in-flight stream (409) — that would orphan a running CC process
    and break ``--resume``.

    The cwd directory on disk is deliberately NOT removed — users may
    have files they want to retrieve out-of-band, and a full reaper is
    an ops concern (cron, not request path). We just drop the row from
    ``session_store`` so the thread stops appearing in listings.
    """
    store = _store()
    row = store.get(tid)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "thread_not_found")
    if runner_registry().active(tid):
        raise HTTPException(409, "thread_busy")
    store.delete(tid)
    return {"deleted": True, "id": tid}


@router.post("/threads/{tid}/messages")
async def send_message(
    tid: str,
    body: SendMessageBody,
    request: Request,
    user_id: str = Depends(current_user_id),
):
    store = _store()
    row = store.get(tid)
    # Single 404 covers "unknown tid" AND "not-your-tid" — avoid leaking
    # existence of other users' threads (Task 5.3). ``row.user_id`` is
    # nullable for pre-5.3 legacy rows; a NULL owner is owned by nobody,
    # so the comparison below still rejects.
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "thread_not_found")

    # Admission control per design Section 2 concurrency table:
    # server capacity (503) > per-user concurrency (429) > per-thread
    # serialize (409). Evaluated in that order under the lock so we
    # never admit past a higher-scope limit. Per-thread "busy" means
    # the runner registry has a still-running ThreadRunner for this tid.
    registry = runner_registry()
    async with _inflight_lock:
        if _server_inflight[0] >= _MAX_SERVER:
            raise HTTPException(503, "server_busy")
        if _user_inflight.get(user_id, 0) >= _MAX_PER_USER:
            raise HTTPException(429, "user_concurrency_limit")
        if registry.active(tid):
            raise HTTPException(409, "thread_busy")
        _user_inflight[user_id] = _user_inflight.get(user_id, 0) + 1
        _server_inflight[0] += 1

    # Compose per-spawn MCP config + skills dir from the user's DB rows.
    # Rebuilt on every request so CRUD edits take effect on the next spawn
    # without any caching layer. row.cwd is authoritative for workspace
    # location (set at create_thread time); we only derive the thread root
    # from data_dir for uploads/.claude placement.
    #
    # Compose can raise (e.g. a malformed stdio MCP row with no command
    # raises ValueError). If anything between here and the ``return
    # EventSourceResponse(...)`` below fails, ``event_gen``'s ``finally``
    # never fires — so we must release ``_inflight`` ourselves, otherwise
    # the thread is wedged at 409 until server restart.
    try:
        # Capture a friendly display title on the first prompt (idempotent
        # — subsequent sends are no-ops). Keeps the sidebar/Chats list
        # readable for non-technical users; the raw thread_id is useless
        # at a glance.
        store.set_title_if_empty(tid, _derive_title(body.content))
        data_dir = _data_dir()
        tmp_root = data_dir / "tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        thread_root = data_dir / "threads" / tid / "user-data"
        db = get_db()
        mcp_path = compose_mcp_config(db=db, user_id=user_id, thread_id=tid, tmp_root=tmp_root)
        compose_skills_dir(db=db, user_id=user_id, skills_dir=thread_root / ".claude" / "skills")

        # User's default_model pref → SpawnConfig.model → --model in argv
        # (adapter.build_cmd appends it iff cfg.model is truthy). Missing row
        # and row with default_model IS NULL both yield None here, so neither
        # the adapter nor CC gets a model flag — CC picks its own default.
        prefs = db.get_user_prefs(user_id)
        # Compose the user's memory facts into an appended system prompt so
        # the agent sees them as persistent context across threads. Facts
        # are loaded on every spawn (cheap: single indexed SELECT) so edits
        # made mid-session take effect on the next message without any
        # caching layer to invalidate.
        memory_prompt = _build_memory_system_prompt(db, user_id)
        adapter = CCAdapter()
        # Optional wall-clock budget per request, driven by env. Unset →
        # no cap (MVP default — legitimate long runs survive). Tests
        # monkeypatch this env var to force the timeout path.
        timeout_env = os.environ.get("HARMONY_CC_TIMEOUT_SECONDS")
        cfg = SpawnConfig(
            cwd=row.cwd,
            user_prompt=body.content,
            resume_session_id=row.session_id,
            mcp_config_path=str(mcp_path),
            add_dirs=[str(thread_root / "uploads")],
            permission_mode="bypassPermissions",
            model=prefs.default_model if prefs else None,
            append_system_prompt=memory_prompt,
            timeout_seconds=float(timeout_env) if timeout_env else None,
        )
        # Audit: spawn event. build_cmd is pure — calling it twice (here
        # for hashing, again inside adapter.run) is safe. We drop the
        # trailing prompt positional so the hash reflects flags only, per
        # design Section 5.
        argv_full = adapter.build_cmd(cfg)
        # build_cmd contract: the prompt is the last positional (after the
        # ``--`` terminator). We hash argv minus the prompt per spec — if
        # that contract ever changes, the slice below drops the wrong
        # element and every audit line silently mis-hashes. Fail loudly
        # instead.
        assert argv_full[-1] == cfg.user_prompt, "build_cmd contract: prompt must be the last element of argv"
        argv_without_prompt = argv_full[:-1]
        mcp_names = [r.name for r in db.query_mcp_for_user(user_id=user_id, enabled_only=True)]
        skill_names = [r.name for r in db.query_skills_for_user(user_id=user_id, enabled_only=True)]
        audit_emit(
            spawn_event(
                user_id=user_id,
                thread_id=tid,
                session_id=row.session_id,
                model=cfg.model,
                argv_without_prompt=argv_without_prompt,
                prompt_len=len(body.content),
                mcp_servers_enabled=mcp_names,
                skills_enabled=skill_names,
            )
        )
    except BaseException:
        await _release_admission(user_id)
        raise

    # Spin up a ThreadRunner and start the CC subprocess inside its own
    # background task. The on_terminate hook fires when the run finishes
    # (naturally / cancelled / errored) — that's where we emit cc.result
    # and free this user's admission slot. Critically, the runner is
    # NOT bound to this HTTP request: if the SSE client disconnects, the
    # runner keeps going and the next reconnect (POST returning 409 +
    # GET /stream subscribing to the bus) drains the buffered events.
    start_ts = time.monotonic()

    async def _on_terminate(outcome: RunnerOutcome) -> None:
        # Persist the session_id captured from the first ``init`` frame
        # so subsequent turns on this thread can ``--resume <sid>``.
        if outcome.session_id and row.session_id is None:
            try:
                store.set_session_id(tid, outcome.session_id)
            except Exception as e:  # pragma: no cover - persistence is best-effort
                logger.debug("set_session_id failed: %r", e)
        duration_ms = outcome.duration_ms or int((time.monotonic() - start_ts) * 1000)
        try:
            audit_emit(
                result_event(
                    user_id=user_id,
                    thread_id=tid,
                    session_id=outcome.session_id,
                    duration_ms=duration_ms,
                    exit_code=outcome.exit_code,
                    cost_usd=outcome.cost_usd,
                    disposition=outcome.disposition,
                )
            )
        except Exception as e:  # pragma: no cover
            logger.debug("audit result emit swallowed: %r", e)
        await _release_admission(user_id)

    try:
        runner = await registry.start(
            thread_id=tid,
            cfg=cfg,
            adapter=adapter,
            on_terminate=_on_terminate,
        )
    except BaseException:
        await _release_admission(user_id)
        raise

    return EventSourceResponse(_subscribe_sse(runner, after_event_id=0))


def _parse_last_event_id(header_value: str | None, expected_run_id: str) -> int:
    """Parse an SSE ``Last-Event-ID`` header into a cursor for resume.

    The id format we emit is ``<run_id>:<event_id>``. On reconnect the
    client echoes that back, and we resume from ``event_id`` *only* if
    the run_id matches the current runner — a stale id from a previous
    run resets to 0 (the new run starts fresh from event 1).
    """
    if not header_value:
        return 0
    if ":" not in header_value:
        return 0
    rid, _, eid = header_value.partition(":")
    if rid != expected_run_id:
        return 0
    try:
        return max(0, int(eid))
    except ValueError:
        return 0


async def _subscribe_sse(runner, after_event_id: int) -> AsyncIterator[dict]:
    """Drain ``runner.bus`` and yield SSE-shaped frames.

    Each yielded dict carries an ``id`` field of the form
    ``<run_id>:<event_id>`` so reconnecting clients can resume via
    the standard ``Last-Event-ID`` header. Exits when the bus closes
    (the runner finished — naturally, cancelled, or errored) by emitting
    a terminal ``done`` SSE event.

    Disconnects are handled by the underlying ``EventSourceResponse`` /
    ``async for`` machinery: when the client goes away, this generator
    is acloseed, which drops the bus subscription. The runner keeps
    running. That is the entire point.
    """
    async for eid, ev in runner.bus.subscribe(after_event_id=after_event_id):
        # Skip the synthetic ``lost_events`` marker's id (it's id=0).
        # We still emit it as data so the client can act on it.
        sse_id = f"{runner.run_id}:{eid}" if eid > 0 else None
        frame = {"data": json.dumps(ev, separators=(",", ":"))}
        if sse_id is not None:
            frame["id"] = sse_id
        yield frame
    # Bus closed → run is over. Emit the terminal SSE ``done`` event so
    # clients can stop listening cleanly.
    yield {"event": "done", "data": "{}"}


@router.post("/threads/{tid}/cancel")
async def cancel_thread(tid: str, user_id: str = Depends(current_user_id)):
    """Cancel the thread's in-flight CC run, if any.

    With the runner registry decoupling SSE from CC lifecycle, the
    client-disconnect path no longer kills the subprocess — this
    endpoint is the *only* way to actually stop a running agent. Sends
    SIGTERM (with the adapter's standard 2s grace before SIGKILL) and
    waits for the runner's terminate hook to finish before returning,
    so the caller knows the slot is free.

    Ownership-aware: an unknown-or-not-yours tid collapses to 404 to
    avoid leaking which thread ids exist across users.
    """
    row = _store().get(tid)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "thread_not_found")
    canceled = await runner_registry().cancel(tid)
    if not canceled:
        return {"canceled": False, "reason": "no_inflight"}
    return {"canceled": True}


@router.get("/threads/{tid}/stream")
async def stream_thread(
    tid: str,
    request: Request,
    user_id: str = Depends(current_user_id),
):
    """Subscribe to a thread's *current* CC run without starting a new one.

    The reconnect path. After ``POST /messages`` returns, every event
    flows out of the runner's EventBus — if the network drops or the
    user reloads the page, the run keeps going on the server. This
    endpoint lets the client hop back onto the bus, optionally with
    ``Last-Event-ID: <run_id>:<event_id>`` for replay-from-cursor.

    Returns 404 if there is no active or recently-finished runner for
    this thread (no live run AND nothing in the registry's retention
    window). When the runner finished naturally a few seconds ago we
    still serve the buffered tail so a slow-reconnecting client sees
    the assistant's final output.
    """
    row = _store().get(tid)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "thread_not_found")
    runner = runner_registry().get(tid)
    if runner is None:
        raise HTTPException(404, "no_active_run")
    after = _parse_last_event_id(request.headers.get("last-event-id"), runner.run_id)
    return EventSourceResponse(_subscribe_sse(runner, after_event_id=after))
