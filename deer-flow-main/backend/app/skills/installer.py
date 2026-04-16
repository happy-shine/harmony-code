"""Skill install helpers.

Extract a zip upload (or git-clone in the second commit) into
``<HARMONY_DATA_DIR>/skills_store/{id}/``, validate the layout, and return
``(skill_id, skill_dir)`` so the caller can insert the matching DB row.

Keeping filesystem work out of the router makes the installer trivially
unit-testable against ``tmp_path`` and keeps the DB ↔ disk contract narrow:
the installer owns the directory, the DB row stores its path.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)


class SkillInstallError(Exception):
    """Raised when installation fails (bad zip, missing ``SKILL.md``, etc.)."""


def _new_skill_id() -> str:
    return f"sk_{uuid.uuid4().hex[:12]}"


def _validate_skill_dir(skill_dir: Path) -> None:
    """Every skill must have a top-level ``SKILL.md``. Reject otherwise."""
    if not (skill_dir / "SKILL.md").is_file():
        raise SkillInstallError(f"Skill at {skill_dir} is missing SKILL.md at the top level")


def _redact_git_stderr(stderr: str, url: str) -> str:
    """Remove credentials from git error output before surfacing to HTTP response.

    Git's stderr commonly echoes the full clone URL (``fatal: could not
    read from https://user:pass@host/x.git``). If the caller submitted a
    URL with embedded credentials, that secret would leak into both the
    400 response body and the gateway access log. Scrub it here so the
    exception — and therefore the HTTP response — carries only a
    placeholder. The raw stderr is still logged at DEBUG for operators.
    """
    # Replace literal URL with placeholder
    redacted = stderr.replace(url, "<url>")
    # Defensive: strip any user:password@ pattern git may have rewritten
    # (e.g. after protocol upgrade), belt-and-braces.
    redacted = re.sub(
        r"(https?://)[^/@\s]+:[^/@\s]+@",
        r"\1<redacted>@",
        redacted,
    )
    return redacted


def _zip_safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract ``zf`` into ``dest``, rejecting zip-slip entries.

    Strips a single leading path component if the archive is wrapped in
    one top-level directory (the GitHub-archive convention). If the zip
    has multiple top-level entries, nothing is stripped.
    """
    names = [n for n in zf.namelist() if n]
    # Reject absolute or traversal entries up-front so they can't mask
    # themselves as a "single root" (an archive whose only root segment is
    # ``..`` would otherwise look single-rooted and get its ``../`` stripped).
    for n in names:
        if n.startswith("/") or ".." in Path(n).parts:
            raise SkillInstallError(f"Zip entry {n!r} has unsafe path")

    # Detect single-root: every entry shares a first path segment.
    roots = {n.split("/", 1)[0] for n in names}
    strip_root = False
    root_prefix = ""
    if len(roots) == 1:
        only = next(iter(roots))
        # A single-root archive either has entries equal to ``only`` (the
        # directory entry itself) or starting with ``only/``.
        if all(n == only or n.startswith(f"{only}/") for n in names):
            strip_root = True
            root_prefix = f"{only}/"

    dest = dest.resolve()
    for member in zf.infolist():
        name = member.filename
        if strip_root and name.startswith(root_prefix):
            name = name[len(root_prefix) :]
        if not name:
            continue
        # Reject symlink entries explicitly. A zip stores a symlink with
        # unix mode ``0o120000`` in the high bits of ``external_attr``;
        # the current ``zf.open(member)`` path treats it as a regular
        # file and writes the link *target string* as file bytes —
        # harmless today, but a future refactor to ``zf.extract()``
        # would silently start creating real symlinks that could point
        # at ``/etc/passwd``. Guard the invariant here.
        file_mode = member.external_attr >> 16
        if file_mode & 0o170000 == 0o120000:  # S_IFLNK
            raise SkillInstallError(f"Zip entry {member.filename!r} is a symlink; not supported")
        target = (dest / name).resolve()
        # Final belt-and-braces check: resolved target must live under dest.
        try:
            target.relative_to(dest)
        except ValueError:
            raise SkillInstallError(f"Zip entry {member.filename!r} escapes destination")
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def install_from_zip(*, zip_stream: BinaryIO, data_dir: Path) -> tuple[str, Path]:
    """Extract a zip into ``skills_store/{id}/`` and validate ``SKILL.md``.

    The caller is responsible for the DB insert; this function is
    filesystem-only. On validation failure the partially-extracted
    directory is cleaned up so nothing leaks behind.
    """
    skill_id = _new_skill_id()
    skill_dir = data_dir / "skills_store" / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_stream) as zf:
            _zip_safe_extract(zf, skill_dir)
        _validate_skill_dir(skill_dir)
    except Exception:
        shutil.rmtree(skill_dir, ignore_errors=True)
        raise
    return skill_id, skill_dir


def install_from_git(*, url: str, data_dir: Path, timeout: int = 60) -> tuple[str, Path]:
    """Shallow-clone a git repo into ``skills_store/{id}/``.

    Uses ``git clone --depth 1 -- <url> <dest>``. The ``--`` terminator
    prevents a URL starting with ``-`` from being parsed as a flag;
    ``subprocess.run`` without ``shell=True`` avoids shell expansion
    entirely. ``timeout`` bounds a hung clone so the gateway can never
    block indefinitely on a slow remote.
    """
    if not (url.startswith("https://") or url.startswith("http://") or url.startswith("git@")):
        raise SkillInstallError(f"Unsupported git URL scheme: {url!r}")
    skill_id = _new_skill_id()
    skill_dir = data_dir / "skills_store" / skill_id
    # Don't pre-create skill_dir — ``git clone`` wants the target to not
    # exist (otherwise it errors) unless the dir is empty. We create the
    # parent ``skills_store/`` but leave the leaf to the clone.
    skill_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--", url, str(skill_dir)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            # Log full unredacted stderr for operator diagnosis; the
            # exception (and therefore the HTTP response) only carries
            # the scrubbed version so credentials in the URL don't leak.
            logger.debug("git clone stderr (unredacted): %s", result.stderr)
            sanitized = _redact_git_stderr(result.stderr.strip(), url)[-500:]
            raise SkillInstallError(f"git clone failed (exit {result.returncode}): {sanitized}")
        _validate_skill_dir(skill_dir)
    except Exception:
        shutil.rmtree(skill_dir, ignore_errors=True)
        raise
    return skill_id, skill_dir


def parse_skill_name(skill_dir: Path) -> str:
    """Read ``SKILL.md`` front-matter to get the skill name.

    Falls back to the directory name if no front-matter / no ``name`` key.
    Accepts both ``name: foo`` and ``name: "foo"`` (single or double
    quotes stripped).
    """
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^---\s*$(.*?)^---\s*$", text, re.DOTALL | re.MULTILINE)
    if m:
        for line in m.group(1).splitlines():
            line = line.strip()
            if line.startswith("name:"):
                val = line[len("name:") :].strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                if val:
                    return val
    return skill_dir.name


def uninstall(*, skill_dir: Path) -> None:
    """Remove the skill directory from ``skills_store``. No-op if absent."""
    shutil.rmtree(skill_dir, ignore_errors=True)
