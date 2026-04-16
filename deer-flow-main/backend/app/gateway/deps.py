"""Per-request dependency providers for the harmony gateway.

M5 scope: harmony-only. The LangGraph-era ``langgraph_runtime``,
``get_stream_bridge``, ``get_run_manager``, ``get_checkpointer``, and
``get_store`` helpers were removed together with ``app.gateway.app``
and the runtime they served.
"""

from __future__ import annotations

import os
from pathlib import Path


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
