import asyncio
import shutil

import pytest

from app.cc_adapter.adapter import CCAdapter
from app.cc_adapter.types import SpawnConfig

pytestmark = pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")


async def _collect_until_result(gen, timeout_per_frame: float = 120.0) -> list[dict]:
    """Consume `gen` frame-by-frame under a per-frame timeout, stopping at `result`.

    Using `asyncio.wait_for(gen.__anext__(), ...)` instead of `async for` so a hung
    CC subprocess can't wedge the test suite indefinitely. We do NOT have
    pytest-timeout in the test venv, so this stdlib-only shape is the guard.
    """
    frames: list[dict] = []
    while True:
        try:
            frame = await asyncio.wait_for(gen.__anext__(), timeout=timeout_per_frame)
        except StopAsyncIteration:
            return frames
        frames.append(frame)
        if frame.get("type") == "result":
            return frames


@pytest.mark.asyncio
async def test_adapter_runs_real_cc_and_yields_session_id(tmp_path):
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    adapter = CCAdapter()
    cfg = SpawnConfig(
        cwd=str(cwd),
        user_prompt="say hi in one word",
        resume_session_id=None,
        mcp_config_path=None,
        model=None,
        add_dirs=[],
        permission_mode="bypassPermissions",
    )
    gen = adapter.run(cfg)
    try:
        events = await _collect_until_result(gen)
    finally:
        await gen.aclose()

    # first frame should be an _adapter spawning signal, then init, then content, then result
    assert any(e.get("type") == "_adapter" and e.get("subtype") == "spawning" for e in events)
    init_events = [e for e in events if e.get("type") == "system" and e.get("subtype") == "init"]
    assert len(init_events) == 1
    assert init_events[0].get("session_id")


@pytest.mark.asyncio
async def test_adapter_resume_continues_session(tmp_path):
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    adapter = CCAdapter()

    # turn 1
    cfg1 = SpawnConfig(cwd=str(cwd), user_prompt="remember the number 7", permission_mode="bypassPermissions")
    gen1 = adapter.run(cfg1)
    sid = None
    try:
        frames1 = await _collect_until_result(gen1)
    finally:
        await gen1.aclose()
    for frame in frames1:
        if frame.get("type") == "system" and frame.get("subtype") == "init":
            sid = frame.get("session_id")
            break
    assert sid

    # turn 2 with --resume
    cfg2 = SpawnConfig(cwd=str(cwd), user_prompt="what number did i tell you?", resume_session_id=sid, permission_mode="bypassPermissions")
    gen2 = adapter.run(cfg2)
    try:
        frames2 = await _collect_until_result(gen2)
    finally:
        await gen2.aclose()
    response_text = ""
    for frame in frames2:
        if frame.get("type") == "assistant":
            for block in frame.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    response_text += block.get("text", "")
    assert "7" in response_text
