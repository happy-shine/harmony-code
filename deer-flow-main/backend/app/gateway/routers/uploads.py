"""Per-thread uploads router — writes to ``<thread>/user-data/uploads/``.

Task 4.3 rewrite. The previous LangGraph-era implementation (sandbox sync,
markdown conversion, virtual_path) is gone; M5 will delete the entire
``deerflow.uploads.manager`` it used to depend on.

Endpoints:

* ``POST /api/threads/{tid}/uploads`` — multipart upload of one or more files.
  Writes each file to ``<HARMONY_DATA_DIR>/threads/<tid>/user-data/uploads/``
  and inserts a row into the ``uploads`` table. The directory is pre-created
  by ``POST /api/threads`` (see :mod:`app.gateway.routers.messages`), so we
  only need to ``mkdir(parents=True, exist_ok=True)`` defensively here.
  CC sees this dir on every spawn via ``SpawnConfig.add_dirs`` — uploaded
  files are read-available to the agent from the next message onward.
* ``GET /api/threads/{tid}/uploads`` — list rows for the thread, newest first.
* ``DELETE /api/threads/{tid}/uploads/{upload_id}`` — delete row + on-disk
  file. DB-first: row is removed before the unlink, mirroring the skills
  router (Task 3.5). If the unlink fails (file already gone, permission),
  we log and still return ok — the row is the source of truth.

Authorization: M3 stub — ``user_id`` is the ``"u_default"`` returned by
:func:`app.gateway.deps.current_user_id`. ``user_id`` is forward-compat only;
today it's recorded on insert but we filter by ``thread_id`` alone (the
thread itself is the access-control boundary until M5 wires real auth).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.db import UploadRow
from app.gateway.deps import current_user_id, data_dir, get_db, session_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads/{tid}/uploads", tags=["uploads"])


# 100 MB default cap. Overridable in tests by monkeypatching the module
# attribute (see ``test_upload_too_large_413``).
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# Chunk size for streaming reads when checking the size cap. 1 MB is a
# comfortable balance — small enough that a 100 MB cap bails out well
# before the whole file is buffered, large enough to keep the syscall
# count low.
_CHUNK = 1024 * 1024

# Absolute path markers. ``os.path.isabs`` is POSIX-only for what we care
# about; checking the leading char directly covers POSIX and Windows
# drive-letter paths uniformly and avoids platform-dependent behavior.
_FILENAME_MAX_LEN = 255


class UploadOut(BaseModel):
    """Response shape for a single upload row. ``created_at`` is absent
    on the POST response (we haven't read it back from the DB) and
    present on GET — the field is optional to accommodate both."""

    id: str
    filename: str
    size: int
    content_type: str | None = None
    created_at: str | None = None


def _uploads_dir(tid: str) -> Path:
    """Per-thread uploads directory. Sibling of ``workspace/`` under
    ``<HARMONY_DATA_DIR>/threads/<tid>/user-data/``."""
    return data_dir() / "threads" / tid / "user-data" / "uploads"


def _normalize_filename(name: str | None) -> str:
    """Return ``name`` iff safe to use as a filename under ``uploads/``.

    Rejects (raises :class:`ValueError`):

    * empty / None / whitespace-only,
    * ``.`` / ``..`` (directory refs),
    * anything containing ``/`` or ``\\`` (path separators on any host),
    * anything containing a NUL byte,
    * names longer than 255 chars.

    We deliberately do NOT try to "sanitize" unsafe names — silently
    rewriting ``../evil`` to ``evil`` is worse UX than a 400, because
    it masks a client bug. The corresponding HTTP detail is
    ``invalid_filename`` (single code, set at the raise site).
    """
    if not name:
        raise ValueError("invalid_filename")
    if "\x00" in name:
        raise ValueError("invalid_filename")
    # Strip only leading/trailing whitespace; internal whitespace is fine.
    stripped = name.strip()
    if not stripped or stripped in (".", ".."):
        raise ValueError("invalid_filename")
    if "/" in stripped or "\\" in stripped:
        raise ValueError("invalid_filename")
    if len(stripped) > _FILENAME_MAX_LEN:
        raise ValueError("invalid_filename")
    return stripped


def _thread_exists(tid: str) -> bool:
    """A thread "exists" iff its ``cc_thread_session`` row is present.

    We don't check the directory: ``create_thread`` creates both the row
    and the dir atomically (well, sequentially — the dir first). A
    missing dir for an existing row means someone nuked it out-of-band,
    which we tolerate (``mkdir(exist_ok=True)`` at write time).
    """
    return session_store().get(tid) is not None


def _row_to_out(r: UploadRow, *, include_created_at: bool = True) -> UploadOut:
    # ``created_at`` arrives as a ``datetime`` when the DB driver has typed
    # conversion wired up, or as a raw string from SQLite's text()
    # interface. Handle both: isoformat on the datetime, str() on anything
    # else (including None → None).
    ca: str | None
    if not include_created_at or r.created_at is None:
        ca = None
    elif hasattr(r.created_at, "isoformat"):
        ca = r.created_at.isoformat()
    else:
        ca = str(r.created_at)
    return UploadOut(
        id=r.id,
        filename=r.filename,
        size=r.size,
        content_type=r.content_type,
        created_at=ca,
    )


# --- Routes ---------------------------------------------------------------


@router.post("", response_model=list[UploadOut])
async def upload_files(
    tid: str,
    files: list[UploadFile] = File(...),
    user_id: str = Depends(current_user_id),
) -> list[UploadOut]:
    """Persist each uploaded file and insert a ``uploads`` row.

    All-or-nothing semantics would be nicer, but multipart streaming
    doesn't rewind cheaply — we validate filenames up front (before
    writing any bytes), then stream each body to disk with a size
    check. If a later file fails size, the earlier files on disk
    remain; the caller can DELETE them via the returned ids.
    """
    if not _thread_exists(tid):
        raise HTTPException(404, "thread_not_found")
    if not files:
        raise HTTPException(400, "no_files")

    # Validate every filename before touching the filesystem. Cheaper than
    # partial rollback on a bad 5th-of-5.
    safe_names: list[str] = []
    for f in files:
        try:
            safe_names.append(_normalize_filename(f.filename))
        except ValueError:
            raise HTTPException(400, "invalid_filename")

    target_dir = _uploads_dir(tid)
    target_dir.mkdir(parents=True, exist_ok=True)

    db = get_db()
    out: list[UploadOut] = []
    for f, safe_name in zip(files, safe_names):
        # Stream to disk with a size cap. Starlette's ``UploadFile`` uses
        # a SpooledTemporaryFile under the hood; large bodies already sit
        # in a temp file and we want to avoid buffering a second copy in
        # memory. Check the total as we go and bail on overshoot.
        dest = target_dir / safe_name
        total = 0
        try:
            with dest.open("wb") as fh:
                while True:
                    chunk = await f.read(_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        raise HTTPException(413, "upload_too_large")
                    fh.write(chunk)
        except HTTPException:
            raise
        except Exception as e:  # pragma: no cover - filesystem edge case
            # Clean up a partial write before bubbling.
            logger.error("upload write failed for %s: %r", safe_name, e)
            dest.unlink(missing_ok=True)
            raise HTTPException(500, "upload_write_failed")

        new_id = db.insert_upload(
            thread_id=tid,
            user_id=user_id,
            filename=safe_name,
            size=total,
            content_type=f.content_type,
        )
        out.append(
            UploadOut(
                id=new_id,
                filename=safe_name,
                size=total,
                content_type=f.content_type,
                created_at=None,  # not read back on POST
            )
        )
    return out


@router.get("", response_model=list[UploadOut])
def list_uploads(
    tid: str,
    user_id: str = Depends(current_user_id),  # noqa: ARG001 - future auth scope
) -> list[UploadOut]:
    if not _thread_exists(tid):
        raise HTTPException(404, "thread_not_found")
    rows = get_db().list_uploads_for_thread(tid)
    return [_row_to_out(r) for r in rows]


@router.delete("/{upload_id}")
def delete_upload(
    tid: str,
    upload_id: str,
    user_id: str = Depends(current_user_id),  # noqa: ARG001 - future auth scope
) -> dict[str, Any]:
    """Delete DB row + on-disk file. DB-first: filesystem failure is logged.

    Mirrors the skills-router pattern (Task 3.5): the row is the source of
    truth for what belongs to the thread, and leaving a dangling file on
    disk is strictly better than leaving a dangling row that points at
    nothing (which would confuse list + re-uploads with the same name).
    """
    if not _thread_exists(tid):
        raise HTTPException(404, "thread_not_found")
    db = get_db()
    row = db.get_upload(upload_id)
    # ``thread_id`` scopes the lookup: a row that exists but belongs to
    # a different thread is a 404, not a 403. Treating it as 404 avoids
    # leaking "id X exists" information across threads.
    if row is None or row.thread_id != tid:
        raise HTTPException(404, "upload_not_found")

    db.delete_upload(upload_id)

    dest = _uploads_dir(tid) / row.filename
    try:
        dest.unlink()
    except FileNotFoundError:
        logger.warning(
            "upload %s row deleted but file %s already missing",
            upload_id,
            dest,
        )
    except Exception as e:  # pragma: no cover - filesystem edge case
        logger.warning(
            "upload %s row deleted but filesystem cleanup failed: %r",
            upload_id,
            e,
        )
    return {"ok": True}
