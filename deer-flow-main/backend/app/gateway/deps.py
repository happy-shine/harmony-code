"""Per-request dependency providers for the harmony gateway.

M5 scope: harmony-only. The LangGraph-era ``langgraph_runtime``,
``get_stream_bridge``, ``get_run_manager``, ``get_checkpointer``, and
``get_store`` helpers were removed together with ``app.gateway.app``
and the runtime they served.

Task 5.2 replaces the ``u_default`` stub with real session-cookie auth:
``current_user`` reads the ``harmony_session`` cookie, validates it, and
returns a :class:`~app.db.UserRow`. ``current_user_id`` keeps its old
signature so M3 routers keep working — it just delegates to the new dep
and raises 401 when unauthenticated.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, HTTPException, Request

from app.db import UserRow


def get_db():
    """Fresh Db handle per request. SQLAlchemy's engine cache is internal to
    get_engine(); callers do not need to hold the instance across requests.

    ``get_engine()`` resolves ``HARMONY_DATA_DIR`` from the environment, so
    this function takes no arguments — tests can monkeypatch the env var to
    redirect all routers at an isolated tmp dir.
    """
    from app.db import Db, get_engine

    return Db(get_engine())


def current_user(
    request: Request,
    db=Depends(get_db),
) -> UserRow:
    """Resolve the current user from the session cookie.

    Raises 401 when there is no cookie, the session is expired or
    unknown, or the underlying user row has been deleted. On success,
    the session's ``last_seen_at`` is bumped so the TTL rolls forward.
    """
    token = request.cookies.get("harmony_session")
    if not token:
        raise HTTPException(status_code=401, detail="not_authenticated")
    session = db.get_auth_session(token)
    if session is None:
        raise HTTPException(status_code=401, detail="session_expired_or_invalid")
    user = db.get_user_by_id(session.user_id)
    if user is None:
        # Session points at a deleted user — nuke the session to avoid
        # replay, then treat as unauthenticated.
        db.delete_auth_session(token)
        raise HTTPException(status_code=401, detail="user_not_found")
    db.touch_auth_session(token)
    return user


def current_user_id(user: UserRow = Depends(current_user)) -> str:
    """Back-compat shim. M3/M4 routers depend on this to get the owner
    id; post-5.2 they transparently see the authenticated user's id."""
    return user.id


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
