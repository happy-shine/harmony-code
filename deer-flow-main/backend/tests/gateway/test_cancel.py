"""Cancel via client disconnect + explicit /cancel stub."""
from __future__ import annotations

import asyncio
import shutil

import httpx
import pytest

pytestmark = pytest.mark.skipif(shutil.which("claude") is None,
                                reason="claude CLI not installed")


@pytest.mark.asyncio
async def test_client_disconnect_allows_new_message(gateway_server):
    """After client disconnects mid-stream, a new message on same thread must succeed
    (no 409 thread_busy from an orphaned inflight entry)."""
    base = gateway_server.url
    async with httpx.AsyncClient(timeout=30.0) as client:
        r_thread = await client.post(f"{base}/api/threads", json={})
        assert r_thread.status_code == 200
        tid = r_thread.json()["id"]

        # Start a streaming request, read a few bytes, abort.
        async with client.stream(
            "POST", f"{base}/api/threads/{tid}/messages",
            json={"content": "write a 300-word poem slowly"},
        ) as r:
            assert r.status_code == 200
            # Pull at least one chunk to confirm the stream is live
            ait = r.aiter_bytes()
            _ = await ait.__anext__()
            await r.aclose()

        # After r.aclose(), poll /cancel until the server reports the thread is idle.
        # Max 5s total; typical is well under 1s.
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            cancel_resp = await client.post(f"{base}/api/threads/{tid}/cancel")
            if cancel_resp.json().get("reason") == "no_inflight":
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("thread did not clear inflight within 5s of disconnect")

        # Now the new message must succeed.
        r2 = await client.post(
            f"{base}/api/threads/{tid}/messages",
            json={"content": "say hi in one word"},
        )
        assert r2.status_code == 200


@pytest.mark.asyncio
async def test_cancel_endpoint_returns_no_inflight_when_idle(gateway_server):
    """Stub cancel endpoint on idle thread returns {canceled: False, reason: no_inflight}."""
    base = gateway_server.url
    async with httpx.AsyncClient(timeout=10.0) as client:
        tid = (await client.post(f"{base}/api/threads", json={})).json()["id"]
        r = await client.post(f"{base}/api/threads/{tid}/cancel")
        assert r.status_code == 200
        body = r.json()
        assert body["canceled"] is False
        assert body["reason"] == "no_inflight"
