"""CCAdapter: compose CLI args, spawn CC, yield jsonl events."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

from .lifecycle import CCProcess
from .stream_parser import StreamParser
from .types import SpawnConfig


class CCAdapter:
    """Stateless adapter — one instance can handle many runs."""

    # Allowlist of env vars passed to CC subprocess.
    # USER/LOGNAME are needed for macOS Keychain fallback — the CLI resolves
    # the login keychain owner from them.
    ENV_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "USER", "LOGNAME")
    ENV_CLAUDE_PREFIX = "CLAUDE_CODE_"
    # ``CLAUDE_CODE_*`` vars that encode short-lived OAuth state from a host
    # launcher (claude-desktop/Claude Code). If the harmony-code backend was
    # itself spawned inside such a host, these vars are captured at startup
    # and GO STALE when the host rotates the token — causing 401 on every
    # subsequent ``claude -p`` spawn. Drop them so the CLI falls back to
    # the Keychain credentials, which the user manages via ``claude login``.
    ENV_CLAUDE_BLOCKLIST = (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
        "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
        "CLAUDE_CODE_ENTRYPOINT",
    )

    def build_cmd(self, cfg: SpawnConfig) -> list[str]:
        # ``--include-partial-messages`` makes CC emit token-by-token
        # assistant deltas instead of one frame per completed message.
        # Without it, a long reply appears in the UI only after generation
        # finishes — defeating the whole point of an SSE pipeline.
        cmd = [
            "claude",
            "-p",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--permission-mode", cfg.permission_mode,
        ]
        if cfg.resume_session_id:
            cmd += ["--resume", cfg.resume_session_id]
        if cfg.mcp_config_path:
            cmd += ["--mcp-config", cfg.mcp_config_path]
        if cfg.model:
            cmd += ["--model", cfg.model]
        for d in cfg.add_dirs:
            cmd += ["--add-dir", d]
        # ``--mcp-config`` and ``--add-dir`` are declared as variadic in the
        # CC CLI (``<configs...>`` / ``<directories...>``), so Commander.js
        # greedily consumes subsequent positionals into whichever of them
        # came last. A ``--`` terminator stops that consumption and forces
        # the remaining token to be parsed as the prompt positional.
        cmd.append("--")
        cmd.append(cfg.user_prompt)
        return cmd

    def build_env(self, cfg: SpawnConfig) -> dict[str, str]:
        """Build subprocess env from allowlisted vars + CLAUDE_CODE_* + cfg.extra_env.

        Precedence: `cfg.extra_env` overrides both the allowlisted passthrough vars
        (PATH, HOME, LANG, LC_ALL, TZ) and any inherited CLAUDE_CODE_* vars. This is
        intentional — callers passing e.g. `extra_env={"PATH": ""}` get an empty PATH
        (a deliberate test escape hatch for simulating a missing claude binary).
        """
        env: dict[str, str] = {}
        for k in self.ENV_PASSTHROUGH:
            if k in os.environ:
                env[k] = os.environ[k]
        for k, v in os.environ.items():
            if k.startswith(self.ENV_CLAUDE_PREFIX) and k not in self.ENV_CLAUDE_BLOCKLIST:
                env[k] = v
        env.update(cfg.extra_env)
        return env

    async def run(self, cfg: SpawnConfig) -> AsyncIterator[dict]:
        """Yield dict events; each event is a CC jsonl frame OR an _adapter synthetic frame.

        Diagnostic error frames (`_adapter.error` with `code=cc_nonzero_exit`) are only
        emitted on the *natural exit* path — i.e. when CC's stdout hits EOF and we
        observe a nonzero exit code. If the consumer breaks early (e.g. after receiving
        a terminal `result` frame), the resulting `GeneratorExit` triggers best-effort
        subprocess cleanup but swallows the error frame by design: the consumer has
        already decided the run is done, and our own SIGTERM would otherwise produce a
        false-positive nonzero exit.
        """
        cmd = self.build_cmd(cfg)
        env = self.build_env(cfg)

        yield {"type": "_adapter", "subtype": "spawning", "cmd_argc": len(cmd)}

        proc = CCProcess(cmd=cmd, cwd=cfg.cwd, env=env)
        parser = StreamParser()
        yielded_spawned = False
        try:
            # Wrap the stream loop in a wall-clock budget if configured.
            # ``asyncio.timeout(None)`` is a no-op so the two branches
            # collapse to one body. Python 3.11+ gives us the context
            # manager form, which plays nicely with async generators —
            # ``wait_for`` is clumsier against an ``async for`` body.
            try:
                async with asyncio.timeout(cfg.timeout_seconds):
                    async for raw in proc.stream():
                        if not yielded_spawned:
                            # `spawned` fires on the first stdout line, not on fork success —
                            # a CC process that dies before writing anything will never produce
                            # this frame. Treat absence of `spawned` as "CC never started talking".
                            yield {"type": "_adapter", "subtype": "spawned", "pid": proc.pid}
                            yielded_spawned = True
                        event, _ = parser.feed_line(raw)
                        if event is not None:
                            yield event
            except TimeoutError:
                # Timeout: emit synthetic error frame, terminate CC, return
                # cleanly. We do NOT fall through to the nonzero-exit path
                # below — our SIGTERM would otherwise produce a false-positive
                # ``cc_nonzero_exit`` diagnostic on top of the timeout frame.
                yield {
                    "type": "_adapter",
                    "subtype": "error",
                    "code": "timeout",
                    "timeout_seconds": cfg.timeout_seconds,
                }
                await proc.terminate()
                await proc.wait()
                return
            # Natural exit: CC's stdout hit EOF. Safe to yield diagnostics.
            code = await proc.wait()
            if code != 0:
                yield {
                    "type": "_adapter",
                    "subtype": "error",
                    "code": "cc_nonzero_exit",
                    "exit_code": code,
                    "stderr_tail": proc.stderr_tail().decode("utf-8", errors="replace")[-2000:],
                }
        except GeneratorExit:
            # Consumer broke early (e.g. after result). We MUST NOT yield here —
            # Python 3.8+ raises RuntimeError if an async generator yields after
            # GeneratorExit. Best-effort cleanup: terminate CC and reap. Errors
            # are swallowed by design; the consumer already decided it's done.
            await proc.terminate()
            await proc.wait()
            raise
        except BaseException:
            # Any other exception: still need to clean up the subprocess, then re-raise.
            await proc.terminate()
            await proc.wait()
            raise
