from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.cc_adapter.adapter import CCAdapter
from app.cc_adapter.session_store import SessionStore
from app.cc_adapter.types import SpawnConfig


router = APIRouter(prefix="/api")


class SendMessageBody(BaseModel):
    content: str
    attachments: list[str] = []


def _data_dir() -> Path:
    return Path(os.environ.get("HARMONY_DATA_DIR", ".harmony-data"))


def _store() -> SessionStore:
    p = _data_dir() / "sessions.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    s = SessionStore(str(p))
    s.ensure_schema()
    return s


def _thread_cwd(thread_id: str) -> Path:
    return _data_dir() / "threads" / thread_id / "user-data" / "workspace"


_inflight: set[str] = set()
_inflight_lock = asyncio.Lock()


@router.post("/threads")
def create_thread() -> dict:
    from uuid import uuid4
    tid = f"t_{uuid4().hex[:12]}"
    cwd = _thread_cwd(tid)
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd.parent / "uploads").mkdir(parents=True, exist_ok=True)
    (cwd.parent / "outputs").mkdir(parents=True, exist_ok=True)
    _store().create(tid, str(cwd))
    return {"id": tid, "cwd": str(cwd)}


@router.post("/threads/{tid}/messages")
async def send_message(tid: str, body: SendMessageBody, request: Request):
    store = _store()
    row = store.get(tid)
    if row is None:
        raise HTTPException(404, "thread not found")

    async with _inflight_lock:
        if tid in _inflight:
            raise HTTPException(409, "thread_busy")
        _inflight.add(tid)

    adapter = CCAdapter()
    cfg = SpawnConfig(
        cwd=row.cwd,
        user_prompt=body.content,
        resume_session_id=row.session_id,
        permission_mode="bypassPermissions",
    )

    async def event_gen() -> AsyncIterator[dict]:
        try:
            async for ev in adapter.run(cfg):
                if await request.is_disconnected():
                    break
                # capture session_id on first init
                if (ev.get("type") == "system"
                        and ev.get("subtype") == "init"
                        and row.session_id is None):
                    sid = ev.get("session_id")
                    if sid:
                        store.set_session_id(tid, sid)
                yield {"data": json.dumps(ev, separators=(",", ":"))}
            yield {"event": "done", "data": "{}"}
        finally:
            async with _inflight_lock:
                _inflight.discard(tid)

    return EventSourceResponse(event_gen())
