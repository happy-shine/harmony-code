"""Minimal SQLite-backed store for thread_id → (session_id, cwd). M1 scope.
M5 will replace with a consolidated thread table."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass


@dataclass
class Mapping:
    thread_id: str
    session_id: str | None
    cwd: str


class SessionStore:
    """SQLite-backed mapping from ``thread_id`` to ``(session_id, cwd)``.

    Callers must ensure the parent directory of ``db_path`` exists before
    instantiating. ``sqlite3.connect()`` does not create missing directories,
    so a nested path like ``foo/bar/sessions.db`` will fail with
    ``OperationalError: unable to open database file`` unless ``foo/bar/``
    pre-exists. Directory layout is the gateway's responsibility; this
    library-layer code stays unopinionated about filesystem hierarchy.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def ensure_schema(self) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cc_thread_session (
                    thread_id   TEXT PRIMARY KEY,
                    session_id  TEXT,
                    cwd         TEXT NOT NULL
                )
            """)

    def create(self, thread_id: str, cwd: str) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute(
                "INSERT INTO cc_thread_session(thread_id, cwd) VALUES (?, ?)",
                (thread_id, cwd),
            )

    def get(self, thread_id: str) -> Mapping | None:
        with closing(self._conn()) as conn, conn:
            row = conn.execute(
                "SELECT thread_id, session_id, cwd FROM cc_thread_session WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
        return Mapping(*row) if row else None

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute(
                "UPDATE cc_thread_session SET session_id=? WHERE thread_id=?",
                (session_id, thread_id),
            )

    def delete(self, thread_id: str) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute("DELETE FROM cc_thread_session WHERE thread_id=?", (thread_id,))
