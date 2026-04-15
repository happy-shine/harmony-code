import asyncio
import json
import pytest
from pathlib import Path

from app.cc_adapter.adapter import CCAdapter
from app.cc_adapter.types import SpawnConfig


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
    events = []
    async for frame in adapter.run(cfg):
        events.append(frame)
        if frame.get("type") == "result":
            break

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
    sid = None
    async for frame in adapter.run(cfg1):
        if frame.get("type") == "system" and frame.get("subtype") == "init":
            sid = frame.get("session_id")
        if frame.get("type") == "result":
            break
    assert sid

    # turn 2 with --resume
    cfg2 = SpawnConfig(cwd=str(cwd), user_prompt="what number did i tell you?", resume_session_id=sid,
                      permission_mode="bypassPermissions")
    response_text = ""
    async for frame in adapter.run(cfg2):
        if frame.get("type") == "assistant":
            for block in frame.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    response_text += block.get("text", "")
        if frame.get("type") == "result":
            break
    assert "7" in response_text
