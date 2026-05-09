"""EventBus: per-thread pub/sub with bounded replay buffer.

Covers the contract that the runner + SSE handler depend on:
- monotonic event_ids starting at 1
- replay from arbitrary cursor
- multiple concurrent subscribers see identical sequences
- close() wakes blocked subscribers
- cursor underrun emits a synthetic lost_events frame
- publish() after close() raises
"""

from __future__ import annotations

import asyncio

import pytest

from app.cc_adapter.event_bus import EventBus


@pytest.mark.asyncio
async def test_publish_assigns_monotonic_ids_starting_at_one():
    bus = EventBus()
    assert bus.publish({"type": "a"}) == 1
    assert bus.publish({"type": "b"}) == 2
    assert bus.publish({"type": "c"}) == 3
    assert bus.next_id == 4


@pytest.mark.asyncio
async def test_subscribe_replays_buffered_events_from_cursor():
    bus = EventBus()
    bus.publish({"type": "a"})
    bus.publish({"type": "b"})
    bus.publish({"type": "c"})
    bus.close()

    # Subscriber starting from scratch sees all three.
    out = [(eid, ev) async for eid, ev in bus.subscribe(after_event_id=0)]
    assert [eid for eid, _ in out] == [1, 2, 3]
    assert [ev["type"] for _, ev in out] == ["a", "b", "c"]

    # Subscriber resuming from id=2 sees only event 3.
    out = [(eid, ev) async for eid, ev in bus.subscribe(after_event_id=2)]
    assert out == [(3, {"type": "c"})]


@pytest.mark.asyncio
async def test_subscribe_blocks_for_live_events_until_close():
    bus = EventBus()
    received: list[tuple[int, dict]] = []

    async def reader() -> None:
        async for pair in bus.subscribe(after_event_id=0):
            received.append(pair)

    task = asyncio.create_task(reader())
    # Let the reader reach its first ``await signal.wait()``.
    await asyncio.sleep(0.01)
    bus.publish({"type": "x"})
    await asyncio.sleep(0.01)
    bus.publish({"type": "y"})
    await asyncio.sleep(0.01)
    bus.close()
    await asyncio.wait_for(task, timeout=1.0)

    assert [ev["type"] for _, ev in received] == ["x", "y"]


@pytest.mark.asyncio
async def test_two_subscribers_see_identical_sequence():
    bus = EventBus()
    a: list[int] = []
    b: list[int] = []

    async def reader(out: list[int]) -> None:
        async for eid, _ev in bus.subscribe(after_event_id=0):
            out.append(eid)

    t1 = asyncio.create_task(reader(a))
    t2 = asyncio.create_task(reader(b))
    await asyncio.sleep(0.01)
    for i in range(5):
        bus.publish({"type": "e", "i": i})
    await asyncio.sleep(0.01)
    bus.close()
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=1.0)

    assert a == [1, 2, 3, 4, 5]
    assert b == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_close_wakes_blocked_subscriber_with_no_events():
    bus = EventBus()

    async def reader() -> int:
        count = 0
        async for _ in bus.subscribe(after_event_id=0):
            count += 1
        return count

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.01)
    bus.close()
    n = await asyncio.wait_for(task, timeout=1.0)
    assert n == 0


@pytest.mark.asyncio
async def test_cursor_underrun_emits_lost_events_then_resumes():
    """A subscriber whose cursor is older than the oldest buffered event
    receives a single synthetic ``lost_events`` frame at id=0, then
    streams from the oldest available id forward."""
    bus = EventBus(max_buffer=3)
    for i in range(5):
        bus.publish({"type": "e", "i": i})
    bus.close()
    # Buffer now holds ids 3,4,5 (1 and 2 dropped).
    out = [(eid, ev) async for eid, ev in bus.subscribe(after_event_id=0)]
    # First frame is the synthetic lost_events marker.
    assert out[0][0] == 0
    assert out[0][1]["type"] == "_adapter"
    assert out[0][1]["code"] == "lost_events"
    assert out[0][1]["from_event_id"] == 1
    assert out[0][1]["oldest_available"] == 3
    # Then ids 3,4,5 in order.
    assert [eid for eid, _ in out[1:]] == [3, 4, 5]


@pytest.mark.asyncio
async def test_cursor_at_or_after_oldest_does_not_emit_lost_events():
    bus = EventBus(max_buffer=3)
    for _ in range(5):
        bus.publish({"type": "e"})
    bus.close()
    # Oldest buffered id is 3. Subscriber at cursor=2 means "next id I
    # want is 3" — that IS available. No lost_events expected.
    out = [(eid, ev) async for eid, ev in bus.subscribe(after_event_id=2)]
    assert out[0][0] == 3
    assert all(ev["type"] != "_adapter" for _, ev in out)


@pytest.mark.asyncio
async def test_publish_after_close_raises():
    bus = EventBus()
    bus.publish({"type": "a"})
    bus.close()
    with pytest.raises(RuntimeError):
        bus.publish({"type": "b"})


@pytest.mark.asyncio
async def test_close_is_idempotent():
    bus = EventBus()
    bus.close()
    bus.close()  # must not raise
    assert bus.closed


@pytest.mark.asyncio
async def test_late_subscriber_after_close_replays_buffer():
    """A subscriber that joins after close() still gets the full
    buffered history — useful when the SSE client reconnects after the
    run finished."""
    bus = EventBus()
    bus.publish({"type": "a"})
    bus.publish({"type": "b"})
    bus.close()
    out = [pair async for pair in bus.subscribe(after_event_id=0)]
    assert [ev["type"] for _, ev in out] == ["a", "b"]
