"""ThreadRunner + RunnerRegistry: lifecycle owned by background task,
not by any HTTP request.

These tests replace ``CCAdapter.run`` with an in-process async generator
so they exercise the runner machinery without needing the ``claude`` CLI.
The real adapter is covered by ``test_adapter.py`` / ``test_timeout.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.cc_adapter.runner import RunnerOutcome, RunnerRegistry, ThreadRunner
from app.cc_adapter.types import SpawnConfig


class _FakeAdapter:
    """Minimal stand-in for CCAdapter — yields the frames the test passes in."""

    def __init__(self, frames: list[dict], *, between_delay: float = 0.0, hang_after: int | None = None) -> None:
        self._frames = frames
        self._between = between_delay
        self._hang_after = hang_after  # None = never hang
        # ``cleanup_ran`` is True if the generator's body unwound via any
        # cancellation/exit path — covers BOTH ``GeneratorExit`` (when the
        # consumer is suspended awaiting __anext__ and someone aclose()s
        # us) and ``CancelledError`` (when the cancel hits the generator's
        # internal ``await asyncio.sleep`` first). The real adapter has
        # the same broad ``except BaseException`` to terminate CC.
        self.cleanup_ran = False

    def run(self, cfg: SpawnConfig) -> AsyncIterator[dict]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[dict]:
        try:
            for i, f in enumerate(self._frames):
                if self._between > 0:
                    await asyncio.sleep(self._between)
                yield f
                if self._hang_after is not None and i + 1 == self._hang_after:
                    # Simulate CC stuck mid-stream (waiting on a long
                    # tool call). Cancel must unblock this.
                    await asyncio.sleep(3600)
        except BaseException:
            self.cleanup_ran = True
            raise


def _cfg() -> SpawnConfig:
    return SpawnConfig(cwd="/tmp", user_prompt="x")


# -- ThreadRunner -----------------------------------------------------


@pytest.mark.asyncio
async def test_runner_natural_completion_records_session_and_cost():
    frames = [
        {"type": "system", "subtype": "init", "session_id": "sess-abc"},
        {"type": "assistant", "message": {"content": "hi"}},
        {"type": "result", "total_cost_usd": 0.0123},
    ]
    captured: list[RunnerOutcome] = []

    async def hook(o: RunnerOutcome) -> None:
        captured.append(o)

    runner = ThreadRunner(
        thread_id="t1",
        cfg=_cfg(),
        adapter=_FakeAdapter(frames),
        on_terminate=hook,
    )
    runner.start()
    await runner.wait()

    assert runner.done()
    assert runner.disposition == "natural"
    assert runner.session_id == "sess-abc"
    assert runner.bus.closed
    # Bus replayed: 3 real frames, no synthetic terminal added.
    out = [ev async for _eid, ev in runner.bus.subscribe()]
    assert [e["type"] for e in out] == ["system", "assistant", "result"]
    # Hook fired with the right outcome.
    assert len(captured) == 1
    o = captured[0]
    assert o.disposition == "natural"
    assert o.session_id == "sess-abc"
    assert o.cost_usd == 0.0123
    assert o.exit_code == 0


@pytest.mark.asyncio
async def test_runner_cancel_terminates_and_publishes_synthetic_frame():
    frames = [
        {"type": "system", "subtype": "init", "session_id": "sess-1"},
        {"type": "assistant", "message": {"content": "thinking..."}},
    ]
    fake = _FakeAdapter(frames, hang_after=2)
    runner = ThreadRunner(thread_id="t1", cfg=_cfg(), adapter=fake)
    runner.start()
    # Let it emit both frames and then hang.
    await asyncio.sleep(0.05)
    await runner.cancel()

    assert runner.done()
    assert runner.disposition == "cancelled"
    # Adapter generator's cleanup ran (CancelledError or GeneratorExit
    # path — both represent a clean unwind that would terminate CC).
    assert fake.cleanup_ran
    # The synthetic ``cancelled`` frame is on the bus.
    out = [ev async for _eid, ev in runner.bus.subscribe()]
    last = out[-1]
    assert last["type"] == "_adapter"
    assert last["subtype"] == "error"
    assert last["code"] == "cancelled"


@pytest.mark.asyncio
async def test_runner_cancel_is_idempotent():
    fake = _FakeAdapter([{"type": "x"}], hang_after=1)
    runner = ThreadRunner(thread_id="t1", cfg=_cfg(), adapter=fake)
    runner.start()
    await asyncio.sleep(0.01)
    await runner.cancel()
    await runner.cancel()  # must not raise
    assert runner.disposition == "cancelled"


@pytest.mark.asyncio
async def test_runner_disconnect_does_not_terminate():
    """Drop the SSE subscriber mid-run; the runner must still finish naturally."""
    frames = [
        {"type": "system", "subtype": "init", "session_id": "s"},
        {"type": "assistant", "message": {"content": "a"}},
        {"type": "assistant", "message": {"content": "b"}},
        {"type": "result"},
    ]
    runner = ThreadRunner(
        thread_id="t1",
        cfg=_cfg(),
        adapter=_FakeAdapter(frames, between_delay=0.02),
    )
    runner.start()

    # Subscribe, read 1 frame, drop the subscription.
    sub = runner.bus.subscribe()
    first = await sub.__anext__()
    assert first[1]["type"] == "system"
    await sub.aclose()  # mimic SSE client disconnect

    # Runner must STILL run to completion.
    await runner.wait()
    assert runner.disposition == "natural"
    # And every frame is replayable from the bus.
    out = [ev async for _eid, ev in runner.bus.subscribe()]
    assert [e["type"] for e in out] == ["system", "assistant", "assistant", "result"]


@pytest.mark.asyncio
async def test_runner_adapter_error_frame_records_exit_code_but_disposition_natural():
    """When the adapter yields a ``cc_nonzero_exit`` frame and then EOFs,
    the disposition is still ``natural`` (the run wasn't cancelled or
    crashed), but the exit_code from the adapter frame is captured for
    the audit hook. This mirrors the pre-runner gateway behavior so
    audit consumers don't need to relearn the disposition labels."""
    frames = [
        {"type": "system", "subtype": "init", "session_id": "s"},
        {"type": "_adapter", "subtype": "error", "code": "cc_nonzero_exit", "exit_code": 137},
    ]
    captured: list[RunnerOutcome] = []

    async def hook(o: RunnerOutcome) -> None:
        captured.append(o)

    runner = ThreadRunner(thread_id="t1", cfg=_cfg(), adapter=_FakeAdapter(frames), on_terminate=hook)
    runner.start()
    await runner.wait()
    assert runner.disposition == "natural"
    assert captured[0].exit_code == 137


@pytest.mark.asyncio
async def test_runner_run_id_is_unique_per_runner():
    r1 = ThreadRunner(thread_id="t1", cfg=_cfg(), adapter=_FakeAdapter([]))
    r2 = ThreadRunner(thread_id="t1", cfg=_cfg(), adapter=_FakeAdapter([]))
    assert r1.run_id != r2.run_id
    assert r1.run_id.startswith("r_")


# -- RunnerRegistry ---------------------------------------------------


@pytest.mark.asyncio
async def test_registry_start_then_get_returns_same_runner():
    reg = RunnerRegistry(idle_retention_seconds=60)
    runner = await reg.start(
        thread_id="t1",
        cfg=_cfg(),
        adapter=_FakeAdapter([{"type": "result"}]),
    )
    assert reg.get("t1") is runner
    assert reg.active("t1") is True
    await runner.wait()
    # After completion, still in registry until GC sweep.
    assert reg.get("t1") is runner
    assert reg.active("t1") is False


@pytest.mark.asyncio
async def test_registry_rejects_concurrent_start_for_same_thread():
    reg = RunnerRegistry()
    fake = _FakeAdapter([{"type": "x"}], hang_after=1)
    await reg.start(thread_id="t1", cfg=_cfg(), adapter=fake)
    with pytest.raises(RuntimeError):
        await reg.start(thread_id="t1", cfg=_cfg(), adapter=_FakeAdapter([]))
    await reg.cancel("t1")


@pytest.mark.asyncio
async def test_registry_replaces_finished_runner_on_new_start():
    """After a runner finishes, starting a new one on the same thread
    works (the finished entry is dropped, not treated as 'active')."""
    reg = RunnerRegistry()
    r1 = await reg.start(thread_id="t1", cfg=_cfg(), adapter=_FakeAdapter([{"type": "result"}]))
    await r1.wait()
    r2 = await reg.start(thread_id="t1", cfg=_cfg(), adapter=_FakeAdapter([{"type": "result"}]))
    assert r2 is not r1
    await r2.wait()


@pytest.mark.asyncio
async def test_registry_cancel_returns_false_when_no_active_runner():
    reg = RunnerRegistry()
    assert (await reg.cancel("nope")) is False


@pytest.mark.asyncio
async def test_registry_cancel_returns_true_and_runner_terminates():
    reg = RunnerRegistry()
    fake = _FakeAdapter([{"type": "x"}], hang_after=1)
    runner = await reg.start(thread_id="t1", cfg=_cfg(), adapter=fake)
    await asyncio.sleep(0.01)
    assert (await reg.cancel("t1")) is True
    assert runner.done()
    assert runner.disposition == "cancelled"


@pytest.mark.asyncio
async def test_registry_on_terminate_hook_fires_with_outcome():
    captured: list[RunnerOutcome] = []

    async def hook(o: RunnerOutcome) -> None:
        captured.append(o)

    reg = RunnerRegistry()
    runner = await reg.start(
        thread_id="t1",
        cfg=_cfg(),
        adapter=_FakeAdapter([{"type": "result", "total_cost_usd": 0.05}]),
        on_terminate=hook,
    )
    await runner.wait()
    assert len(captured) == 1
    assert captured[0].disposition == "natural"
    assert captured[0].cost_usd == 0.05


@pytest.mark.asyncio
async def test_registry_gc_drops_finished_entry_after_retention():
    """A finished entry is swept away after ``idle_retention_seconds``
    so reconnects past that window start fresh."""
    reg = RunnerRegistry(idle_retention_seconds=0.05)
    runner = await reg.start(thread_id="t1", cfg=_cfg(), adapter=_FakeAdapter([{"type": "result"}]))
    await runner.wait()
    assert reg.get("t1") is runner
    # Wait past the retention window.
    await asyncio.sleep(0.15)
    assert reg.get("t1") is None


@pytest.mark.asyncio
async def test_registry_shutdown_cancels_all_active_runners():
    reg = RunnerRegistry()
    fake1 = _FakeAdapter([{"type": "x"}], hang_after=1)
    fake2 = _FakeAdapter([{"type": "x"}], hang_after=1)
    r1 = await reg.start(thread_id="t1", cfg=_cfg(), adapter=fake1)
    r2 = await reg.start(thread_id="t2", cfg=_cfg(), adapter=fake2)
    await asyncio.sleep(0.01)
    await reg.shutdown()
    assert r1.done() and r1.disposition == "cancelled"
    assert r2.done() and r2.disposition == "cancelled"
    assert reg.get("t1") is None
    assert reg.get("t2") is None
