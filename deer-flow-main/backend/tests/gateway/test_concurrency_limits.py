"""Task 6.1: admission-control hardening tests.

Covers the three-tier concurrency table from design Section 2:

- per-thread serial (409 ``thread_busy``)
- per-user concurrency cap (429 ``user_concurrency_limit``)
- server-wide concurrency cap (503 ``server_busy``)

plus the release paths (natural completion, mid-stream exception,
client disconnect). All tests mock ``CCAdapter.run`` so they do not
depend on the real ``claude`` CLI and can precisely choreograph the
overlap between requests via an ``asyncio.Event`` gate.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest
from alembic.config import Config

from alembic import command

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


@pytest.fixture(autouse=True)
def _reset_sse_starlette_exit_event():
    """Clear sse_starlette's cached AppStatus event between tests so the
    gated adapter doesn't hit ``bound to a different event loop`` errors
    when we spin up fresh event loops inside async tests.
    """
    yield
    try:
        from sse_starlette.sse import AppStatus

        AppStatus.should_exit_event = None
        AppStatus.should_exit = False
    except Exception:
        pass


@pytest.fixture
def migrated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    _run_migrations(tmp_path)
    return tmp_path


@pytest.fixture
def reset_counters():
    """Every test runs against a fresh inflight/user/server counter state.

    ``messages`` is a module-level singleton; other tests may have left
    the counters in a non-zero state if they failed mid-flight. We snap
    back to zero pre-test AND post-test so we don't corrupt later tests.
    """
    from app.gateway.routers import messages

    def _zero() -> None:
        messages._inflight.clear()
        messages._user_inflight.clear()
        messages._server_inflight[0] = 0

    _zero()
    yield
    _zero()


def _mock_adapter_run(gate: asyncio.Event, started: asyncio.Event):
    """Build an async ``run`` method that blocks on ``gate`` until released.

    Yields ``system.init`` up front (so the gateway emits stream headers
    and begins pumping SSE frames), sets ``started`` so the test knows
    the request made it past admission, then waits on ``gate`` before
    emitting the terminal ``result`` frame and returning.
    """

    async def fake_run(self, cfg):  # noqa: ARG001
        yield {"type": "system", "subtype": "init", "session_id": "s_mock"}
        started.set()
        await gate.wait()
        yield {"type": "result", "duration_ms": 1}

    return fake_run


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _create_thread(client: httpx.AsyncClient) -> str:
    r = await client.post("/api/threads", json={})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# --- 409 thread_busy -------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_busy_409(migrated_data_dir, monkeypatch, reset_counters):
    """Second POST to same thread while first is in-flight → 409 thread_busy."""
    gate = asyncio.Event()
    started = asyncio.Event()
    monkeypatch.setattr("app.cc_adapter.adapter.CCAdapter.run", _mock_adapter_run(gate, started))

    from app.gateway.harmony_app import app

    async with _make_client(app) as client:
        tid = await _create_thread(client)

        async def first():
            async with client.stream("POST", f"/api/threads/{tid}/messages", json={"content": "hi"}) as r:
                assert r.status_code == 200
                async for _ in r.aiter_text():
                    pass

        task = asyncio.create_task(first())
        # Wait until the mock adapter has seen the request (past admission).
        await asyncio.wait_for(started.wait(), timeout=5.0)

        # Second request on SAME thread → 409.
        r2 = await client.post(f"/api/threads/{tid}/messages", json={"content": "hi again"})
        assert r2.status_code == 409
        assert r2.json()["detail"] == "thread_busy"

        # Release the first run; it should complete cleanly.
        gate.set()
        await asyncio.wait_for(task, timeout=5.0)


# --- 429 user_concurrency_limit --------------------------------------------


@pytest.mark.asyncio
async def test_user_concurrency_limit_429(migrated_data_dir, monkeypatch, reset_counters):
    """With per-user cap = 2, a 3rd in-flight request for the same user → 429."""
    from app.gateway.routers import messages

    monkeypatch.setattr(messages, "_MAX_PER_USER", 2)

    gate = asyncio.Event()
    started_count: list[int] = [0]

    async def fake_run(self, cfg):  # noqa: ARG001
        yield {"type": "system", "subtype": "init", "session_id": "s_mock"}
        started_count[0] += 1
        await gate.wait()
        yield {"type": "result", "duration_ms": 1}

    monkeypatch.setattr("app.cc_adapter.adapter.CCAdapter.run", fake_run)

    from app.gateway.harmony_app import app

    async with _make_client(app) as client:
        tids = [await _create_thread(client) for _ in range(3)]

        async def run_one(tid: str) -> None:
            async with client.stream("POST", f"/api/threads/{tid}/messages", json={"content": "hi"}) as r:
                assert r.status_code == 200
                async for _ in r.aiter_text():
                    pass

        t1 = asyncio.create_task(run_one(tids[0]))
        t2 = asyncio.create_task(run_one(tids[1]))
        # Spin until BOTH slotted runs are past admission before firing the
        # third. Without this the third could race in while slot 0 or 1 is
        # still being acquired and leak a transient 200.
        for _ in range(100):
            if started_count[0] >= 2:
                break
            await asyncio.sleep(0.02)
        assert started_count[0] == 2

        # Third request for same user must 429.
        r3 = await client.post(f"/api/threads/{tids[2]}/messages", json={"content": "hi"})
        assert r3.status_code == 429
        assert r3.json()["detail"] == "user_concurrency_limit"

        # Releasing the in-flight pair frees slots; a fresh fourth request
        # on tid[2] must then admit cleanly.
        gate.set()
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5.0)
        # Re-gate so the next run blocks but slots are free now.
        gate.clear()
        started_count[0] = 0
        t4 = asyncio.create_task(run_one(tids[2]))
        for _ in range(100):
            if started_count[0] >= 1:
                break
            await asyncio.sleep(0.02)
        assert started_count[0] == 1
        gate.set()
        await asyncio.wait_for(t4, timeout=5.0)


# --- 503 server_busy -------------------------------------------------------


@pytest.mark.asyncio
async def test_server_busy_503(migrated_data_dir, monkeypatch, reset_counters):
    """Server cap = 1 → second concurrent request (any user, any thread) → 503."""
    from app.gateway.routers import messages

    monkeypatch.setattr(messages, "_MAX_SERVER", 1)

    gate = asyncio.Event()
    started = asyncio.Event()
    monkeypatch.setattr("app.cc_adapter.adapter.CCAdapter.run", _mock_adapter_run(gate, started))

    from app.gateway.harmony_app import app

    async with _make_client(app) as client:
        tid1 = await _create_thread(client)
        tid2 = await _create_thread(client)

        async def run_one(tid: str) -> None:
            async with client.stream("POST", f"/api/threads/{tid}/messages", json={"content": "hi"}) as r:
                assert r.status_code == 200
                async for _ in r.aiter_text():
                    pass

        t1 = asyncio.create_task(run_one(tid1))
        await asyncio.wait_for(started.wait(), timeout=5.0)

        r2 = await client.post(f"/api/threads/{tid2}/messages", json={"content": "hi"})
        assert r2.status_code == 503
        assert r2.json()["detail"] == "server_busy"

        gate.set()
        await asyncio.wait_for(t1, timeout=5.0)


# --- release paths ---------------------------------------------------------


@pytest.mark.asyncio
async def test_limits_released_on_completion(migrated_data_dir, monkeypatch, reset_counters):
    """Natural EOF → per-thread + per-user + server counters all back to 0."""
    gate = asyncio.Event()
    gate.set()  # let the adapter run through immediately
    started = asyncio.Event()
    monkeypatch.setattr("app.cc_adapter.adapter.CCAdapter.run", _mock_adapter_run(gate, started))

    from app.gateway.harmony_app import app
    from app.gateway.routers import messages

    async with _make_client(app) as client:
        tid = await _create_thread(client)
        async with client.stream("POST", f"/api/threads/{tid}/messages", json={"content": "hi"}) as r:
            assert r.status_code == 200
            async for _ in r.aiter_text():
                pass

    assert tid not in messages._inflight
    assert messages._user_inflight == {}
    assert messages._server_inflight[0] == 0


@pytest.mark.asyncio
async def test_limits_released_on_exception(migrated_data_dir, monkeypatch, reset_counters):
    """Mid-stream RuntimeError → counters released, no slot leak."""

    async def boom_run(self, cfg):  # noqa: ARG001
        yield {"type": "system", "subtype": "init", "session_id": "s_mock"}
        raise RuntimeError("mid-stream blowup")

    monkeypatch.setattr("app.cc_adapter.adapter.CCAdapter.run", boom_run)

    from app.gateway.harmony_app import app
    from app.gateway.routers import messages

    async with _make_client(app) as client:
        tid = await _create_thread(client)
        try:
            async with client.stream("POST", f"/api/threads/{tid}/messages", json={"content": "hi"}) as r:
                async for _ in r.aiter_text():
                    pass
        except Exception:
            # httpx may surface a response/stream error — we only care
            # that counters cleaned up, not the wire symptom.
            pass

    assert tid not in messages._inflight
    assert messages._user_inflight == {}
    assert messages._server_inflight[0] == 0


@pytest.mark.asyncio
async def test_release_admission_is_idempotent(reset_counters):
    """``_release_admission`` called twice for the same (tid, uid) stays at 0.

    The natural-completion and mid-stream-exception paths both call it in
    an ``event_gen.finally`` AND an outer ``except BaseException`` under
    some sequences — the helper has to tolerate that. End-to-end
    disconnect coverage lives in ``tests/gateway/test_cancel.py`` (real
    server + abort), this unit-level check nails the counter math.
    """
    from app.gateway.routers import messages

    async with messages._inflight_lock:
        messages._inflight.add("t_x")
        messages._user_inflight["u"] = 1
        messages._server_inflight[0] = 1

    await messages._release_admission("t_x", "u")
    # Double-release should NOT push counters negative.
    await messages._release_admission("t_x", "u")

    assert "t_x" not in messages._inflight
    assert messages._user_inflight == {}
    assert messages._server_inflight[0] == 0
