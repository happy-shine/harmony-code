"""Minimal SQLite-backed store for thread_id → (session_id, cwd, user_id).

M1 scope kept (raw sqlite3, single-table store); M5 Task 5.3 adds a
``user_id`` column so ``/api/threads/*`` endpoints can enforce per-user
ownership. The broader consolidation into the alembic-managed
``cc_thread_session`` in ``harmony.db`` is still pending (out of 5.3 scope).

Legacy rows — written before Task 5.3 — have ``user_id = NULL``. They are
treated as owned by nobody: every authenticated user gets a 404 when
asking for them. In practice this only happens if a manually-inserted
fixture or a prior-version DB sneaks into the data dir; fresh thread
creation always records an owner.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass


@dataclass
class Mapping:
    thread_id: str
    session_id: str | None
    cwd: str
    user_id: str | None


class SessionStore:
    """SQLite-backed mapping from ``thread_id`` to ``(session_id, cwd, user_id)``.

    Callers must ensure the parent directory of ``db_path`` exists before
    instantiating. ``sqlite3.connect()`` does not create missing directories,
    so a nested path like ``foo/bar/sessions.db`` will fail with
    ``OperationalError: unable to open database file`` unless ``foo/bar/``
    pre-exists. Directory layout is the gateway's responsibility; this
    library-layer code stays unopinionated about filesystem hierarchy.

    Schema: ``(thread_id PK, session_id, cwd, user_id)``. ``user_id`` is
    nullable to accommodate pre-5.3 rows (see module docstring), but all
    new rows written via :meth:`create` carry a required owner.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def ensure_schema(self) -> None:
        """Create the table if missing; ALTER to add ``user_id`` on pre-5.3 DBs.

        Idempotent: safe to call on every ``session_store()`` dependency
        resolution. The ``PRAGMA table_info`` check keeps the ALTER path
        out of the fast path once the column exists.
        """
        with closing(self._conn()) as conn, conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cc_thread_session (
                    thread_id   TEXT PRIMARY KEY,
                    session_id  TEXT,
                    cwd         TEXT NOT NULL,
                    user_id     TEXT
                )
            """)
            # Migrate pre-5.3 DBs that have the table but no user_id column.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(cc_thread_session)").fetchall()}
            if "user_id" not in cols:
                # Belt-and-suspenders against a concurrent first-run race:
                # two workers can both read the pre-migration PRAGMA,
                # both try to ALTER, and the loser hits "duplicate column
                # name". The PRAGMA guard above catches the common case;
                # this try/except covers the narrow multi-process window
                # where two ``ensure_schema`` calls overlap on the very
                # first request against a fresh DB.
                try:
                    conn.execute("ALTER TABLE cc_thread_session ADD COLUMN user_id TEXT")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        raise
            # Index for per-user listings. CREATE INDEX IF NOT EXISTS is
            # idempotent and cheap; sqlite uses it for list_for_user.
            conn.execute("CREATE INDEX IF NOT EXISTS ix_cc_thread_session_user ON cc_thread_session(user_id)")

    def create(self, thread_id: str, cwd: str, *, user_id: str) -> None:
        """Insert a new thread row. ``user_id`` is required (keyword-only).

        Post-5.3 callers must record the owner on creation — anonymous
        rows end up unreachable by every authenticated endpoint."""
        with closing(self._conn()) as conn, conn:
            conn.execute(
                "INSERT INTO cc_thread_session(thread_id, cwd, user_id) VALUES (?, ?, ?)",
                (thread_id, cwd, user_id),
            )

    def get(self, thread_id: str) -> Mapping | None:
        with closing(self._conn()) as conn, conn:
            row = conn.execute(
                "SELECT thread_id, session_id, cwd, user_id FROM cc_thread_session WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
        return Mapping(*row) if row else None

    def list_for_user(self, user_id: str) -> list[Mapping]:
        """Return all thread rows owned by ``user_id`` (newest insertion
        order not guaranteed — callers that care should sort).

        NULL-owner legacy rows are excluded; a parameterized ``=`` against
        NULL never matches, which is the desired behavior (see module
        docstring on legacy-row policy).
        """
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                "SELECT thread_id, session_id, cwd, user_id FROM cc_thread_session WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return [Mapping(*r) for r in rows]

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute(
                "UPDATE cc_thread_session SET session_id=? WHERE thread_id=?",
                (session_id, thread_id),
            )

    def delete(self, thread_id: str) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute("DELETE FROM cc_thread_session WHERE thread_id=?", (thread_id,))
