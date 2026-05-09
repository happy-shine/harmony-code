"""ThreadRunner + RunnerRegistry: own the CC subprocess across HTTP requests.

A ``ThreadRunner`` represents one in-flight CC turn for a thread. It
spawns the subprocess (via :class:`CCAdapter`) inside a background
``asyncio.Task`` and writes every frame the adapter yields into a
per-runner :class:`EventBus`. SSE handlers attach as bus subscribers —
they do not own the lifecycle, and disconnecting them does not kill the
subprocess. This is the core decoupling that lets long agent runs
survive flaky networks, page reloads, and idle-timeout-happy proxies.

Termination paths
-----------------
The runner ends in exactly one of three dispositions:

- ``natural``: the adapter generator exhausts cleanly (CC emitted its
  terminal ``result`` frame and the process exited 0).
- ``cancelled``: ``runner.cancel()`` was called — typically by
  ``POST /cancel``. The driver task is cancelled, which propagates as
  ``GeneratorExit`` into the adapter and terminates CC via SIGTERM.
- ``error``: anything else — CC nonzero exit, adapter exception,
  timeout frame, etc. The terminal frame on the bus carries the
  diagnostic.

After termination the bus is ``close()``d so all subscribers wake up
and exit. The runner stays in the registry for ``IDLE_RETENTION_SECONDS``
afterwards so a reconnecting client can still replay the buffer.

Audit
-----
The ``cc.spawn`` event is emitted by the *router* before calling
``RunnerRegistry.start`` (it has the policy context: mcp/skills lists,
prompt length). The ``cc.result`` event is emitted by the runner itself
when the driver task completes — that's the only place that knows the
disposition + duration + cost without racing the SSE handler.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from .adapter import CCAdapter
from .event_bus import EventBus
from .types import SpawnConfig

logger = logging.getLogger(__name__)

Disposition = Literal["natural", "cancelled", "error"]


@dataclass
class RunnerOutcome:
    """Terminal state of a finished runner — passed to the on_terminate hook."""

    disposition: Disposition
    exit_code: int
    cost_usd: float | None
    session_id: str | None
    duration_ms: int


class ThreadRunner:
    """Owns one CC run for a thread. Lifecycle is independent of any HTTP request."""

    def __init__(
        self,
        *,
        thread_id: str,
        cfg: SpawnConfig,
        adapter: CCAdapter | None = None,
        on_terminate: Callable[[RunnerOutcome], Awaitable[None]] | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.cfg = cfg
        # ``run_id`` namespaces SSE event ids: a client reconnecting with
        # ``Last-Event-ID`` from a *previous* run gets no false replay
        # (the run_ids won't match → resume from 0).
        self.run_id = f"r_{uuid.uuid4().hex[:12]}"
        self.bus = EventBus()
        self._adapter = adapter or CCAdapter()
        self._on_terminate = on_terminate
        self._task: asyncio.Task | None = None
        self._started_at: float = 0.0
        self._finished_at: float | None = None
        # Captured from frames as they fly past, so the on_terminate
        # hook (and audit) can report them without re-scanning the bus.
        self._observed_session_id: str | None = cfg.resume_session_id
        self._cost_usd: float | None = None
        self._exit_code: int = 0
        self._disposition: Disposition = "error"  # default until proven otherwise
        self._cancel_requested: bool = False

    # -- public API ---------------------------------------------------

    def start(self) -> None:
        """Kick off the background driver task. Must be called exactly once."""
        if self._task is not None:
            raise RuntimeError("ThreadRunner.start called twice")
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(self._drive(), name=f"runner:{self.thread_id}")

    def done(self) -> bool:
        """True once the driver task has finished and the bus is closed."""
        return self._task is not None and self._task.done()

    @property
    def disposition(self) -> Disposition | None:
        """Final disposition once ``done()``; ``None`` while still running."""
        return self._disposition if self.done() else None

    @property
    def session_id(self) -> str | None:
        """Latest known CC session_id (captured from ``system.init`` frame)."""
        return self._observed_session_id

    @property
    def finished_at(self) -> float | None:
        """``time.monotonic()`` value when the runner finished, or None."""
        return self._finished_at

    async def cancel(self) -> None:
        """Request cancellation. Returns once the driver task has finished
        (terminal frame on bus, bus closed, on_terminate fired). Safe to
        call multiple times — subsequent calls just await completion."""
        self._cancel_requested = True
        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            # Driver swallows its own CancelledError to run cleanup;
            # if it propagated anyway (rare), don't bubble out of cancel().
            pass
        except Exception:  # pragma: no cover - defensive
            pass

    async def wait(self) -> None:
        """Block until the runner finishes naturally or via cancel."""
        if self._task is None:
            return
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    # -- internals ----------------------------------------------------

    async def _drive(self) -> None:
        """Consume the adapter generator, mirror every frame into the bus,
        track terminal state, then close the bus and notify."""
        gen = self._adapter.run(self.cfg)
        natural_eof = False
        try:
            async for ev in gen:
                self._observe(ev)
                self.bus.publish(ev)
                # If the adapter emitted a synthetic timeout/error frame
                # we still let the loop drain — adapter.run handles its
                # own subprocess teardown after yielding the error.
            natural_eof = True
        except asyncio.CancelledError:
            # Cancellation: aclose the generator, which triggers the
            # adapter's GeneratorExit handler — that SIGTERMs CC and
            # reaps. We must NOT re-raise here; the runner owns its
            # disposition reporting and we want a clean unwind so the
            # ``finally`` below fires before the task is marked done.
            self._disposition = "cancelled"
            self._exit_code = -1
            try:
                await gen.aclose()
            except Exception as e:  # pragma: no cover - defensive
                logger.debug("adapter aclose during cancel swallowed: %r", e)
            # Publish a synthetic frame so subscribers see *why* the run
            # ended even if no terminal frame from CC made it through.
            self.bus.publish(
                {
                    "type": "_adapter",
                    "subtype": "error",
                    "code": "cancelled",
                }
            )
        except Exception as e:
            logger.exception("runner driver crashed: %r", e)
            self._disposition = "error"
            self._exit_code = -1
            self.bus.publish(
                {
                    "type": "_adapter",
                    "subtype": "error",
                    "code": "driver_crashed",
                    "detail": repr(e),
                }
            )
            try:
                await gen.aclose()
            except Exception:  # pragma: no cover - defensive
                pass
        else:
            # Natural completion: the adapter generator exhausted without
            # cancellation or driver crash. Disposition is "natural" even
            # if no terminal ``result`` frame arrived — the presence of a
            # ``cc_nonzero_exit`` frame is captured separately in
            # ``exit_code``, mirroring the pre-runner gateway behavior so
            # audit consumers don't need to relearn the disposition labels.
            if natural_eof:
                self._disposition = "natural"
        finally:
            self._finished_at = time.monotonic()
            # Run the on_terminate hook BEFORE closing the bus so that
            # by the time SSE subscribers see the bus close (and emit
            # their terminal ``done`` event), audit logging and slot
            # release have already happened. Otherwise tests that read
            # caplog right after the SSE stream ends would race the
            # hook and see zero audit lines. The hook is best-effort —
            # if it raises, the bus still closes and subscribers still
            # unblock.
            if self._on_terminate is not None:
                duration_ms = int((self._finished_at - self._started_at) * 1000)
                outcome = RunnerOutcome(
                    disposition=self._disposition,
                    exit_code=self._exit_code,
                    cost_usd=self._cost_usd,
                    session_id=self._observed_session_id,
                    duration_ms=duration_ms,
                )
                try:
                    await self._on_terminate(outcome)
                except Exception as e:  # pragma: no cover - hook is best-effort
                    logger.warning("runner on_terminate hook raised: %r", e)
            self.bus.close()

    def _observe(self, ev: dict) -> None:
        """Pull bookkeeping bits out of frames as they fly past."""
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            sid = ev.get("session_id")
            if isinstance(sid, str):
                self._observed_session_id = sid
        elif t == "result":
            cost = ev.get("total_cost_usd")
            if cost is None:
                cost = ev.get("cost_usd")
            if cost is not None:
                self._cost_usd = cost
        elif t == "_adapter" and ev.get("subtype") == "error":
            code = ev.get("code")
            if code == "cc_nonzero_exit":
                ec = ev.get("exit_code")
                if isinstance(ec, int):
                    self._exit_code = ec
            elif code == "timeout":
                self._exit_code = -1

# -- registry ---------------------------------------------------------


@dataclass
class _RegistryEntry:
    runner: ThreadRunner
    # Set when the runner finishes; older finished entries are GC'd.
    sweeper_handle: asyncio.TimerHandle | None = field(default=None)


class RunnerRegistry:
    """thread_id → ThreadRunner. One runner per thread at a time.

    Finished runners stick around for ``idle_retention_seconds`` so SSE
    clients that reconnect after the run finished can still drain the
    bus replay buffer — typical use case: user closed the laptop, opens
    it ten minutes later, and the UI reconnects to find the assistant's
    final reply still buffered. Past that retention window the entry is
    GC'd and reconnects start from scratch.
    """

    DEFAULT_IDLE_RETENTION_SECONDS = 30 * 60

    def __init__(
        self,
        *,
        idle_retention_seconds: float = DEFAULT_IDLE_RETENTION_SECONDS,
    ) -> None:
        self._idle_retention = idle_retention_seconds
        self._entries: dict[str, _RegistryEntry] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        thread_id: str,
        cfg: SpawnConfig,
        adapter: CCAdapter | None = None,
        on_terminate: Callable[[RunnerOutcome], Awaitable[None]] | None = None,
    ) -> ThreadRunner:
        """Create and start a runner for ``thread_id``. Raises ``RuntimeError``
        if an active runner already exists — callers must check
        :meth:`active` and surface the right HTTP code."""
        async with self._lock:
            existing = self._entries.get(thread_id)
            if existing is not None and not existing.runner.done():
                raise RuntimeError(f"runner already active for thread {thread_id!r}")
            # If a finished entry still lingers, drop it and its sweeper.
            if existing is not None:
                self._dispose_locked(thread_id)

            wrapped = self._wrap_terminate(thread_id, on_terminate)
            runner = ThreadRunner(
                thread_id=thread_id,
                cfg=cfg,
                adapter=adapter,
                on_terminate=wrapped,
            )
            self._entries[thread_id] = _RegistryEntry(runner=runner)
            runner.start()
            return runner

    def get(self, thread_id: str) -> ThreadRunner | None:
        entry = self._entries.get(thread_id)
        return entry.runner if entry else None

    def active(self, thread_id: str) -> bool:
        """True if there is a runner for the thread that has not finished."""
        entry = self._entries.get(thread_id)
        return entry is not None and not entry.runner.done()

    async def cancel(self, thread_id: str) -> bool:
        """Cancel an active runner. Returns True if a cancel was issued,
        False if there was no active runner to cancel."""
        entry = self._entries.get(thread_id)
        if entry is None or entry.runner.done():
            return False
        await entry.runner.cancel()
        return True

    def _wrap_terminate(
        self,
        thread_id: str,
        user_hook: Callable[[RunnerOutcome], Awaitable[None]] | None,
    ) -> Callable[[RunnerOutcome], Awaitable[None]]:
        """Compose the user's on_terminate hook with our GC scheduling."""
        registry = self

        async def _on_terminate(outcome: RunnerOutcome) -> None:
            try:
                if user_hook is not None:
                    await user_hook(outcome)
            finally:
                registry._schedule_gc(thread_id)

        return _on_terminate

    def _schedule_gc(self, thread_id: str) -> None:
        """Arrange for a finished entry to be removed after the retention
        window. Uses ``loop.call_later`` so we don't keep a task alive
        per finished thread — the timer handle is fire-and-forget."""
        loop = asyncio.get_running_loop()
        entry = self._entries.get(thread_id)
        if entry is None:
            return

        def _sweep() -> None:
            # Re-check existence: a new run might have replaced this
            # entry before the timer fired.
            cur = self._entries.get(thread_id)
            if cur is entry:
                self._entries.pop(thread_id, None)

        entry.sweeper_handle = loop.call_later(self._idle_retention, _sweep)

    def _dispose_locked(self, thread_id: str) -> None:
        """Caller must hold ``self._lock``. Tears down the entry's sweeper
        and removes it from the map."""
        entry = self._entries.pop(thread_id, None)
        if entry is None:
            return
        if entry.sweeper_handle is not None:
            entry.sweeper_handle.cancel()

    async def shutdown(self) -> None:
        """Cancel every active runner and clear the map. For test teardown
        and graceful server shutdown — never call from request handlers."""
        async with self._lock:
            entries = list(self._entries.items())
        for tid, entry in entries:
            if not entry.runner.done():
                await entry.runner.cancel()
            if entry.sweeper_handle is not None:
                entry.sweeper_handle.cancel()
        async with self._lock:
            self._entries.clear()
