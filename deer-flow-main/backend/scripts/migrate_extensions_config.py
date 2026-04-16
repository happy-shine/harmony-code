"""One-time migration of legacy ``extensions_config.json`` into ``harmony.db``.

The pre-harmony deerflow backend tracked MCP servers and skill enabled-state
in a single JSON file (``extensions_config.json``, or legacy
``mcp_config.json``). M3 moves that configuration into the ``mcp_servers``
and ``skills`` tables of ``harmony.db`` and exposes CRUD routers against it.

This script imports the legacy file **once** so existing users don't lose
their MCP setup when upgrading. It is idempotent on re-run.

Decisions:

* **Global rows only.** The legacy config has no notion of users; everything
  is imported with ``user_id = NULL``.
* **Skills are logged but not inserted.** The legacy config only tracks
  skill *names* plus enabled-state — skill *files* lived in a separate
  deerflow-specific tree that M3 does not import. Inserting skill rows
  without valid ``path`` values would produce broken symlinks in
  :func:`app.cc_adapter.compose.compose_skills_dir`.
"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import text


logger = logging.getLogger("migrate_extensions_config")


def _mcp_global_row_exists(db, name: str) -> bool:
    """Return True if a global (``user_id IS NULL``) mcp_servers row with
    ``name`` already exists.

    Needed because SQLite's UNIQUE index treats ``NULL`` as distinct per
    the SQL standard — so the ``ix_mcp_user_name`` index does NOT block
    duplicate global rows. Pre-checking here is the cheapest way to stay
    idempotent.
    """
    with db.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM mcp_servers "
                "WHERE user_id IS NULL AND name = :name LIMIT 1"
            ),
            {"name": name},
        ).first()
    return row is not None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate legacy extensions_config.json into harmony.db.",
    )
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def migrate(*, config_path: str | None = None, dry_run: bool = False) -> int:
    """Run the migration. Returns the number of rows newly inserted."""
    from packages.harness.deerflow.config.extensions_config import (
        ExtensionsConfig,
    )

    try:
        cfg = ExtensionsConfig.from_file(config_path)
    except (FileNotFoundError, RuntimeError) as exc:
        # RuntimeError here wraps the FileNotFoundError raised when an
        # explicit --config path doesn't exist (ExtensionsConfig.from_file
        # converts most errors into RuntimeError). Treat either as "no
        # config to migrate" — one-time tool, user-friendly.
        cause = exc.__cause__ if isinstance(exc, RuntimeError) else exc
        if isinstance(cause, FileNotFoundError) or isinstance(exc, FileNotFoundError):
            logger.info("no extensions config found, nothing to migrate")
            return 0
        raise

    mcp_count = len(cfg.mcp_servers)
    skills_count = len(cfg.skills)
    logger.info(
        "loaded extensions config: %d MCP servers, %d skills",
        mcp_count,
        skills_count,
    )

    if mcp_count == 0:
        return 0

    from app.db import Db, get_engine

    db = Db(get_engine())

    inserted = 0
    for name, server in cfg.mcp_servers.items():
        if _mcp_global_row_exists(db, name):
            logger.info(
                "MCP %r already present as a global row, skipping", name
            )
            continue
        if dry_run:
            logger.info(
                "[dry-run] would insert MCP %r (transport=%s)",
                name,
                server.type,
            )
            continue
        db.insert_mcp(
            user_id=None,
            name=name,
            transport=server.type,
            command=server.command,
            args=list(server.args) if server.args else None,
            url=server.url,
            headers=dict(server.headers) if server.headers else None,
            env=dict(server.env) if server.env else None,
            enabled=server.enabled,
        )
        inserted += 1
        logger.info("inserted MCP %r (transport=%s)", name, server.type)

    return inserted


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)
    try:
        migrate(config_path=args.config, dry_run=args.dry_run)
    except Exception as exc:
        logger.error("migration failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
