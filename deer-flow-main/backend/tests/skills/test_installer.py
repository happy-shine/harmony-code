"""Unit tests for :mod:`app.skills.installer` (no FastAPI, no DB).

Covers happy-path zip extraction (flat + single-root stripped),
zip-slip rejection, missing ``SKILL.md`` cleanup, the
``parse_skill_name`` YAML heuristics, and ``uninstall`` idempotency.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.skills.installer import (
    SkillInstallError,
    install_from_git,
    install_from_zip,
    parse_skill_name,
    uninstall,
)


def _make_zip(tree: dict[str, bytes], root: str | None = None) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in tree.items():
            key = f"{root}/{name}" if root else name
            zf.writestr(key, content)
    buf.seek(0)
    return buf


# --- install_from_zip -----------------------------------------------------


def test_install_from_zip_flat_structure(tmp_path):
    buf = _make_zip({"SKILL.md": b"---\nname: myskill\n---\nbody"})
    skill_id, skill_dir = install_from_zip(
        zip_stream=buf, data_dir=tmp_path
    )
    assert skill_id.startswith("sk_")
    assert skill_dir == tmp_path / "skills_store" / skill_id
    assert (skill_dir / "SKILL.md").read_text().startswith(
        "---\nname: myskill"
    )


def test_install_from_zip_single_root_stripped(tmp_path):
    buf = _make_zip(
        {"SKILL.md": b"---\nname: wrapped\n---\n", "sub/file.txt": b"x"},
        root="pkg-main",
    )
    skill_id, skill_dir = install_from_zip(
        zip_stream=buf, data_dir=tmp_path
    )
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "sub" / "file.txt").exists()
    # Wrapping directory was stripped; SKILL.md lives at the top level.
    assert not (skill_dir / "pkg-main").exists()


def test_install_from_zip_multi_root_not_stripped(tmp_path):
    # Two distinct roots — nothing is stripped; SKILL.md must be at the
    # archive top level for validation to pass.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", b"---\nname: twin\n---\n")
        zf.writestr("extras/readme.txt", b"hi")
    buf.seek(0)
    skill_id, skill_dir = install_from_zip(
        zip_stream=buf, data_dir=tmp_path
    )
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "extras" / "readme.txt").is_file()


def test_install_from_zip_missing_skill_md(tmp_path):
    buf = _make_zip({"README.md": b"hello"})
    with pytest.raises(SkillInstallError, match="SKILL.md"):
        install_from_zip(zip_stream=buf, data_dir=tmp_path)
    # Partial dir was cleaned up.
    assert not any((tmp_path / "skills_store").glob("sk_*"))


def test_install_from_zip_rejects_path_traversal(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", b"pwned")
    buf.seek(0)
    with pytest.raises(SkillInstallError, match="unsafe|escapes"):
        install_from_zip(zip_stream=buf, data_dir=tmp_path)
    # And nothing leaked outside the dest.
    assert not (tmp_path / "evil.txt").exists()


def test_install_from_zip_rejects_absolute_path(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("/etc/passwd", b"nope")
    buf.seek(0)
    with pytest.raises(SkillInstallError, match="unsafe|escapes"):
        install_from_zip(zip_stream=buf, data_dir=tmp_path)


# --- parse_skill_name -----------------------------------------------------


def test_parse_skill_name_plain(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: my_skill\n---\n")
    assert parse_skill_name(d) == "my_skill"


def test_parse_skill_name_double_quoted(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text('---\nname: "quoted name"\n---\n')
    assert parse_skill_name(d) == "quoted name"


def test_parse_skill_name_single_quoted(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: 'single q'\n---\n")
    assert parse_skill_name(d) == "single q"


def test_parse_skill_name_fallback_to_dirname(tmp_path):
    d = tmp_path / "foo_skill"
    d.mkdir()
    (d / "SKILL.md").write_text("no front matter")
    assert parse_skill_name(d) == "foo_skill"


def test_parse_skill_name_fallback_when_no_name_key(tmp_path):
    d = tmp_path / "nokey"
    d.mkdir()
    (d / "SKILL.md").write_text("---\ndescription: just a desc\n---\n")
    assert parse_skill_name(d) == "nokey"


# --- install_from_git -----------------------------------------------------


def test_install_from_git_rejects_bad_scheme(tmp_path):
    with pytest.raises(SkillInstallError, match="Unsupported"):
        install_from_git(url="ftp://evil/x.git", data_dir=tmp_path)


def test_install_from_git_rejects_leading_dash(tmp_path):
    # A URL starting with ``-`` would be caught by scheme rejection
    # regardless of the ``--`` terminator; belt-and-braces.
    with pytest.raises(SkillInstallError, match="Unsupported"):
        install_from_git(url="--upload-pack=evil", data_dir=tmp_path)


def test_install_from_git_clone_failure_cleans_up(tmp_path, monkeypatch):
    import subprocess

    def fake_run(cmd, **kwargs):  # noqa: ARG001 — match signature shape
        class R:
            returncode = 128
            stderr = "fatal: bad url"
            stdout = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SkillInstallError, match="git clone failed"):
        install_from_git(
            url="https://example/repo.git", data_dir=tmp_path
        )
    # Partial dir cleaned up so a retry can land on the same id-space.
    assert not any((tmp_path / "skills_store").glob("sk_*"))


def test_install_from_git_accepts_https_and_ssh(tmp_path, monkeypatch):
    """Happy-path URL schemes pass scheme validation and reach git."""
    import subprocess

    called_urls: list[str] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        called_urls.append(cmd[cmd.index("--") + 1])

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        # Stamp the clone target with a valid SKILL.md so validation passes.
        # cmd[-1] is the destination path (after ``--``).
        dest = Path(cmd[-1])
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text("---\nname: cloned\n---\n")
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    for url in ("https://host/repo.git", "git@host:org/repo.git"):
        # Each call needs its own data_dir — the skill_id collides across
        # iterations only via uuid, so reuse the same dir is fine here.
        install_from_git(url=url, data_dir=tmp_path)
    assert called_urls == [
        "https://host/repo.git",
        "git@host:org/repo.git",
    ]


def test_install_from_git_redacts_url_credentials_in_error(
    tmp_path, monkeypatch
):
    """Error message must not echo the URL (which may embed user:password@)."""
    import subprocess

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        class R:
            returncode = 128
            stderr = (
                "fatal: could not read from "
                "https://alice:hunter2@example.com/x.git"
            )
            stdout = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    url = "https://alice:hunter2@example.com/x.git"
    with pytest.raises(SkillInstallError) as exc_info:
        install_from_git(url=url, data_dir=tmp_path)
    msg = str(exc_info.value)
    # The password MUST be gone. The username MUST NOT appear verbatim
    # unless accompanied by a placeholder marker (``<url>`` or
    # ``<redacted>``).
    assert "hunter2" not in msg
    assert "alice" not in msg or "<" in msg


def test_install_from_zip_rejects_symlink_entry(tmp_path):
    """Zip entries marked as symlinks (unix mode S_IFLNK) must be rejected.

    Even though the current extractor treats them as regular files, a
    future refactor to ``zf.extract()`` would happily create a real
    symlink — so we reject at the source.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("SKILL.md")
        info.create_system = 3  # unix
        # S_IFLNK (0o120000) OR permission bits, stored in the high
        # half-word of external_attr.
        info.external_attr = (0o120777 & 0xFFFF) << 16
        zf.writestr(info, "/etc/passwd")
    buf.seek(0)
    with pytest.raises(SkillInstallError, match="symlink"):
        install_from_zip(zip_stream=buf, data_dir=tmp_path)


def test_install_from_git_missing_skill_md_cleans_up(tmp_path, monkeypatch):
    """Successful clone without SKILL.md is rejected and dir removed."""
    import subprocess

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        dest = Path(cmd[-1])
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "README.md").write_text("no skill file")
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SkillInstallError, match="SKILL.md"):
        install_from_git(
            url="https://host/repo.git", data_dir=tmp_path
        )
    assert not any((tmp_path / "skills_store").glob("sk_*"))


# --- uninstall ------------------------------------------------------------


def test_uninstall_idempotent(tmp_path):
    d = tmp_path / "gone"
    # Doesn't exist yet — must not raise.
    uninstall(skill_dir=d)
    d.mkdir()
    (d / "f").write_text("x")
    uninstall(skill_dir=d)
    assert not d.exists()
