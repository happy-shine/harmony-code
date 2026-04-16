"""Unit tests for :mod:`app.skills.installer` (no FastAPI, no DB).

Covers happy-path zip extraction (flat + single-root stripped),
zip-slip rejection, missing ``SKILL.md`` cleanup, the
``parse_skill_name`` YAML heuristics, and ``uninstall`` idempotency.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.skills.installer import (
    SkillInstallError,
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


# --- uninstall ------------------------------------------------------------


def test_uninstall_idempotent(tmp_path):
    d = tmp_path / "gone"
    # Doesn't exist yet — must not raise.
    uninstall(skill_dir=d)
    d.mkdir()
    (d / "f").write_text("x")
    uninstall(skill_dir=d)
    assert not d.exists()
