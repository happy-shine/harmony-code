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

# Counters are guarded by ``_inflight_lock``. The server total is wrapped
# in a one-element list to avoid ``global`` + reassignment ceremony at
# every mutation site.
_inflight: set[str] = set()
_user_inflight: dict[str, int] = {}
_server_inflight: list[int] = [0]
_inflight_lock = asyncio.Lock()


async def _release_admission(tid: str, user_id: str) -> None:
    """Release per-thread + per-user + server-wide slots.

    Idempotent — safe to call twice (e.g. once in ``event_gen.finally``
    and again in a defensive outer except). All counters clamp at 0.
    """
    async with _inflight_lock:
        _inflight.discard(tid)
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
                "updated_at": datetime.fromtimestamp(ts, UTC).isoformat() if ts else None,
                "has_session": r.session_id is not None,
            }
        )
    # Newest first. ``None`` updated_at sorts last.
    out.sort(key=lambda d: d["updated_at"] or "", reverse=True)
    return {"threads": out}


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
    async with _inflight_lock:
        if tid in _inflight:
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
    # never admit past a higher-scope limit.
    async with _inflight_lock:
        if _server_inflight[0] >= _MAX_SERVER:
            raise HTTPException(503, "server_busy")
        if _user_inflight.get(user_id, 0) >= _MAX_PER_USER:
            raise HTTPException(429, "user_concurrency_limit")
        if tid in _inflight:
            raise HTTPException(409, "thread_busy")
        _inflight.add(tid)
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
        await _release_admission(tid, user_id)
        raise

    async def event_gen() -> AsyncIterator[dict]:
        gen = adapter.run(cfg)
        start = time.monotonic()
        disposition: str = "error"  # default until proven otherwise
        exit_code = 0
        cost_usd: float | None = None
        observed_session_id: str | None = row.session_id
        try:
            async for ev in gen:
                if await request.is_disconnected():
                    disposition = "disconnected"
                    exit_code = -1
                    await gen.aclose()
                    break
                # capture session_id on first init
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    sid = ev.get("session_id")
                    if sid:
                        observed_session_id = sid
                        if row.session_id is None:
                            store.set_session_id(tid, sid)
                # capture terminal cost info
                if ev.get("type") == "result":
                    cost = ev.get("total_cost_usd")
                    if cost is None:
                        cost = ev.get("cost_usd")
                    if cost is not None:
                        cost_usd = cost
                # capture nonzero-exit diagnostic
                if ev.get("type") == "_adapter" and ev.get("subtype") == "error" and ev.get("code") == "cc_nonzero_exit":
                    ec = ev.get("exit_code")
                    if isinstance(ec, int):
                        exit_code = ec
                yield {"data": json.dumps(ev, separators=(",", ":"))}
            else:
                # natural EOF: emit done. (Skipped on break.)
                disposition = "natural"
                yield {"event": "done", "data": "{}"}
        finally:
            # Belt-and-suspenders: if we exit via exception or cancellation,
            # aclose the adapter generator to drive its GeneratorExit cleanup.
            try:
                await gen.aclose()
            except Exception as e:  # pragma: no cover
                logger.debug("adapter aclose swallowed on cleanup: %r", e)
            # On the error path (any exception before the for/else fired
            # AND no disconnect was observed), exit_code stays 0 unless an
            # adapter.error frame was seen — normalize it to -1 since the
            # run did not finish cleanly.
            if disposition == "error":
                exit_code = -1
            duration_ms = int((time.monotonic() - start) * 1000)
            try:
                audit_emit(
                    result_event(
                        user_id=user_id,
                        thread_id=tid,
                        session_id=observed_session_id,
                        duration_ms=duration_ms,
                        exit_code=exit_code,
                        cost_usd=cost_usd,
                        disposition=disposition,  # type: ignore[arg-type]
                    )
                )
            except Exception as e:  # pragma: no cover
                logger.debug("audit result emit swallowed: %r", e)
            await _release_admission(tid, user_id)

    return EventSourceResponse(event_gen())


@router.post("/threads/{tid}/cancel")
async def cancel_thread(tid: str, user_id: str = Depends(current_user_id)):
    """Explicit cancel. Ownership-aware stub.

    CC actually dies via the client-disconnect path (SSE stream abort →
    sse-starlette closes event_gen → adapter's GeneratorExit handler
    terminates the subprocess). This endpoint still validates ownership
    so cross-user callers can't use it to probe which thread ids exist or
    leak the current inflight set. Task 5.3.

    M-future wires the body to a task registry that can signal an
    in-flight stream; the 404 gate here stays.
    """
    # Ownership check before touching _inflight. Same 404-for-both rule
    # as send_message so this endpoint doesn't reveal thread existence.
    row = _store().get(tid)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, "thread_not_found")
    async with _inflight_lock:
        if tid not in _inflight:
            return {"canceled": False, "reason": "no_inflight"}
    return {"canceled": True, "note": "disconnect to actually cancel; explicit kill is M5"}
