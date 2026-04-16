"""Task 3.4: DB-backed ``/api/mcp`` CRUD router tests.

Covers list/create/patch/delete lifecycle, JSON round-trip through the
``args_json`` / ``headers_json`` / ``env_json`` columns, authorization
for global (``user_id IS NULL``) and cross-user rows, and an end-to-end
check that a freshly created MCP row shows up in the composed config
handed to ``CCAdapter.run`` on the next message.

All fixtures follow Task 3.3's pattern: construct :class:`alembic.config.Config`
**without** the ini path, so alembic doesn't call ``logging.config.fileConfig``
and break caplog-based tests elsewhere in the suite.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
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


# --- GET / list -----------------------------------------------------------


def test_list_empty(client):
    c, _ = client
    r = c.get("/api/mcp")
    assert r.status_code == 200
    assert r.json() == []


# --- POST / create --------------------------------------------------------


def test_create_returns_row_with_id_and_user_id(client):
    c, _ = client
    body = {"name": "srv", "transport": "stdio", "command": "echo"}
    r = c.post("/api/mcp", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["id"].startswith("mcp_")
    assert out["user_id"] == "u_default"
    assert out["name"] == "srv"
    assert out["transport"] == "stdio"
    assert out["command"] == "echo"
    assert out["enabled"] is True


def test_list_after_create_includes_row(client):
    c, _ = client
    c.post("/api/mcp", json={"name": "a", "transport": "stdio", "command": "true"})
    c.post("/api/mcp", json={"name": "b", "transport": "stdio", "command": "true"})
    rows = c.get("/api/mcp").json()
    names = sorted(r["name"] for r in rows)
    assert names == ["a", "b"]


def test_create_roundtrips_args_headers_env(client):
    c, _ = client
    body = {
        "name": "rich",
        "transport": "http",
        "url": "https://x.example/mcp",
        "headers": {"Authorization": "Bearer x", "X-Trace": "1"},
        "env": {"FOO": "bar"},
        "args": ["--flag", "v"],
    }
    out = c.post("/api/mcp", json=body).json()
    assert out["args"] == ["--flag", "v"]
    assert out["headers"] == {"Authorization": "Bearer x", "X-Trace": "1"}
    assert out["env"] == {"FOO": "bar"}
    assert out["url"] == "https://x.example/mcp"

    # Fresh list call rehydrates from the DB — ensures JSON columns are parsed
    rows = c.get("/api/mcp").json()
    match = next(r for r in rows if r["id"] == out["id"])
    assert match["args"] == ["--flag", "v"]
    assert match["headers"]["Authorization"] == "Bearer x"
    assert match["env"] == {"FOO": "bar"}


def test_global_row_visible_in_list(client):
    c, tmp = client
    # Direct DB insert with user_id=None to simulate a global/admin row.
    db = Db(get_engine(tmp))
    db.insert_mcp(user_id=None, name="global_srv", transport="stdio", command="true")
    rows = c.get("/api/mcp").json()
    names = [r["name"] for r in rows]
    assert "global_srv" in names
    row = next(r for r in rows if r["name"] == "global_srv")
    assert row["user_id"] is None


def test_user_owned_row_visible_in_list(client):
    c, _ = client
    c.post(
        "/api/mcp",
        json={"name": "mine", "transport": "stdio", "command": "true"},
    )
    rows = c.get("/api/mcp").json()
    assert any(r["name"] == "mine" and r["user_id"] == "u_default" for r in rows)


# --- PATCH / update -------------------------------------------------------


def test_patch_updates_subset_preserves_rest(client):
    c, _ = client
    out = c.post(
        "/api/mcp",
        json={
            "name": "s",
            "transport": "stdio",
            "command": "echo",
            "args": ["one"],
            "env": {"K": "v"},
        },
    ).json()
    mid = out["id"]

    r = c.patch(f"/api/mcp/{mid}", json={"enabled": False, "command": "cat"})
    assert r.status_code == 200, r.text
    patched = r.json()
    assert patched["enabled"] is False
    assert patched["command"] == "cat"
    # untouched fields survive
    assert patched["name"] == "s"
    assert patched["args"] == ["one"]
    assert patched["env"] == {"K": "v"}
    assert patched["transport"] == "stdio"


def test_patch_other_users_row_is_403(client):
    c, tmp = client
    db = Db(get_engine(tmp))
    other_id = db.insert_mcp(user_id="u_other", name="theirs", transport="stdio", command="true")
    # u_other's row is NOT visible in u_default's GET (different owner, not global).
    rows = c.get("/api/mcp").json()
    assert all(r["id"] != other_id for r in rows)
    # But a direct PATCH by id must still 403.
    r = c.patch(f"/api/mcp/{other_id}", json={"enabled": False})
    assert r.status_code == 403


def test_patch_global_row_is_403(client):
    c, tmp = client
    db = Db(get_engine(tmp))
    gid = db.insert_mcp(user_id=None, name="global_srv", transport="stdio", command="true")
    r = c.patch(f"/api/mcp/{gid}", json={"enabled": False})
    assert r.status_code == 403


def test_patch_nonexistent_is_404(client):
    c, _ = client
    r = c.patch("/api/mcp/mcp_does_not_exist", json={"enabled": False})
    assert r.status_code == 404


# --- DELETE ---------------------------------------------------------------


def test_delete_own_row_then_list_omits(client):
    c, _ = client
    mid = c.post("/api/mcp", json={"name": "d", "transport": "stdio", "command": "true"}).json()["id"]

    r = c.delete(f"/api/mcp/{mid}")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    rows = c.get("/api/mcp").json()
    assert all(row["id"] != mid for row in rows)


def test_delete_global_row_is_403(client):
    c, tmp = client
    db = Db(get_engine(tmp))
    gid = db.insert_mcp(user_id=None, name="global_srv", transport="stdio", command="true")
    r = c.delete(f"/api/mcp/{gid}")
    assert r.status_code == 403
    # row must still be present
    rows = c.get("/api/mcp").json()
    assert any(row["id"] == gid for row in rows)


def test_delete_nonexistent_is_404(client):
    c, _ = client
    r = c.delete("/api/mcp/mcp_nope")
    assert r.status_code == 404


def test_delete_other_users_row_is_403(client):
    c, tmp = client
    db = Db(get_engine(tmp))
    other_id = db.insert_mcp(user_id="u_other", name="theirs", transport="stdio", command="true")
    r = c.delete(f"/api/mcp/{other_id}")
    assert r.status_code == 403


# --- End-to-end: created row lands in composed MCP config -----------------


class _CapturedCfg:
    value = None


@pytest.fixture(autouse=True)
def _reset_sse_starlette_exit_event():
    """Match the Task 3.3 fixture — TestClient spins a fresh loop per test."""
    yield
    try:
        from sse_starlette.sse import AppStatus

        AppStatus.should_exit_event = None
        AppStatus.should_exit = False
    except Exception:
        pass


def test_end_to_end_create_then_send_message_composes_row(client, monkeypatch):
    c, tmp = client
    # 1. Create the MCP via the new router.
    created = c.post(
        "/api/mcp",
        json={
            "name": "e2e_fs",
            "transport": "stdio",
            "command": "echo",
            "args": ["e2e"],
        },
    ).json()
    assert created["id"].startswith("mcp_")

    # 2. Stub out CCAdapter.run and capture the SpawnConfig.
    captured = _CapturedCfg()

    async def fake_run(self, cfg):
        captured.value = cfg
        yield {"type": "system", "subtype": "init", "session_id": "s_fake"}
        yield {"type": "result", "duration_ms": 1}

    monkeypatch.setattr("app.cc_adapter.adapter.CCAdapter.run", fake_run)

    # 3. Create a thread + send a message so send_message re-composes MCP.
    tid = c.post("/api/threads", json={}).json()["id"]
    with c.stream("POST", f"/api/threads/{tid}/messages", json={"content": "hi"}) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_text():
            pass

    cfg = captured.value
    assert cfg is not None and cfg.mcp_config_path
    data = json.loads(Path(cfg.mcp_config_path).read_text())
    assert "e2e_fs" in data["mcpServers"]
    assert data["mcpServers"]["e2e_fs"]["command"] == "echo"
    assert data["mcpServers"]["e2e_fs"]["args"] == ["e2e"]
