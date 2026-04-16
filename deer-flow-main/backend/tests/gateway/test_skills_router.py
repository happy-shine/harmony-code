"""Task 3.5: DB-backed ``/api/skills`` CRUD router tests.

Covers list/upload/patch/delete lifecycle, authorization for global
(``user_id IS NULL``) and cross-user rows, and an end-to-end check that
a freshly uploaded skill shows up as a symlink in
:func:`app.cc_adapter.compose.compose_skills_dir` (the same hook CC uses on
every spawn).

Follows the Task 3.4 fixture pattern: alembic ``Config()`` WITHOUT the
ini path so ``logging.config.fileConfig`` can't disable pre-existing
loggers and break caplog-based tests elsewhere in the suite.
"""
from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.db import Db, get_engine


BACKEND_DIR = Path(__file__).resolve().parents[2]


def _run_migrations(data_dir: Path) -> None:
    prev = os.environ.get("HARMONY_DATA_DIR")
    os.environ["HARMONY_DATA_DIR"] = str(data_dir)
    try:
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        if prev is None:
            os.environ.pop("HARMONY_DATA_DIR", None)
        else:
            os.environ["HARMONY_DATA_DIR"] = prev


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    _run_migrations(tmp_path)
    from app.gateway.harmony_app import app

    return TestClient(app), tmp_path


# --- Helpers --------------------------------------------------------------


def _zip_bytes(tree: dict[str, bytes], root: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in tree.items():
            key = f"{root}/{name}" if root else name
            zf.writestr(key, content)
    return buf.getvalue()


# --- GET / list -----------------------------------------------------------


def test_list_empty(client):
    c, _ = client
    r = c.get("/api/skills")
    assert r.status_code == 200
    assert r.json() == []


# --- POST /upload ---------------------------------------------------------


def test_upload_valid_zip_creates_row_and_dir(client):
    c, tmp = client
    payload = _zip_bytes({"SKILL.md": b"---\nname: my_sk\n---\nbody"})
    r = c.post(
        "/api/skills/upload",
        files={"file": ("pkg.zip", payload, "application/zip")},
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["id"].startswith("sk_")
    assert out["user_id"] == "u_default"
    assert out["name"] == "my_sk"
    assert out["source"] == "upload"
    assert out["enabled"] is True
    # skills_store dir was created and SKILL.md was extracted
    p = Path(out["path"])
    assert p.is_dir()
    assert (p / "SKILL.md").is_file()
    assert str(p).startswith(str(tmp / "skills_store"))


def test_upload_strips_single_root_dir(client):
    c, _ = client
    payload = _zip_bytes(
        {"SKILL.md": b"---\nname: wrapped\n---\n", "sub/x.txt": b"y"},
        root="pkg-main",
    )
    r = c.post(
        "/api/skills/upload",
        files={"file": ("pkg.zip", payload, "application/zip")},
    )
    assert r.status_code == 201, r.text
    p = Path(r.json()["path"])
    assert (p / "SKILL.md").is_file()
    assert (p / "sub" / "x.txt").is_file()
    assert not (p / "pkg-main").exists()


def test_upload_non_zip_filename_is_400(client):
    c, _ = client
    r = c.post(
        "/api/skills/upload",
        files={"file": ("pkg.tar", b"not a zip", "application/x-tar")},
    )
    assert r.status_code == 400


def test_upload_missing_skill_md_is_400_and_no_row(client):
    c, tmp = client
    payload = _zip_bytes({"README.md": b"hi"})
    r = c.post(
        "/api/skills/upload",
        files={"file": ("pkg.zip", payload, "application/zip")},
    )
    assert r.status_code == 400
    # No DB row
    assert c.get("/api/skills").json() == []
    # No orphan dir in skills_store
    store = tmp / "skills_store"
    assert not store.exists() or not any(store.glob("sk_*"))


def test_list_after_upload_includes_row(client):
    c, _ = client
    payload = _zip_bytes({"SKILL.md": b"---\nname: first\n---\n"})
    c.post(
        "/api/skills/upload",
        files={"file": ("a.zip", payload, "application/zip")},
    )
    payload2 = _zip_bytes({"SKILL.md": b"---\nname: second\n---\n"})
    c.post(
        "/api/skills/upload",
        files={"file": ("b.zip", payload2, "application/zip")},
    )
    rows = c.get("/api/skills").json()
    names = sorted(r["name"] for r in rows)
    assert names == ["first", "second"]


def test_global_row_visible_in_list(client):
    c, tmp = client
    # Direct DB insert with user_id=None to simulate a global row.
    db = Db(get_engine(tmp))
    db.insert_skill(
        user_id=None, name="global_sk", source="upload", path="/does/not/matter"
    )
    rows = c.get("/api/skills").json()
    names = [r["name"] for r in rows]
    assert "global_sk" in names
    match = next(r for r in rows if r["name"] == "global_sk")
    assert match["user_id"] is None


# --- PATCH / update -------------------------------------------------------


def test_patch_renames_row(client):
    c, _ = client
    payload = _zip_bytes({"SKILL.md": b"---\nname: orig\n---\n"})
    created = c.post(
        "/api/skills/upload",
        files={"file": ("a.zip", payload, "application/zip")},
    ).json()
    sid = created["id"]

    r = c.patch(f"/api/skills/{sid}", json={"name": "renamed"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["name"] == "renamed"
    # path/source survive — installer artifacts are not editable via PATCH.
    assert out["source"] == "upload"
    assert out["path"] == created["path"]


def test_patch_toggles_enabled(client):
    c, _ = client
    payload = _zip_bytes({"SKILL.md": b"---\nname: x\n---\n"})
    sid = c.post(
        "/api/skills/upload",
        files={"file": ("a.zip", payload, "application/zip")},
    ).json()["id"]
    r = c.patch(f"/api/skills/{sid}", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_patch_nonexistent_is_404(client):
    c, _ = client
    r = c.patch("/api/skills/sk_does_not_exist", json={"enabled": False})
    assert r.status_code == 404


def test_patch_other_users_row_is_403(client):
    c, tmp = client
    db = Db(get_engine(tmp))
    other_id = db.insert_skill(
        user_id="u_other", name="theirs", source="upload", path="/nowhere"
    )
    r = c.patch(f"/api/skills/{other_id}", json={"enabled": False})
    assert r.status_code == 403


def test_patch_global_row_is_403(client):
    c, tmp = client
    db = Db(get_engine(tmp))
    gid = db.insert_skill(
        user_id=None, name="g", source="upload", path="/nowhere"
    )
    r = c.patch(f"/api/skills/{gid}", json={"name": "hacked"})
    assert r.status_code == 403


# --- DELETE ---------------------------------------------------------------


def test_delete_own_row_removes_db_and_dir(client):
    c, _ = client
    payload = _zip_bytes({"SKILL.md": b"---\nname: gone\n---\n"})
    created = c.post(
        "/api/skills/upload",
        files={"file": ("a.zip", payload, "application/zip")},
    ).json()
    sid = created["id"]
    skill_path = Path(created["path"])
    assert skill_path.is_dir()

    r = c.delete(f"/api/skills/{sid}")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # Row removed from list
    assert all(row["id"] != sid for row in c.get("/api/skills").json())
    # And the filesystem dir is gone too.
    assert not skill_path.exists()


def test_delete_nonexistent_is_404(client):
    c, _ = client
    r = c.delete("/api/skills/sk_nope")
    assert r.status_code == 404


def test_delete_global_row_is_403(client):
    c, tmp = client
    db = Db(get_engine(tmp))
    gid = db.insert_skill(
        user_id=None, name="g", source="upload", path="/nowhere"
    )
    r = c.delete(f"/api/skills/{gid}")
    assert r.status_code == 403
    rows = c.get("/api/skills").json()
    assert any(row["id"] == gid for row in rows)


def test_delete_other_users_row_is_403(client):
    c, tmp = client
    db = Db(get_engine(tmp))
    other_id = db.insert_skill(
        user_id="u_other", name="theirs", source="upload", path="/nowhere"
    )
    r = c.delete(f"/api/skills/{other_id}")
    assert r.status_code == 403


# --- End-to-end: uploaded skill lands in composed skills dir --------------


def test_end_to_end_upload_then_compose_skills_dir(client):
    """A freshly uploaded skill is symlinked into ``.claude/skills`` on spawn."""
    from app.cc_adapter.compose import compose_skills_dir

    c, tmp = client
    payload = _zip_bytes({"SKILL.md": b"---\nname: e2e_sk\n---\n"})
    created = c.post(
        "/api/skills/upload",
        files={"file": ("pkg.zip", payload, "application/zip")},
    ).json()

    target = tmp / "threads/t1/.claude/skills"
    db = Db(get_engine(tmp))
    compose_skills_dir(db=db, user_id="u_default", skills_dir=target)

    link = target / "e2e_sk"
    assert link.is_symlink()
    # Symlink resolves to the installer's skills_store dir
    assert link.resolve() == Path(created["path"]).resolve()
    assert (link / "SKILL.md").is_file()
