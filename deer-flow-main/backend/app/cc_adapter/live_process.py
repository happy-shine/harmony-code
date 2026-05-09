"""Long-lived ``claude -p --input-format stream-json`` subprocess.

Matches the openclaude pattern: spawn once per thread, feed messages via
stdin, read stream-json events via stdout. Amortizes the ~9s CC CLI
cold-start cost across every turn in that thread — first message is
slow, subsequent ones skip plugin sync / SessionStart hooks / MCP
handshake entirely.

Lifecycle is owned by :class:`app.cc_adapter.pool.CCProcessPool`. This
class is a thin wrapper — one process, one send loop at a time, no
implicit respawn. The pool handles LRU eviction and config-change
invalidation.

Design notes
------------
- ``send()`` is an async generator. The caller reads frames until the
  generator exits naturally (``result`` frame arrived) or raises.
  Concurrent sends against the same process corrupt CC's turn state
  irrecoverably, so callers MUST ensure serialized access via the
  per-thread admission lock already enforced by the messages router.
- We intentionally do NOT emit synthetic ``_adapter.spawning`` /
  ``_adapter.spawned`` frames here. Those are spawn-events and the
  router forwards them explicitly on the first turn. A long-lived
  process that reuses its subprocess should yield nothing extra.
- ``session_id`` is captured opportunistically from the first
  ``system.init`` frame. We expose it so the pool can persist it for
  respawns (``--resume <sid>``) that preserve conversation continuity.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from collections import deque
from collections.abc import AsyncIterator


class LiveCCProcess:
    """Async wrapper over a long-lived claude subprocess with JSON stdin/stdout."""

    STDERR_TAIL_LINES = 200

    def __init__(self, cmd: list[str], cwd: str, env: dict[str, str]) -> None:
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._session_id: str | None = None
        self._last_used_at: float = time.time()
        self._stderr_buf: deque[bytes] = deque(maxlen=self.STDERR_TAIL_LINES)
        self._stderr_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            cwd=self.cwd,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        async for line in self._proc.stderr:
            self._stderr_buf.append(line)

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def last_used_at(self) -> float:
        return self._last_used_at

    async def send(
        self,
        content: str,
        attachments: list[str] | None = None,
    ) -> AsyncIterator[dict]:
        """Feed one user turn and yield every event until the ``result`` frame.

        The CC ``--input-format stream-json`` protocol expects one JSON
        object per line on stdin. Payload shape is
        ``{"type":"user","message":{"role":"user","content": <string>}}``
        — mirrors openclaude's wire format and matches what CC writes
        into its own session jsonl for user turns.
        """
        if not self.alive or self._proc is None or self._proc.stdin is None:
            raise RuntimeError("LiveCCProcess.send called on a dead/unstarted process")
        self._last_used_at = time.time()

        msg: dict = {
            "type": "user",
            "message": {"role": "user", "content": content},
        }
        if attachments:
            # CC currently ignores extras but leaving the key here keeps
            # the wire shape forward-compatible with a future attachments
            # channel — mirrors the REST body we already persist.
            msg["attachments"] = list(attachments)
        line = (json.dumps(msg) + "\n").encode("utf-8")
        self._proc.stdin.write(line)
        try:
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            # Subprocess died between the last turn and this one (e.g.
            # OOM). Surface a clean RuntimeError; the pool will notice
            # ``alive == False`` afterwards and spawn a replacement.
            raise RuntimeError("cc subprocess died before accepting input") from e

        assert self._proc.stdout is not None
        async for raw in self._proc.stdout:
            self._last_used_at = time.time()
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                # CC sometimes intersperses non-JSON diagnostic lines on
                # stdout (legacy behavior). Skip rather than terminate.
                continue
            if (
                event.get("type") == "system"
                and event.get("subtype") == "init"
                and self._session_id is None
            ):
                sid = event.get("session_id")
                if isinstance(sid, str):
                    self._session_id = sid
            yield event
            if event.get("type") == "result":
                # End of turn. Stop reading — the process is ready for
                # the next stdin message.
                return
        # stdout closed without a result frame — process died mid-turn.
        # Caller sees the generator exhausted; pool notices on next
        # ``alive`` probe.

    async def terminate(self, grace_seconds: float = 2.0) -> None:
        if not self.alive or self._proc is None:
            return
        # Close stdin first so CC exits its read loop cleanly instead of
        # hanging on a pending readline when we SIGTERM.
        try:
            if self._proc.stdin and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
        except Exception:
            # stdin transport may already be broken; SIGTERM will still work
            pass
        try:
            self._proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=grace_seconds)
        except TimeoutError:
            try:
                self._proc.kill()
            except ProcessLookupError:
                return
            await self._proc.wait()
        if self._stderr_task is not None:
            try:
                await self._stderr_task
            except Exception:
                pass

    def stderr_tail(self) -> bytes:
        return b"".join(self._stderr_buf)
