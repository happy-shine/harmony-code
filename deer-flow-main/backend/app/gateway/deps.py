"""Centralized accessors for singleton objects stored on ``app.state``.

**Getters** (used by routers): raise 503 when a required dependency is
missing, except ``get_store`` which returns ``None``.

Initialization is handled directly in ``app.py`` via :class:`AsyncExitStack`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from deerflow.runtime import RunManager, StreamBridge


@asynccontextmanager
async def langgraph_runtime(app: FastAPI) -> AsyncGenerator[None, None]:
    """Bootstrap and tear down all LangGraph runtime singletons.

    Usage in ``app.py``::

        async with langgraph_runtime(app):
            yield
    """
    from deerflow.agents.checkpointer.async_provider import make_checkpointer
    from deerflow.runtime import make_store, make_stream_bridge

    async with AsyncExitStack() as stack:
        app.state.stream_bridge = await stack.enter_async_context(make_stream_bridge())
        app.state.checkpointer = await stack.enter_async_context(make_checkpointer())
        app.state.store = await stack.enter_async_context(make_store())
        app.state.run_manager = RunManager()
        yield


# ---------------------------------------------------------------------------
# Getters – called by routers per-request
# ---------------------------------------------------------------------------


def get_stream_bridge(request: Request) -> StreamBridge:
    """Return the global :class:`StreamBridge`, or 503."""
    bridge = getattr(request.app.state, "stream_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503, detail="Stream bridge not available")
    return bridge


def get_run_manager(request: Request) -> RunManager:
    """Return the global :class:`RunManager`, or 503."""
    mgr = getattr(request.app.state, "run_manager", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Run manager not available")
    return mgr


def get_checkpointer(request: Request):
    """Return the global checkpointer, or 503."""
    cp = getattr(request.app.state, "checkpointer", None)
    if cp is None:
        raise HTTPException(status_code=503, detail="Checkpointer not available")
    return cp


def get_store(request: Request):
    """Return the global store (may be ``None`` if not configured)."""
    return getattr(request.app.state, "store", None)


# ---------------------------------------------------------------------------
# Harmony gateway deps (M3+). Everything above this comment is LangGraph-era
# and will be deleted in M5 along with the runtime it serves.
# ---------------------------------------------------------------------------


def current_user_id() -> str:
    """M3 stub. M5 replaces with real auth dep (e.g. better-auth session)."""
    return "u_default"


def get_db():
    """Fresh Db handle per request. SQLAlchemy's engine cache is internal to
    get_engine(); callers do not need to hold the instance across requests.

    ``get_engine()`` resolves ``HARMONY_DATA_DIR`` from the environment, so
    this function takes no arguments — tests can monkeypatch the env var to
    redirect all routers at an isolated tmp dir.
    """
    from app.db import Db, get_engine

    return Db(get_engine())


# ---------------------------------------------------------------------------
# Thread/session filesystem helpers. Shared by routers that touch the
# per-thread workspace directory (messages, workspace, cancel). Resolve
# ``HARMONY_DATA_DIR`` fresh on every call so tests can monkeypatch it.
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    """Root data directory (``$HARMONY_DATA_DIR`` or ``.harmony-data``)."""
    return Path(os.environ.get("HARMONY_DATA_DIR", ".harmony-data"))


def session_store():
    """Fresh ``SessionStore`` rooted at ``data_dir()/sessions.db``.

    Creates the parent directory if needed (sqlite3 does not create
    missing dirs) and ensures the schema exists. Safe to call from
    every request.
    """
    from app.cc_adapter.session_store import SessionStore

    p = data_dir() / "sessions.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    s = SessionStore(str(p))
    s.ensure_schema()
    return s


def thread_cwd(thread_id: str) -> Path:
    """Per-thread CC working directory. Authoritative ``row.cwd`` for new
    threads; existing threads may point elsewhere if created before this
    layout existed — callers must prefer the stored ``row.cwd``."""
    return data_dir() / "threads" / thread_id / "user-data" / "workspace"
