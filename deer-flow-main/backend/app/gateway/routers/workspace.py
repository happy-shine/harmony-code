"""Task 4.1: Workspace router — file tree + download for ``row.cwd``.

Exposes the per-thread CC working directory (set by
``/api/threads`` at create time and persisted in ``SessionStore``) to
the frontend. Two endpoints:

* ``GET /api/threads/{tid}/workspace/tree`` — recursive directory listing.
* ``GET /api/threads/{tid}/workspace/files/{path:path}`` — streamed file.

All filesystem paths are resolved via :func:`_safe_resolve`, which rejects
anything that escapes ``cwd`` after symlink resolution. The resolved-path
containment check uses :meth:`pathlib.Path.is_relative_to` (Python 3.9+)
to avoid the classic sibling-prefix bug (``/tmp/work`` vs
``/tmp/work-evil/x``).
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.gateway.deps import session_store as _store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads/{tid}/workspace")


# Traversal limits. Symlink loops and pathologically-deep trees should
# surface as a 413, not OOM the gateway.
_MAX_DEPTH = 20
_MAX_NODES = 10_000


def _thread_root(tid: str) -> Path:
    """Resolve ``tid`` → absolute cwd, or raise 404."""
    row = _store().get(tid)
    if row is None:
        raise HTTPException(status_code=404, detail="thread_not_found")
    cwd = Path(row.cwd)
    if not cwd.exists():
        # Row exists but the directory was removed out-of-band. Treat as
        # an empty workspace for tree (caller gets []); for file downloads
        # the missing-file branch handles it.
        cwd.mkdir(parents=True, exist_ok=True)
    return cwd


def _safe_resolve(cwd: Path, rel: str) -> Path:
    """Join ``rel`` onto ``cwd`` and resolve symlinks, rejecting escapes.

    Raises :class:`HTTPException` 400 if:
      * ``rel`` contains a NUL byte,
      * the resolved path falls outside ``cwd`` (after symlink follow).

    Accepts absolute ``rel`` input because FastAPI's ``{path:path}`` may
    deliver ``/etc/passwd``; ``(cwd / "/etc/passwd")`` returns
    ``/etc/passwd`` in Python, and the ``is_relative_to`` check catches
    it. Tested explicitly.
    """
    if "\x00" in rel:
        raise HTTPException(status_code=400, detail="null_byte_in_path")
    cwd_resolved = cwd.resolve()
    try:
        full = (cwd / rel).resolve()
    except (OSError, RuntimeError) as e:
        # RuntimeError: symlink loop. OSError: something weirder.
        logger.debug("safe_resolve rejected %r: %r", rel, e)
        raise HTTPException(status_code=400, detail="path_unresolvable") from e
    if not full.is_relative_to(cwd_resolved):
        raise HTTPException(status_code=400, detail="path_out_of_scope")
    return full


def _node(entry: Path, rel_posix: str) -> dict:
    """Build one tree node from a Path. ``rel_posix`` is the relative path
    with forward-slash separators (POSIX) regardless of host OS."""
    is_dir = entry.is_dir()
    out: dict = {
        "name": entry.name,
        "path": rel_posix,
        "type": "dir" if is_dir else "file",
    }
    if not is_dir:
        try:
            st = entry.stat()
            out["size"] = st.st_size
            out["mtime"] = st.st_mtime
        except OSError:
            # Disappeared mid-traversal; fall back to zero.
            out["size"] = 0
            out["mtime"] = 0.0
    return out


def _walk(cwd: Path) -> list[dict]:
    """Depth-bounded recursive walk.

    Returns a nested list of nodes. Skips:
      * symlinks that escape ``cwd`` (defense in depth alongside
        ``_safe_resolve`` for direct-GET paths),
      * entries that error on stat (transient filesystem races).

    Raises 413 if node count exceeds :data:`_MAX_NODES`.
    """
    cwd_resolved = cwd.resolve()
    count = 0

    def _children(parent: Path, rel_prefix: str, depth: int) -> list[dict]:
        nonlocal count
        if depth >= _MAX_DEPTH:
            return []
        try:
            entries = sorted(parent.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except OSError:
            return []
        out: list[dict] = []
        for entry in entries:
            # Symlink containment check: resolve the link target and skip
            # anything that lands outside cwd. Path.resolve(strict=False)
            # follows links in CPython; symlinks to nonexistent targets
            # return the would-be-path.
            try:
                resolved = entry.resolve()
            except (OSError, RuntimeError):
                continue
            if not resolved.is_relative_to(cwd_resolved):
                continue
            count += 1
            if count > _MAX_NODES:
                raise HTTPException(status_code=413, detail="workspace_tree_too_large")
            rel = f"{rel_prefix}{entry.name}" if not rel_prefix else f"{rel_prefix}/{entry.name}"
            node = _node(entry, rel)
            if node["type"] == "dir":
                node["children"] = _children(entry, rel, depth + 1)
            out.append(node)
        return out

    return _children(cwd, "", 0)


@router.get("/tree")
def get_tree(tid: str) -> dict:
    """Return the workspace file tree rooted at ``row.cwd``."""
    cwd = _thread_root(tid)
    return {
        "root": str(cwd),
        "children": _walk(cwd),
    }


@router.get("/files/{path:path}")
def get_file(tid: str, path: str) -> FileResponse:
    """Stream a file under ``row.cwd``. Path-escape rejected with 400."""
    cwd = _thread_root(tid)
    full = _safe_resolve(cwd, path)
    if not full.exists():
        raise HTTPException(status_code=404, detail="file_not_found")
    if not full.is_file():
        raise HTTPException(status_code=400, detail="not_a_file")
    mime, _ = mimetypes.guess_type(full.name)
    # FileResponse handles Range requests and ETag/Last-Modified for us.
    return FileResponse(
        path=str(full),
        media_type=mime or "application/octet-stream",
        filename=full.name,
    )
