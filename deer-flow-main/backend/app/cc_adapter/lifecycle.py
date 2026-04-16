"""Subprocess lifecycle: spawn, stream stdout lines, capture stderr, terminate."""

from __future__ import annotations

import asyncio
import signal
from collections import deque
from collections.abc import AsyncIterator


class CCProcess:
    """Async wrapper over a CC subprocess. Stream stdout line-by-line, capture stderr."""

    STDERR_TAIL_LINES = 500

    def __init__(self, cmd: list[str], cwd: str, env: dict[str, str]) -> None:
        """Initialize. `env` is passed verbatim to the subprocess — no inheritance.
        Passing `{}` yields an empty environment (no PATH), so bare commands like
        `claude` will not be found. Callers that need PATH/HOME/etc. must populate
        `env` explicitly (see `CCAdapter.build_env`).
        """
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_buf: deque[bytes] = deque(maxlen=self.STDERR_TAIL_LINES)
        self._stderr_reader_task: asyncio.Task | None = None

    async def _spawn(self) -> None:
        if self._proc is not None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            cwd=self.cwd,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_reader_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        async for line in self._proc.stderr:
            self._stderr_buf.append(line)

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield raw stdout lines (including trailing newline)."""
        await self._spawn()
        assert self._proc and self._proc.stdout
        async for line in self._proc.stdout:
            yield line

    async def wait(self) -> int:
        if self._proc is None:
            raise RuntimeError("wait() called before stream()")
        code = await self._proc.wait()
        if self._stderr_reader_task:
            await self._stderr_reader_task
        return code

    async def terminate(self, grace_seconds: float = 2.0) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        self._proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=grace_seconds)
        except TimeoutError:
            self._proc.kill()
            await self._proc.wait()

    def stderr_tail(self) -> bytes:
        return b"".join(self._stderr_buf)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None
