"""Compose per-spawn CC config from the deer-flow DB.

On each claude-code CLI spawn the gateway needs two derived artifacts:

* an ``--mcp-config`` JSON file listing the MCP servers enabled for the
  thread's user, and
* a ``.claude/skills`` directory populated with symlinks pointing at the
  source directory of each enabled skill (CC discovers skills by scanning
  that folder).

Both live next to the per-thread workspace and are recomposed on every
spawn so that DB edits take effect without a long-lived cache.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from app.db import Db


def compose_mcp_config(*, db: Db, user_id: str, thread_id: str, tmp_root: Path) -> Path:
    """Write a ``{"mcpServers": {...}}`` JSON file for this spawn.

    Queries enabled MCP rows for ``user_id`` (plus global rows where
    ``user_id IS NULL``), renders each into the CC-expected shape, writes
    the combined config under ``tmp_root``, and returns the output path.
    """
    rows = db.query_mcp_for_user(user_id=user_id, enabled_only=True)
    servers: dict[str, dict] = {}
    for r in rows:
        entry: dict = {}
        if r.transport == "stdio":
            if r.command is None:
                raise ValueError(f"stdio MCP {r.name!r} missing command")
            entry["command"] = r.command
            if r.args_json:
                entry["args"] = json.loads(r.args_json)
        else:
            if r.url is None:
                raise ValueError(f"{r.transport} MCP {r.name!r} missing url")
            entry["url"] = r.url
            if r.headers_json:
                entry["headers"] = json.loads(r.headers_json)
        if r.env_json:
            entry["env"] = json.loads(r.env_json)
        servers[r.name] = entry

    out_path = tmp_root / f"mcp-{thread_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps({"mcpServers": servers}))
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, out_path)
    return out_path


def compose_skills_dir(*, db: Db, user_id: str, skills_dir: Path) -> None:
    """(Re)create ``skills_dir`` and symlink each enabled skill into it.

    Any existing directory at ``skills_dir`` is removed first so stale
    symlinks from a previous spawn can't leak in.
    """
    if skills_dir.exists():
        shutil.rmtree(skills_dir)
    skills_dir.mkdir(parents=True)
    rows = db.query_skills_for_user(user_id=user_id, enabled_only=True)
    for r in rows:
        (skills_dir / r.name).symlink_to(r.path)
