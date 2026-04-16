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
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

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


_inflight: set[str] = set()
_inflight_lock = asyncio.Lock()


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


@router.post("/threads/{tid}/messages")
async def send_message(
    tid: str,
    body: SendMessageBody,
    request: Request,
    user_id: str = Depends(current_user_id),
):
    store = _store()
    row = store.get(tid)
    if row is None:
        raise HTTPException(404, "thread not found")

    async with _inflight_lock:
        if tid in _inflight:
            raise HTTPException(409, "thread_busy")
        _inflight.add(tid)

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
        cfg = SpawnConfig(
            cwd=row.cwd,
            user_prompt=body.content,
            resume_session_id=row.session_id,
            mcp_config_path=str(mcp_path),
            add_dirs=[str(thread_root / "uploads")],
            permission_mode="bypassPermissions",
            model=prefs.default_model if prefs else None,
        )
    except BaseException:
        async with _inflight_lock:
            _inflight.discard(tid)
        raise

    async def event_gen() -> AsyncIterator[dict]:
        gen = adapter.run(cfg)
        try:
            async for ev in gen:
                if await request.is_disconnected():
                    await gen.aclose()
                    break
                # capture session_id on first init
                if ev.get("type") == "system" and ev.get("subtype") == "init" and row.session_id is None:
                    sid = ev.get("session_id")
                    if sid:
                        store.set_session_id(tid, sid)
                yield {"data": json.dumps(ev, separators=(",", ":"))}
            else:
                # natural EOF: emit done. (Skipped on break.)
                yield {"event": "done", "data": "{}"}
        finally:
            # Belt-and-suspenders: if we exit via exception or cancellation,
            # aclose the adapter generator to drive its GeneratorExit cleanup.
            try:
                await gen.aclose()
            except Exception as e:  # pragma: no cover
                logger.debug("adapter aclose swallowed on cleanup: %r", e)
            async with _inflight_lock:
                _inflight.discard(tid)

    return EventSourceResponse(event_gen())


@router.post("/threads/{tid}/cancel")
async def cancel_thread(tid: str, user_id: str = Depends(current_user_id)):
    """Explicit cancel. M1 scope: this is a status stub only.

    CC actually dies via the client-disconnect path (SSE stream abort → sse-starlette
    closes event_gen → adapter's GeneratorExit handler terminates the subprocess).
    M5 wires this endpoint to a task registry that can signal an in-flight stream.
    """
    async with _inflight_lock:
        if tid not in _inflight:
            return {"canceled": False, "reason": "no_inflight"}
    return {"canceled": True, "note": "disconnect to actually cancel; explicit kill is M5"}
