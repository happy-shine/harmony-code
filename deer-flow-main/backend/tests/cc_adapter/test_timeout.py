"""Task 6.1: adapter wall-clock timeout coverage.

``SpawnConfig.timeout_seconds`` wraps CC's stream loop in
``asyncio.timeout``. On expiry the adapter must (a) emit an
``_adapter.error`` frame with ``code="timeout"``, (b) SIGTERM/SIGKILL the
subprocess, and (c) return without falling through to the
``cc_nonzero_exit`` diagnostic — a SIGTERM exit is expected here and
would produce a false-positive error frame otherwise.

These tests use a real ``/bin/sh`` subprocess (no ``claude`` dependency)
by patching ``CCAdapter.build_cmd`` / ``build_env``.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.cc_adapter.adapter import CCAdapter
from app.cc_adapter.types import SpawnConfig


async def _drain(gen, max_frames: int = 50) -> list[dict]:
    """Collect up to ``max_frames`` frames, stopping at StopAsyncIteration."""
    out: list[dict] = []
    for _ in range(max_frames):
        try:
            out.append(await gen.__anext__())
        except StopAsyncIteration:
            return out
    return out


@pytest.mark.asyncio
async def test_timeout_kills_cc_and_yields_error_frame(tmp_path, monkeypatch):
    """`sleep 60` under ``timeout_seconds=0.3`` → `_adapter.error code=timeout`
    within ~the budget, not the 60s the sleep would take."""
    # Patch build_cmd to spawn a long-running shell instead of real `claude`,
    # and build_env to use inherited PATH so /bin/sh is reachable.
    monkeypatch.setattr(
        CCAdapter,
        "build_cmd",
        lambda self, cfg: ["/bin/sh", "-c", "sleep 60"],
    )
    import os as _os

    monkeypatch.setattr(
        CCAdapter,
        "build_env",
        lambda self, cfg: {"PATH": _os.environ.get("PATH", "")},
    )

    adapter = CCAdapter()
    cfg = SpawnConfig(
        cwd=str(tmp_path),
        user_prompt="ignored",
        timeout_seconds=0.3,
    )

    gen = adapter.run(cfg)
    t0 = time.monotonic()
    try:
        frames = await _drain(gen, max_frames=20)
    finally:
        await gen.aclose()
    elapsed = time.monotonic() - t0

    # Must finish well before 60s — the SIGTERM/SIGKILL path reaps the
    # subprocess; a 5x safety margin on the 0.3s budget covers CI jitter.
    assert elapsed < 5.0, f"timeout took {elapsed:.2f}s"

    err = [f for f in frames if f.get("type") == "_adapter" and f.get("subtype") == "error" and f.get("code") == "timeout"]
    assert len(err) == 1, f"expected exactly one timeout frame, got frames={frames!r}"
    assert err[0].get("timeout_seconds") == 0.3

    # The SIGTERM path must NOT emit cc_nonzero_exit — that's reserved
    # for "CC exited on its own with a nonzero code", not "we killed it".
    nonzero = [f for f in frames if f.get("type") == "_adapter" and f.get("code") == "cc_nonzero_exit"]
    assert nonzero == [], f"timeout path should not emit cc_nonzero_exit, got: {nonzero!r}"


@pytest.mark.asyncio
async def test_no_timeout_when_unset(tmp_path, monkeypatch):
    """``timeout_seconds=None`` → quick command runs to natural EOF, no timeout frame."""
    monkeypatch.setattr(
        CCAdapter,
        "build_cmd",
        lambda self, cfg: ["/bin/sh", "-c", 'printf \'{"type":"result"}\\n\''],
    )
    import os as _os

    monkeypatch.setattr(
        CCAdapter,
        "build_env",
        lambda self, cfg: {"PATH": _os.environ.get("PATH", "")},
    )

    adapter = CCAdapter()
    cfg = SpawnConfig(cwd=str(tmp_path), user_prompt="x", timeout_seconds=None)

    gen = adapter.run(cfg)
    try:
        frames = await _drain(gen, max_frames=20)
    finally:
        await gen.aclose()

    codes = [f.get("code") for f in frames if f.get("type") == "_adapter"]
    assert "timeout" not in codes


@pytest.mark.asyncio
async def test_timeout_within_budget_no_error(tmp_path, monkeypatch):
    """Command that finishes before the budget → no timeout frame,
    and the adapter completes naturally."""
    monkeypatch.setattr(
        CCAdapter,
        "build_cmd",
        lambda self, cfg: ["/bin/sh", "-c", 'printf \'{"type":"result"}\\n\''],
    )
    import os as _os

    monkeypatch.setattr(
        CCAdapter,
        "build_env",
        lambda self, cfg: {"PATH": _os.environ.get("PATH", "")},
    )

    adapter = CCAdapter()
    cfg = SpawnConfig(cwd=str(tmp_path), user_prompt="x", timeout_seconds=10.0)

    gen = adapter.run(cfg)
    try:
        frames = await _drain(gen, max_frames=20)
    finally:
        await gen.aclose()

    codes = [f.get("code") for f in frames if f.get("type") == "_adapter"]
    assert "timeout" not in codes
    assert any(f.get("type") == "result" for f in frames)


@pytest.mark.asyncio
async def test_timeout_uses_asyncio_timeout_primitive():
    """Sanity check: we rely on Python 3.11+ ``asyncio.timeout``."""
    assert hasattr(asyncio, "timeout"), "adapter timeout path requires Python 3.11+ asyncio.timeout"
