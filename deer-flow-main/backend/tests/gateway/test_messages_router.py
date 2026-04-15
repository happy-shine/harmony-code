import shutil

import pytest
from fastapi.testclient import TestClient

from app.gateway.harmony_app import app

pytestmark = pytest.mark.skipif(shutil.which("claude") is None,
                                reason="claude CLI not installed")


client = TestClient(app)


def test_create_thread_then_send_message_streams_sse(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))

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
