"""Minimal SQLite-backed store for thread_id → (session_id, cwd). M1 scope.
Later merged with deer-flow's proper thread table if one exists."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class Mapping:
    thread_id: str
    session_id: str | None
    cwd: str


class SessionStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def ensure_schema(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS cc_thread_session (
                    thread_id   TEXT PRIMARY KEY,
                    session_id  TEXT,
                    cwd         TEXT NOT NULL
                )
            """)

    def create(self, thread_id: str, cwd: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO cc_thread_session(thread_id, cwd) VALUES (?, ?)",
                (thread_id, cwd),
            )

    def get(self, thread_id: str) -> Mapping | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT thread_id, session_id, cwd FROM cc_thread_session WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
        return Mapping(*row) if row else None

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE cc_thread_session SET session_id=? WHERE thread_id=?",
                (session_id, thread_id),
            )

    def delete(self, thread_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM cc_thread_session WHERE thread_id=?", (thread_id,))
