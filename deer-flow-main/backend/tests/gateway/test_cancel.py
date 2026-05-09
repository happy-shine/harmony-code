"""End-to-end cancel + reconnect coverage with a real ``claude`` CLI.

Two scenarios:

- ``test_client_disconnect_does_not_kill_runner``: dropping the SSE
  stream must NOT terminate the CC subprocess. The runner keeps going
  on the server, the new POST returns 409 ``thread_busy`` until the
  run finishes naturally (or /cancel kills it).

- ``test_explicit_cancel_terminates_runner``: ``POST /cancel`` while
  a run is in flight actually stops CC and frees the slot, so a new
  message on the same thread succeeds.

- ``test_cancel_endpoint_returns_no_inflight_when_idle``: cancel on an
  idle thread returns ``{canceled: False, reason: "no_inflight"}``.

- ``test_reconnect_via_get_stream_replays_buffer``: after the SSE link
  drops, ``GET /threads/{tid}/stream`` resumes the same run from where
  the buffer left off, including the terminal ``done`` event.
"""

from __future__ import annotations

import shutil

import httpx
import pytest

pytestmark = pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")


@pytest.mark.asyncio
async def test_client_disconnect_does_not_kill_runner(gateway_server):
    """SSE drop ≠ user cancel. After abort, the runner continues and a
    second POST on the same thread is rejected with 409 until the run
    finishes — the entire point of decoupling the runner from HTTP."""
    base = gateway_server.url
    async with httpx.AsyncClient(timeout=30.0, cookies=gateway_server.auth_cookies) as client:
        tid = (await client.post(f"{base}/api/threads", json={})).json()["id"]

        async with client.stream(
            "POST",
            f"{base}/api/threads/{tid}/messages",
            json={"content": "write a 300-word poem slowly"},
        ) as r:
            assert r.status_code == 200
            ait = r.aiter_bytes()
            _ = await ait.__anext__()  # confirm stream is live
            await r.aclose()  # drop the SSE link

        # Immediately try a new message: must be rejected because the
        # runner is still active.
        r2 = await client.post(
            f"{base}/api/threads/{tid}/messages",
            json={"content": "say hi in one word"},
        )
        assert r2.status_code == 409
        assert r2.json()["detail"] == "thread_busy"

        # Cancel to free the slot so the test doesn't leave a runaway run.
        await client.post(f"{base}/api/threads/{tid}/cancel")


@pytest.mark.asyncio
async def test_explicit_cancel_terminates_runner(gateway_server):
    """``POST /cancel`` actually kills the in-flight CC subprocess and
    frees the per-thread slot, so a fresh message admits cleanly."""
    base = gateway_server.url
    async with httpx.AsyncClient(timeout=30.0, cookies=gateway_server.auth_cookies) as client:
        tid = (await client.post(f"{base}/api/threads", json={})).json()["id"]
        async with client.stream(
            "POST",
            f"{base}/api/threads/{tid}/messages",
            json={"content": "write a 300-word poem slowly"},
        ) as r:
            assert r.status_code == 200
            _ = await r.aiter_bytes().__anext__()
            await r.aclose()

        cancel = await client.post(f"{base}/api/threads/{tid}/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["canceled"] is True

        # After cancel, the slot is free and a new message admits.
        r2 = await client.post(
            f"{base}/api/threads/{tid}/messages",
            json={"content": "say hi in one word"},
        )
        assert r2.status_code == 200


@pytest.mark.asyncio
async def test_cancel_endpoint_returns_no_inflight_when_idle(gateway_server):
    """Cancel on an idle thread returns ``{canceled: False, reason: no_inflight}``."""
    base = gateway_server.url
    async with httpx.AsyncClient(timeout=10.0, cookies=gateway_server.auth_cookies) as client:
        tid = (await client.post(f"{base}/api/threads", json={})).json()["id"]
        r = await client.post(f"{base}/api/threads/{tid}/cancel")
        assert r.status_code == 200
        body = r.json()
        assert body["canceled"] is False
        assert body["reason"] == "no_inflight"


@pytest.mark.asyncio
async def test_reconnect_via_get_stream_replays_buffer(gateway_server):
    """After the SSE link drops mid-run, ``GET /threads/{tid}/stream``
    re-attaches to the same runner and delivers the rest of the events
    plus the terminal ``done`` marker."""
    base = gateway_server.url
    async with httpx.AsyncClient(timeout=60.0, cookies=gateway_server.auth_cookies) as client:
        tid = (await client.post(f"{base}/api/threads", json={})).json()["id"]
        # Start a quick run (one-word reply) so we don't have to wait long
        # for the natural end after reconnect.
        async with client.stream(
            "POST",
            f"{base}/api/threads/{tid}/messages",
            json={"content": "say hi in one word"},
        ) as r:
            assert r.status_code == 200
            _ = await r.aiter_bytes().__anext__()
            await r.aclose()

        # Reconnect via GET. Without Last-Event-ID the cursor is 0 so
        # we replay everything that was buffered.
        async with client.stream("GET", f"{base}/api/threads/{tid}/stream") as r:
            assert r.status_code == 200
            seen_done = False
            async for chunk in r.aiter_text():
                if "event: done" in chunk:
                    seen_done = True
                    break
            assert seen_done, "GET /stream did not deliver terminal 'done' event after reconnect"
