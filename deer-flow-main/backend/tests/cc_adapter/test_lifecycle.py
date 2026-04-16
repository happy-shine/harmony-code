import asyncio

import pytest

from app.cc_adapter.lifecycle import CCProcess


@pytest.mark.asyncio
async def test_ccprocess_runs_cc_and_streams_lines(tmp_path):
    # Use `echo` first to isolate the streaming harness from CC itself,
    # then a separate test hits real `claude`.
    proc = CCProcess(
        cmd=["/bin/sh", "-c", 'printf \'{"type":"x"}\\n{"type":"y"}\\n\''],
        cwd=str(tmp_path),
        env={},
    )
    lines = []
    async for line in proc.stream():
        lines.append(line)
    exit_code = await proc.wait()
    assert exit_code == 0
    assert lines == [b'{"type":"x"}\n', b'{"type":"y"}\n']


@pytest.mark.asyncio
async def test_ccprocess_terminate_kills_long_running(tmp_path):
    proc = CCProcess(
        cmd=["/bin/sh", "-c", "sleep 60"],
        cwd=str(tmp_path),
        env={},
    )

    # start streaming, then immediately kill
    async def consume():
        async for _ in proc.stream():
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    await proc.terminate(grace_seconds=0.5)
    await task
    code = await proc.wait()
    assert code != 0  # killed


@pytest.mark.asyncio
async def test_ccprocess_captures_stderr_on_failure(tmp_path):
    proc = CCProcess(
        cmd=["/bin/sh", "-c", 'echo "boom" 1>&2; exit 2'],
        cwd=str(tmp_path),
        env={},
    )
    lines = []
    async for line in proc.stream():
        lines.append(line)
    code = await proc.wait()
    assert code == 2
    assert b"boom" in proc.stderr_tail()
