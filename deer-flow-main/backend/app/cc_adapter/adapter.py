"""CCAdapter: compose CLI args, spawn CC, yield jsonl events."""
from __future__ import annotations

import os
from typing import AsyncIterator

from .lifecycle import CCProcess
from .stream_parser import StreamParser
from .types import SpawnConfig


class CCAdapter:
    """Stateless adapter — one instance can handle many runs."""

    # Allowlist of env vars passed to CC subprocess
    ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
    ENV_CLAUDE_PREFIX = "CLAUDE_CODE_"

    def build_cmd(self, cfg: SpawnConfig) -> list[str]:
        cmd = ["claude", "-p",
               "--output-format", "stream-json",
               "--verbose",
               "--permission-mode", cfg.permission_mode]
        if cfg.resume_session_id:
            cmd += ["--resume", cfg.resume_session_id]
        if cfg.mcp_config_path:
            cmd += ["--mcp-config", cfg.mcp_config_path]
        if cfg.model:
            cmd += ["--model", cfg.model]
        for d in cfg.add_dirs:
            cmd += ["--add-dir", d]
        cmd.append(cfg.user_prompt)
        return cmd

    def build_env(self, cfg: SpawnConfig) -> dict[str, str]:
        env: dict[str, str] = {}
        for k in self.ENV_PASSTHROUGH:
            if k in os.environ:
                env[k] = os.environ[k]
        for k, v in os.environ.items():
            if k.startswith(self.ENV_CLAUDE_PREFIX):
                env[k] = v
        env.update(cfg.extra_env)
        return env

    async def run(self, cfg: SpawnConfig) -> AsyncIterator[dict]:
        """Yield dict events; each event is a CC jsonl frame OR an _adapter synthetic frame."""
        cmd = self.build_cmd(cfg)
        env = self.build_env(cfg)

        yield {"type": "_adapter", "subtype": "spawning", "cmd_argc": len(cmd)}

        proc = CCProcess(cmd=cmd, cwd=cfg.cwd, env=env)
        parser = StreamParser()
        yielded_spawned = False
        try:
            async for raw in proc.stream():
                if not yielded_spawned:
                    yield {"type": "_adapter", "subtype": "spawned", "pid": proc.pid}
                    yielded_spawned = True
                event, _ = parser.feed_line(raw)
                if event is not None:
                    yield event
        finally:
            # Consumer may break early (e.g. after seeing the `result` event) before
            # the subprocess has exited. If we called wait() on a still-running proc
            # with an open stdout pipe, we'd hang forever. Terminate first to ensure
            # wait() returns promptly; if CC already exited naturally, terminate()
            # is a no-op and wait() just reaps the exit code.
            await proc.terminate()
            code = await proc.wait()
            if code != 0:
                yield {
                    "type": "_adapter",
                    "subtype": "error",
                    "code": "cc_nonzero_exit",
                    "exit_code": code,
                    "stderr_tail": proc.stderr_tail().decode("utf-8", errors="replace")[-2000:],
                }
