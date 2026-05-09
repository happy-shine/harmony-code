"""Per-thread event bus that decouples CC subprocess output from HTTP delivery.

The CC subprocess (lifecycle owned by ``ThreadRunner``) publishes every
frame it produces into a thread-scoped ``EventBus``. SSE handlers are
plain subscribers — they pull from the bus, never drive it. Disconnecting
a subscriber does NOT signal the runner; the runner keeps writing to the
bus until CC exits naturally or someone calls ``runner.cancel()`` via the
``/cancel`` endpoint.

This is the piece that makes long agent runs survive flaky networks,
page reloads, tab switches, and corporate proxies that close idle TCP
connections after 60s.

Design
------
- Each event gets a monotonic ``event_id`` (int, starting at 1). Clients
  pass ``Last-Event-ID`` on reconnect to resume from the next id.
- The bus keeps a bounded ring buffer (``max_buffer``). A subscriber
  whose cursor falls off the back of the buffer receives a synthetic
  ``_adapter.error`` frame with ``code="lost_events"`` and is expected
  to fall back to the history endpoint to refetch the missed range.
  This is a tradeoff: bounded memory for unbounded run length, at the
  cost of an explicit "you fell behind" failure mode for slow clients.
- ``close()`` marks the bus terminal. Subscribers caught up to the end
  of the buffer wake up, see ``closed=True``, and exit their loops.
  The terminal frame itself (the CC ``result`` event or an
  ``_adapter.error``) is *always* published before ``close()``.
- Multiple concurrent subscribers are supported (e.g. same thread open
  in two browser tabs). Each gets the full event sequence.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator


class EventBus:
    """Per-thread, in-memory event broadcaster with replay buffer."""

    # Default ring-buffer capacity. A single long agent run can produce
    # thousands of partial-message deltas; 10k events ~= a few MB at
    # typical frame sizes. Tuneable per instance for tests.
    DEFAULT_MAX_BUFFER = 10_000

    def __init__(self, *, max_buffer: int = DEFAULT_MAX_BUFFER) -> None:
        self._max_buffer = max_buffer
        # Each entry is (event_id, event_dict). ``deque(maxlen=N)`` drops
        # from the left as new entries are appended past capacity — the
        # exact behavior we want for "oldest events fall off".
        self._buffer: deque[tuple[int, dict]] = deque(maxlen=max_buffer)
        self._next_id: int = 1
        self._closed: bool = False
        # Single shared asyncio.Event signaling "buffer changed" — woken
        # on every ``publish`` and on ``close``. Subscribers ``wait()``
        # then re-arm. We replace the Event on each wake so multiple
        # waiters can be released atomically (asyncio.Event.set/clear is
        # not edge-triggered for late waiters).
        self._signal: asyncio.Event = asyncio.Event()

    # -- writer side --------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def next_id(self) -> int:
        """The id that the *next* published event will receive. Useful for
        tests and for callers that want to record the watermark before
        publishing terminal frames."""
        return self._next_id

    def publish(self, event: dict) -> int:
        """Append ``event`` to the buffer and wake all subscribers.

        Returns the assigned ``event_id``. Calling ``publish`` after
        ``close`` raises — the contract is that the runner publishes
        every frame including the terminal one *before* closing.
        """
        if self._closed:
            raise RuntimeError("publish() on a closed EventBus")
        eid = self._next_id
        self._next_id += 1
        self._buffer.append((eid, event))
        self._wake()
        return eid

    def close(self) -> None:
        """Mark the bus terminal. Idempotent. Wakes all subscribers so
        they can observe ``closed=True`` and exit their loops."""
        if self._closed:
            return
        self._closed = True
        self._wake()

    def _wake(self) -> None:
        # Set the current signal (releasing all current waiters), then
        # immediately swap in a fresh Event so the next ``publish`` has
        # a clean edge to trigger on. This avoids the
        # "set-then-clear-races-with-waiter" hole in asyncio.Event.
        old = self._signal
        self._signal = asyncio.Event()
        old.set()

    # -- reader side --------------------------------------------------

    def _oldest_id(self) -> int | None:
        """Smallest event id still in the buffer, or ``None`` if empty."""
        return self._buffer[0][0] if self._buffer else None

    async def subscribe(self, after_event_id: int = 0) -> AsyncIterator[tuple[int, dict]]:
        """Yield ``(event_id, event)`` pairs strictly after ``after_event_id``.

        Replays buffered events the subscriber missed, then streams live
        publishes until the bus is closed AND the cursor has reached
        ``next_id - 1``. On subscribe, if ``after_event_id`` is older
        than the oldest buffered event (cursor fell off the back), one
        synthetic ``_adapter.error`` ``code="lost_events"`` frame is
        yielded with ``event_id=0`` so the client knows to refetch via
        the history endpoint. The subscriber then resumes from the
        oldest available id.
        """
        cursor = after_event_id
        # Detect cursor underrun BEFORE entering the main loop. The
        # check is "client asked for X+1, but our oldest is > X+1".
        # Equivalently: client's last seen id < oldest buffered id - 1.
        oldest = self._oldest_id()
        if oldest is not None and cursor + 1 < oldest:
            yield (
                0,
                {
                    "type": "_adapter",
                    "subtype": "error",
                    "code": "lost_events",
                    "from_event_id": cursor + 1,
                    "oldest_available": oldest,
                },
            )
            cursor = oldest - 1

        while True:
            # Snapshot the wake-signal *before* draining the buffer so
            # any publish that races our drain still triggers the next
            # wait. (publish swaps the signal under us, but we hold the
            # reference to the old one — and old.set() has already been
            # called by then if a publish raced in.)
            signal = self._signal
            drained_any = False
            for eid, ev in list(self._buffer):
                if eid > cursor:
                    cursor = eid
                    drained_any = True
                    yield (eid, ev)
            # If the bus is closed and we've caught up to the latest
            # published id, we're done.
            if self._closed and cursor >= self._next_id - 1:
                return
            # If we drained nothing AND the signal already fired (race:
            # publish came in between snapshot and check), loop again
            # without blocking — the next iteration's drain will catch
            # the new entry.
            if not drained_any and signal.is_set():
                continue
            # Block until the next publish or close.
            await signal.wait()
