"""Task 4.1: Workspace router tests.

Covers the file-tree + download endpoints that expose the CC-managed
``<HARMONY_DATA_DIR>/threads/<tid>/user-data/workspace/`` directory to
the frontend. Security-critical — path-escape, symlink, and null-byte
tests must fail loudly before any production deploy.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient


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


def _new_thread(c: TestClient) -> tuple[str, Path]:
    r = c.post("/api/threads", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["id"], Path(body["cwd"])


# --- Tree: happy path ------------------------------------------------------


def test_tree_empty_workspace_returns_empty_list(client):
    """Fresh thread → workspace/tree returns ``{"root": "...", "children": []}``."""
    c, _ = client
    tid, cwd = _new_thread(c)
    r = c.get(f"/api/threads/{tid}/workspace/tree")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["children"] == []


# --- Security: path escape (Task 4.1 Step 1 — the failing test) ------------


def test_workspace_path_escape_blocked(client):
    """Per the plan's Step 1 snippet: a crafted ``../../../`` path MUST NOT
    leak files outside the thread's cwd. Accept 400/403/404 but NOT 200."""
    c, tmp = client
    tid, cwd = _new_thread(c)

    # Plant a secret at the data root, outside every thread's cwd.
    secret = tmp / "secret.txt"
    secret.write_text("pwned")
    # And one higher than that (the tmp_path parent), for good measure.
    (tmp.parent / "escape-secret.txt").write_text("double-pwned")

    # Crafted relative path (URL-encoded ../../).
    r = c.get(f"/api/threads/{tid}/workspace/files/..%2F..%2F..%2Fsecret.txt")
    assert r.status_code in (400, 403, 404), (r.status_code, r.text)
    # The response body must not contain the secret.
    assert "pwned" not in r.text
