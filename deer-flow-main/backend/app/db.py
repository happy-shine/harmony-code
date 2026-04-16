"""Minimal SQLAlchemy-backed access layer for harmony.db.

Alembic (``backend/alembic/versions/001_harmony_schema.py``) owns the
schema. This module is the thin read/write API used by the CC adapter's
``compose.py`` (and, once M3 tasks 3.4/3.5 land, by the MCP and skills
routers).

We intentionally use raw SQL via :func:`sqlalchemy.text` instead of the
ORM: the table shapes are small and stable, and staying close to SQL
keeps the surface area minimal and predictable.
"""
from __future__ import annotations

import json as _json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def _sqlite_url(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data_dir / 'harmony.db'}"


def get_engine(data_dir: Path | None = None) -> Engine:
    """Lazily create a SQLAlchemy engine for ``harmony.db``.

    Defaults the data directory to ``$HARMONY_DATA_DIR`` (falling back to
    ``.harmony-data`` in the cwd) to match the alembic env config.
    """
    if data_dir is None:
        data_dir = Path(os.environ.get("HARMONY_DATA_DIR", ".harmony-data"))
    return create_engine(_sqlite_url(data_dir), future=True)


@dataclass
class McpRow:
    """One row of ``mcp_servers`` as consumed by ``compose_mcp_config``."""

    id: str
    user_id: str | None
    name: str
    transport: str
    command: str | None
    args_json: str | None
    url: str | None
    headers_json: str | None
    env_json: str | None
    enabled: bool


@dataclass
class SkillRow:
    """One row of ``skills`` as consumed by ``compose_skills_dir``."""

    id: str
    user_id: str | None
    name: str
    source: str
    path: str
    enabled: bool


@dataclass
class UploadRow:
    """One row of ``uploads`` as consumed by the uploads router (Task 4.3).

    ``created_at`` is populated by the ``server_default=sa.func.now()`` in
    the alembic migration. Under SQLAlchemy's raw ``text()`` interface
    against SQLite, the value comes back as an ISO string rather than a
    typed :class:`datetime.datetime` (the sqlite3 DBAPI preserves the
    column's text representation); the router accepts both and stringifies
    for JSON.
    """

    id: str
    thread_id: str
    user_id: str | None
    filename: str
    size: int
    content_type: str | None
    created_at: datetime | str | None


@dataclass
class UserPrefsRow:
    """One row of ``user_prefs`` as consumed by the ``/api/models`` router
    and ``send_message`` (reads ``default_model`` to set ``SpawnConfig.model``).

    ``extras_json`` is a forward-compat slot reserved for M4+ preferences
    (theme, timezone, etc.); deliberately not surfaced through the HTTP API
    in M3.
    """

    user_id: str
    default_model: str | None
    extras_json: str | None


class Db:
    """Thin SQLAlchemy wrapper exposing only what routers/compose need.

    We deliberately keep this class minimal — alembic is the schema source
    of truth, and each caller only needs a handful of parameterized
    queries. Additional CRUD methods (list/get/update/delete) will land
    alongside the MCP / skills routers in subsequent M3 tasks.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ------------------------------------------------------------------
    # MCP servers
    # ------------------------------------------------------------------
    def insert_mcp(
        self,
        *,
        user_id: str | None,
        name: str,
        transport: str,
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        enabled: bool = True,
    ) -> str:
        """Insert an ``mcp_servers`` row and return its generated id."""
        new_id = f"mcp_{uuid.uuid4().hex[:12]}"
        args_json = _json.dumps(args) if args else None
        headers_json = _json.dumps(headers) if headers else None
        env_json = _json.dumps(env) if env else None
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO mcp_servers (id, user_id, name, transport, command,
                        args_json, url, headers_json, env_json, enabled)
                    VALUES (:id, :uid, :name, :t, :cmd, :args, :url, :headers, :env, :enabled)
                    """
                ),
                {
                    "id": new_id,
                    "uid": user_id,
                    "name": name,
                    "t": transport,
                    "cmd": command,
                    "args": args_json,
                    "url": url,
                    "headers": headers_json,
                    "env": env_json,
                    "enabled": enabled,
                },
            )
        return new_id

    def query_mcp_for_user(
        self, *, user_id: str, enabled_only: bool = True
    ) -> list[McpRow]:
        """Return MCP rows owned by ``user_id`` plus global rows (``user_id IS NULL``)."""
        sql = (
            "SELECT id, user_id, name, transport, command, args_json, url, "
            "headers_json, env_json, enabled "
            "FROM mcp_servers "
            "WHERE (user_id = :uid OR user_id IS NULL)"
        )
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY name"
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), {"uid": user_id}).mappings().all()
        return [McpRow(**{**dict(r), "enabled": bool(r["enabled"])}) for r in rows]

    def list_mcp_for_user(self, *, user_id: str) -> list[McpRow]:
        """All rows (enabled + disabled) owned by ``user_id`` OR global (``user_id IS NULL``).

        Used by the CRUD router; callers that want only enabled rows for a
        CC spawn should still use :meth:`query_mcp_for_user`.
        """
        sql = (
            "SELECT id, user_id, name, transport, command, args_json, url, "
            "headers_json, env_json, enabled "
            "FROM mcp_servers "
            "WHERE (user_id = :uid OR user_id IS NULL) "
            "ORDER BY name"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), {"uid": user_id}).mappings().all()
        return [McpRow(**{**dict(r), "enabled": bool(r["enabled"])}) for r in rows]

    def get_mcp(self, mcp_id: str) -> McpRow | None:
        sql = (
            "SELECT id, user_id, name, transport, command, args_json, url, "
            "headers_json, env_json, enabled "
            "FROM mcp_servers WHERE id = :id"
        )
        with self.engine.connect() as conn:
            row = conn.execute(text(sql), {"id": mcp_id}).mappings().first()
        if row is None:
            return None
        return McpRow(**{**dict(row), "enabled": bool(row["enabled"])})

    def delete_mcp(self, mcp_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM mcp_servers WHERE id = :id"), {"id": mcp_id}
            )

    def update_mcp(self, mcp_id: str, patch: dict) -> None:
        """Update only the columns present in ``patch``.

        Recognized keys: ``name``, ``transport``, ``command``, ``args``,
        ``url``, ``headers``, ``env``, ``enabled``. JSON-serializable
        fields (``args``/``headers``/``env``) are ``json.dumps``'d before
        storage; empty/falsy values are stored as ``NULL``. Unknown keys
        are silently ignored (defensive for forward-compat).
        """
        JSON_FIELDS = {"args": "args_json", "headers": "headers_json", "env": "env_json"}
        DIRECT = {"name", "transport", "command", "url", "enabled"}

        sets: list[str] = []
        params: dict = {"id": mcp_id}
        for k, v in patch.items():
            if k in DIRECT:
                sets.append(f"{k} = :{k}")
                params[k] = v
            elif k in JSON_FIELDS:
                col = JSON_FIELDS[k]
                sets.append(f"{col} = :{col}")
                params[col] = _json.dumps(v) if v else None
        if not sets:
            return
        sql = f"UPDATE mcp_servers SET {', '.join(sets)} WHERE id = :id"
        with self.engine.begin() as conn:
            conn.execute(text(sql), params)

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------
    def insert_skill(
        self,
        *,
        user_id: str | None,
        name: str,
        source: str,
        path: str,
        enabled: bool = True,
    ) -> str:
        """Insert a ``skills`` row and return its generated id."""
        new_id = f"sk_{uuid.uuid4().hex[:12]}"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO skills (id, user_id, name, source, path, enabled)
                    VALUES (:id, :uid, :name, :src, :path, :enabled)
                    """
                ),
                {
                    "id": new_id,
                    "uid": user_id,
                    "name": name,
                    "src": source,
                    "path": path,
                    "enabled": enabled,
                },
            )
        return new_id

    def query_skills_for_user(
        self, *, user_id: str, enabled_only: bool = True
    ) -> list[SkillRow]:
        """Return skill rows owned by ``user_id`` plus global rows (``user_id IS NULL``)."""
        sql = (
            "SELECT id, user_id, name, source, path, enabled "
            "FROM skills "
            "WHERE (user_id = :uid OR user_id IS NULL)"
        )
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY name"
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), {"uid": user_id}).mappings().all()
        return [SkillRow(**{**dict(r), "enabled": bool(r["enabled"])}) for r in rows]

    def list_skills_for_user(self, *, user_id: str) -> list[SkillRow]:
        """All rows (enabled + disabled) owned by ``user_id`` OR global (``user_id IS NULL``).

        Used by the CRUD router; callers that want only enabled rows for a
        CC spawn should still use :meth:`query_skills_for_user`.
        """
        sql = (
            "SELECT id, user_id, name, source, path, enabled "
            "FROM skills "
            "WHERE (user_id = :uid OR user_id IS NULL) "
            "ORDER BY name"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), {"uid": user_id}).mappings().all()
        return [SkillRow(**{**dict(r), "enabled": bool(r["enabled"])}) for r in rows]

    def get_skill(self, skill_id: str) -> SkillRow | None:
        sql = (
            "SELECT id, user_id, name, source, path, enabled "
            "FROM skills WHERE id = :id"
        )
        with self.engine.connect() as conn:
            row = conn.execute(text(sql), {"id": skill_id}).mappings().first()
        if row is None:
            return None
        return SkillRow(**{**dict(row), "enabled": bool(row["enabled"])})

    def delete_skill(self, skill_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM skills WHERE id = :id"), {"id": skill_id}
            )

    def update_skill(self, skill_id: str, patch: dict) -> None:
        """Update only the columns present in ``patch``.

        Recognized keys: ``name``, ``enabled``. ``source`` and ``path`` are
        install-time artifacts owned by the installer — to change them the
        caller should delete the row (+ its skills_store dir) and reinstall.
        Unknown keys are silently ignored (defensive for forward-compat).
        """
        DIRECT = {"name", "enabled"}
        sets: list[str] = []
        params: dict = {"id": skill_id}
        for k, v in patch.items():
            if k in DIRECT:
                sets.append(f"{k} = :{k}")
                params[k] = v
        if not sets:
            return
        sql = f"UPDATE skills SET {', '.join(sets)} WHERE id = :id"
        with self.engine.begin() as conn:
            conn.execute(text(sql), params)

    # ------------------------------------------------------------------
    # User prefs (M3 task 3.6)
    # ------------------------------------------------------------------
    def get_user_prefs(self, user_id: str) -> UserPrefsRow | None:
        """Return the ``user_prefs`` row for ``user_id`` or ``None`` if absent.

        A missing row and a row with ``default_model IS NULL`` are
        semantically different: the former means the user has never
        touched their model preference (use whatever default CC picks),
        the latter means they explicitly cleared it (same effect, but
        ``send_message`` still checks the row to decide).
        """
        sql = (
            "SELECT user_id, default_model, extras_json "
            "FROM user_prefs WHERE user_id = :uid"
        )
        with self.engine.connect() as conn:
            row = conn.execute(text(sql), {"uid": user_id}).mappings().first()
        if row is None:
            return None
        return UserPrefsRow(**dict(row))

    def upsert_user_prefs(
        self,
        user_id: str,
        *,
        default_model: str | None = ...,  # type: ignore[assignment]
    ) -> None:
        """Insert-or-update ``user_prefs`` for ``user_id``.

        ``default_model`` uses a sentinel (``...``) to distinguish
        "not provided" from "explicitly set to None". In practice the
        router only ever passes it, but keeping the sentinel leaves room
        for M4 to add more pref fields without revisiting the signature.
        """
        existing = self.get_user_prefs(user_id)
        if existing is None:
            dm = None if default_model is ... else default_model
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO user_prefs (user_id, default_model, extras_json)
                        VALUES (:uid, :dm, NULL)
                        """
                    ),
                    {"uid": user_id, "dm": dm},
                )
            return
        if default_model is ...:
            return  # nothing to update
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE user_prefs SET default_model = :dm WHERE user_id = :uid"
                ),
                {"uid": user_id, "dm": default_model},
            )

    # ------------------------------------------------------------------
    # Uploads (M4 task 4.3)
    # ------------------------------------------------------------------
    def insert_upload(
        self,
        *,
        thread_id: str,
        user_id: str | None,
        filename: str,
        size: int,
        content_type: str | None,
    ) -> str:
        """Insert an ``uploads`` row and return its generated id."""
        new_id = f"up_{uuid.uuid4().hex[:12]}"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO uploads (id, thread_id, user_id, filename,
                        size, content_type)
                    VALUES (:id, :tid, :uid, :fn, :sz, :ct)
                    """
                ),
                {
                    "id": new_id,
                    "tid": thread_id,
                    "uid": user_id,
                    "fn": filename,
                    "sz": size,
                    "ct": content_type,
                },
            )
        return new_id

    def list_uploads_for_thread(self, thread_id: str) -> list[UploadRow]:
        """All upload rows for ``thread_id``, newest first.

        Tiebreaker on ``id DESC`` keeps ordering deterministic when two
        rows share a ``created_at`` value (SQLite's default precision
        is 1s, and back-to-back inserts in tests do tie).
        """
        sql = (
            "SELECT id, thread_id, user_id, filename, size, content_type, "
            "created_at FROM uploads WHERE thread_id = :tid "
            "ORDER BY created_at DESC, id DESC"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), {"tid": thread_id}).mappings().all()
        return [UploadRow(**dict(r)) for r in rows]

    def get_upload(self, upload_id: str) -> UploadRow | None:
        sql = (
            "SELECT id, thread_id, user_id, filename, size, content_type, "
            "created_at FROM uploads WHERE id = :id"
        )
        with self.engine.connect() as conn:
            row = conn.execute(text(sql), {"id": upload_id}).mappings().first()
        if row is None:
            return None
        return UploadRow(**dict(row))

    def delete_upload(self, upload_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM uploads WHERE id = :id"),
                {"id": upload_id},
            )
