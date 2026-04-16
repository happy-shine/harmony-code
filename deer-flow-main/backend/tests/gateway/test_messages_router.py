import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.gateway.harmony_app import app

pytestmark = pytest.mark.skipif(shutil.which("claude") is None,
                                reason="claude CLI not installed")


client = TestClient(app)


def test_create_thread_then_send_message_streams_sse(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    # Task 3.3: send_message now reads harmony.db for MCP + skills on every
    # spawn. Apply migrations against the tmp data dir so those queries find
    # the expected schema.
    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path}/harmony.db")
    command.upgrade(cfg, "head")

    r = client.post("/api/threads", json={})
    assert r.status_code == 200
    tid = r.json()["id"]

    r2 = client.post(f"/api/threads/{tid}/messages",
                     json={"content": "say hi in one word"},
                     headers={"Accept": "text/event-stream"})
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("text/event-stream")
    body = r2.text
    # Expect at least one system.init frame and a final result frame
    assert '"type":"system"' in body
    assert '"subtype":"init"' in body
    assert '"type":"result"' in body or "event: done" in body
