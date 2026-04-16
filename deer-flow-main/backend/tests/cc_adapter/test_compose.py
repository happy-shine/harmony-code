"""Tests for ``app.cc_adapter.compose``."""
from __future__ import annotations

import json
from pathlib import Path

from app.cc_adapter.compose import compose_mcp_config, compose_skills_dir


def test_compose_mcp_config_combines_user_and_global(tmp_path, db_with_rows):
    db_with_rows.insert_mcp(
        user_id="u1",
        name="personal",
        transport="stdio",
        command="echo",
        args=["a"],
        env={"X": "1"},
    )
    db_with_rows.insert_mcp(
        user_id=None,
        name="team_fs",
        transport="stdio",
        command="npx",
        args=["-y", "fs"],
    )
    out = compose_mcp_config(
        db=db_with_rows, user_id="u1", thread_id="t_abc", tmp_root=tmp_path
    )
    data = json.loads(Path(out).read_text())
    assert set(data["mcpServers"].keys()) == {"personal", "team_fs"}
    assert data["mcpServers"]["personal"]["env"] == {"X": "1"}
    assert data["mcpServers"]["personal"]["command"] == "echo"
    assert data["mcpServers"]["personal"]["args"] == ["a"]


def test_compose_mcp_config_skips_other_users(tmp_path, db_with_rows):
    db_with_rows.insert_mcp(
        user_id="u2", name="secret", transport="stdio", command="echo"
    )
    out = compose_mcp_config(
        db=db_with_rows, user_id="u1", thread_id="t1", tmp_root=tmp_path
    )
    data = json.loads(Path(out).read_text())
    assert data["mcpServers"] == {}


def test_compose_mcp_config_skips_disabled(tmp_path, db_with_rows):
    db_with_rows.insert_mcp(
        user_id="u1",
        name="off",
        transport="stdio",
        command="echo",
        enabled=False,
    )
    out = compose_mcp_config(
        db=db_with_rows, user_id="u1", thread_id="t1", tmp_root=tmp_path
    )
    data = json.loads(Path(out).read_text())
    assert data["mcpServers"] == {}


def test_compose_mcp_config_http_transport(tmp_path, db_with_rows):
    db_with_rows.insert_mcp(
        user_id="u1",
        name="h",
        transport="http",
        url="https://example/mcp",
        headers={"Auth": "Bearer x"},
    )
    out = compose_mcp_config(
        db=db_with_rows, user_id="u1", thread_id="t1", tmp_root=tmp_path
    )
    data = json.loads(Path(out).read_text())
    assert data["mcpServers"]["h"] == {
        "url": "https://example/mcp",
        "headers": {"Auth": "Bearer x"},
    }


def test_compose_skills_dir_symlinks(tmp_path, db_with_rows):
    skill_src = tmp_path / "skill1"
    skill_src.mkdir()
    (skill_src / "SKILL.md").write_text("---\nname: skill1\n---")
    db_with_rows.insert_skill(
        user_id="u1", name="skill1", source="upload", path=str(skill_src)
    )
    target = tmp_path / "threads/t1/user-data/.claude/skills"
    compose_skills_dir(db=db_with_rows, user_id="u1", skills_dir=target)
    assert (target / "skill1").is_symlink()
    assert (target / "skill1" / "SKILL.md").read_text().startswith(
        "---\nname: skill1"
    )


def test_compose_skills_dir_replaces_existing(tmp_path, db_with_rows):
    target = tmp_path / ".claude/skills"
    target.mkdir(parents=True)
    (target / "stale").write_text("leftover")
    skill_src = tmp_path / "skill2"
    skill_src.mkdir()
    db_with_rows.insert_skill(
        user_id="u1", name="skill2", source="upload", path=str(skill_src)
    )
    compose_skills_dir(db=db_with_rows, user_id="u1", skills_dir=target)
    assert not (target / "stale").exists()
    assert (target / "skill2").is_symlink()
