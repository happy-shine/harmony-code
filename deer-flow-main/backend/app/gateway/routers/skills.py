"""Skill install + CRUD backed by ``harmony.db``.

Install sources:

* zip upload via multipart form-data at ``POST /api/skills/upload``
* git clone via JSON body at ``POST /api/skills/git``

Two endpoints rather than one branching on content-type: the request
shapes (multipart vs JSON) and the Pydantic/OpenAPI contracts are
cleaner separate — the frontend picks based on the install dialog, not
based on request dispatch.

Enabled rows are symlinked into ``<thread>/.claude/skills/`` on every CC
spawn by :func:`app.cc_adapter.compose.compose_skills_dir`, so edits through
these endpoints take effect on the next message with no caching layer to
invalidate.

M3 scope: ``user_id`` is stubbed to ``"u_default"`` via
:func:`app.gateway.deps.current_user_id`; M5 wires real auth.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.db import SkillRow
from app.gateway.deps import current_user_id, get_db
from app.skills.installer import (
    SkillInstallError,
    install_from_git,
    install_from_zip,
    parse_skill_name,
    uninstall,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _data_dir() -> Path:
    return Path(os.environ.get("HARMONY_DATA_DIR", ".harmony-data"))


# --- Models ---------------------------------------------------------------


class SkillOut(BaseModel):
    id: str
    user_id: str | None
    name: str
    source: str  # "upload" | "git"
    path: str
    enabled: bool


class SkillPatch(BaseModel):
    """Partial update. Only ``name`` + ``enabled`` are editable;
    ``source`` / ``path`` are install-time artifacts — reinstall to change."""

    name: str | None = None
    enabled: bool | None = None


class GitInstallBody(BaseModel):
    url: str = Field(
        ...,
        description=(
            "HTTPS or git@ URL of a repository whose root contains SKILL.md"
        ),
    )
    name: str | None = Field(
        None,
        description=(
            "Optional override for the skill name (defaults to "
            "SKILL.md front-matter)"
        ),
    )


def _row_to_out(r: SkillRow) -> SkillOut:
    return SkillOut(
        id=r.id,
        user_id=r.user_id,
        name=r.name,
        source=r.source,
        path=r.path,
        enabled=r.enabled,
    )


# --- Routes ---------------------------------------------------------------


@router.get("", response_model=list[SkillOut])
def list_skills(user_id: str = Depends(current_user_id)) -> list[SkillOut]:
    return [
        _row_to_out(r)
        for r in get_db().list_skills_for_user(user_id=user_id)
    ]


@router.post(
    "/upload",
    response_model=SkillOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_skill(
    file: UploadFile = File(...),
    user_id: str = Depends(current_user_id),
) -> SkillOut:
    """Install a skill from a ``.zip`` upload.

    The zip must contain ``SKILL.md`` at its root, or in a single
    top-level directory — the wrapping directory is stripped (GitHub-archive
    convention).
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "file must be a .zip")
    try:
        _skill_id, skill_dir = install_from_zip(
            zip_stream=file.file, data_dir=_data_dir()
        )
    except SkillInstallError as e:
        raise HTTPException(400, str(e))
    finally:
        file.file.close()
    name = parse_skill_name(skill_dir)
    db = get_db()
    new_id = db.insert_skill(
        user_id=user_id, name=name, source="upload", path=str(skill_dir)
    )
    row = db.get_skill(new_id)
    if row is None:
        # Insert just succeeded; this would be a concurrent delete racing us.
        raise HTTPException(500, "inserted row not found")
    return _row_to_out(row)


@router.post(
    "/git",
    response_model=SkillOut,
    status_code=status.HTTP_201_CREATED,
)
def git_install_skill(
    body: GitInstallBody, user_id: str = Depends(current_user_id)
) -> SkillOut:
    """Install a skill by shallow-cloning ``body.url`` into ``skills_store``.

    Validation happens post-clone (``SKILL.md`` must exist at repo root);
    on any failure the partially-cloned directory is cleaned up.
    """
    try:
        _skill_id, skill_dir = install_from_git(
            url=body.url, data_dir=_data_dir()
        )
    except SkillInstallError as e:
        raise HTTPException(400, str(e))
    name = body.name or parse_skill_name(skill_dir)
    db = get_db()
    new_id = db.insert_skill(
        user_id=user_id, name=name, source="git", path=str(skill_dir)
    )
    row = db.get_skill(new_id)
    if row is None:
        raise HTTPException(500, "inserted row not found")
    return _row_to_out(row)


@router.patch("/{skill_id}", response_model=SkillOut)
def update_skill(
    skill_id: str,
    patch: SkillPatch,
    user_id: str = Depends(current_user_id),
) -> SkillOut:
    db = get_db()
    row = db.get_skill(skill_id)
    if row is None:
        raise HTTPException(404, "skill not found")
    # Global rows (user_id IS NULL) are visible in GET but cannot be
    # mutated by non-admins; cross-user rows likewise 403. Matches the
    # MCP router's convention.
    if row.user_id is None or row.user_id != user_id:
        raise HTTPException(403, "not yours")
    try:
        db.update_skill(skill_id, patch.model_dump(exclude_unset=True))
    except IntegrityError as e:
        # Unique index ``ix_skills_user_name(user_id, name)`` — rename
        # collision with another skill owned by the same user. SQLAlchemy's
        # ``text()`` raw queries wrap sqlite3's IntegrityError in this
        # class, so catching the SQLA type is sufficient.
        raise HTTPException(409, "skill name already exists") from e
    updated = db.get_skill(skill_id)
    assert updated is not None  # we just read it above under the same engine
    return _row_to_out(updated)


@router.delete("/{skill_id}")
def delete_skill(
    skill_id: str, user_id: str = Depends(current_user_id)
) -> dict[str, Any]:
    """Delete the DB row first (source of truth), then clean up the filesystem.

    If filesystem cleanup fails, we leak a directory in ``skills_store/``
    but the skill is correctly gone from ``compose_skills_dir``'s
    perspective — a leaked dir is harmless disk bloat until M5's GC
    sweep. The reverse ordering (rmtree then delete row) would leave a
    dangling DB row whose ``path`` points at nothing, producing broken
    symlinks on every subsequent CC spawn — strictly worse.
    """
    db = get_db()
    row = db.get_skill(skill_id)
    if row is None:
        raise HTTPException(404, "skill not found")
    if row.user_id is None or row.user_id != user_id:
        raise HTTPException(403, "not yours")
    db.delete_skill(skill_id)
    try:
        uninstall(skill_dir=Path(row.path))
    except Exception as e:
        # Row is already gone; a leaked dir is harmless until M5 gc.
        logger.warning(
            "skill row %s deleted but filesystem cleanup failed: %s",
            skill_id,
            e,
        )
    return {"ok": True}
