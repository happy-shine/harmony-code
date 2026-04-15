# harmony-code Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace deer-flow's LangGraph harness with Claude Code CLI as the agent runtime, keeping deer-flow's frontend + gateway as the UI and management surface, so that a small team can chat with an agent that uses user-installed skills/MCPs.

**Architecture:** Ephemeral CC subprocess per user message (`claude -p --resume <sid> --output-format stream-json`), stdout jsonl streamed 1:1 as SSE to frontend; frontend natively renders CC events (no translation layer); deer-flow DB is config SoT (composes MCP/skills at spawn time); CC session jsonl is conversation SoT.

**Tech Stack:** Python 3.11+ (FastAPI gateway), TypeScript 5.8 + Next.js 16 + React 19 (frontend), Claude Code CLI (runtime), better-auth (cookie session), sqlite/postgres for DB, asyncio subprocess for CC spawn.

**Design doc:** [docs/plans/2026-04-15-harmony-code-design.md](2026-04-15-harmony-code-design.md) — read this first for the full architectural context.

**Three unshakable invariants:**
1. CC is a black box. Never patch `claude-code-main/src/`. Always drive via official CLI flags + stdin/stdout jsonl.
2. CC's `~/.claude/projects/{cwd-hash}/{session_id}.jsonl` is conversation SoT. deer-flow DB only stores `thread_id → (session_id, cwd)`.
3. deer-flow DB is config SoT. Adapter composes CC config files per spawn.

**Anti-goals:**
- No domain logic from emergency_plan (or any user's app) leaks into this codebase
- Adapter never translates CC events; it only transports them
- Frontend never pretends CC concepts are deer-flow concepts

---

## Working Directory & Repo Setup

Project root: `/Users/shine/PycharmProjects/harmony-code/`

Contains three vendored source trees (research only, not part of build):
- `claude-code-main/` — CC CLI source (reference only)
- `deer-flow-main/` — base to fork
- `emergency_plan_swiftgen_v2-main/` — example downstream app (not imported)

**We will work IN `deer-flow-main/`**, refactoring it in place into harmony-code. The other two stay as reference and are gitignored from the active codebase.

---

## Milestone Layout

| M | What | Exit |
|---|------|------|
| M0 | Repo init + CC CLI verification spike | `git init` done; captured actual CC jsonl samples as fixtures |
| M1 | CC adapter skeleton end-to-end | curl POST returns SSE; second call with `--resume` continues |
| M2 | Frontend CC-native renderer | Real thread page uses new client; one full conversation renders "优雅" |
| M3 | Config flow (MCP/skills/models) | UI install MCP+skill → new thread CC uses them |
| M4 | Workspace browser + file flow | Upload→CC reads; CC writes→file tree auto-updates |
| M5 | Delete LangGraph + enable auth | `make dev` = 3 processes; 2-account isolation passes |
| M6 | Hardening + docs | Test coverage; new README; skill example tutorial |

Each milestone ends with a commit tag `m0-exit`, `m1-exit`, etc.

---

## M0: Repo init + CC CLI spike

### Task 0.1: Initialize git repo at harmony-code root

**Files:**
- Create: `/Users/shine/PycharmProjects/harmony-code/.gitignore`
- Create: `/Users/shine/PycharmProjects/harmony-code/README.md` (placeholder)

**Step 1: Decide repo scope**

The three vendored directories must NOT be tracked (they have their own upstream). Only track our design docs, root-level tooling, and (eventually) our refactored `deer-flow-main/` fork.

**Step 2: Write .gitignore**

Content:
```
# Vendored reference (research only, not part of build)
claude-code-main/
emergency_plan_swiftgen_v2-main/

# Python
__pycache__/
*.py[cod]
.venv/
venv/
.env
.env.*

# Node
node_modules/
.next/
pnpm-debug.log
.turbo/

# deer-flow specific
deer-flow-main/backend/.deer-flow/
deer-flow-main/backend/skills_store/
deer-flow-main/backend/uv.lock  # keep? decide in M5
deer-flow-main/frontend/.next/

# Editor
.vscode/
.idea/
*.swp

# OS
.DS_Store
```

**Step 3: Init repo + initial commit**

Run:
```bash
cd /Users/shine/PycharmProjects/harmony-code
git init
git add .gitignore docs/
git commit -m "chore: initial commit (design doc + gitignore)"
```

Expected: commit created, `claude-code-main/` and `emergency_plan_swiftgen_v2-main/` both shown as untracked-and-ignored.

**Step 4: Verify ignores**

Run: `git status --ignored | grep -E "(claude-code-main|emergency_plan)"`
Expected: both listed under "Ignored files".

**Step 5: Add deer-flow-main/ as tracked content**

```bash
git add deer-flow-main/
git commit -m "chore: vendor deer-flow base as starting point"
```

Expected: large commit with all deer-flow files staged. This is intentional — we fork in place.

---

### Task 0.2: CC CLI discovery — verify flags exist

**Why:** Our design assumes specific CLI flags. Before writing code that depends on them, verify against the installed `claude` binary.

**Files:** None (exploratory only, results captured in notes file).

**Step 1: Capture CC version**

Run: `claude --version`
Record output to `docs/plans/cc-cli-notes.md` under header `## Version`.

**Step 2: Capture CC help**

Run: `claude --help 2>&1 | tee /tmp/cc-help.txt`
Verify these flags exist in output (grep each):
```bash
grep -E -- "--print|-p" /tmp/cc-help.txt
grep -E -- "--resume" /tmp/cc-help.txt
grep -E -- "--output-format" /tmp/cc-help.txt
grep -E -- "--verbose" /tmp/cc-help.txt
grep -E -- "--mcp-config" /tmp/cc-help.txt
grep -E -- "--permission-mode" /tmp/cc-help.txt
grep -E -- "--model" /tmp/cc-help.txt
grep -E -- "--add-dir" /tmp/cc-help.txt
```

Expected: every flag found.

If any missing: flag spec changed — document in `cc-cli-notes.md` under `## Missing flags`, and adjust design Section 2 Spawn template before proceeding.

**Step 3: Document in notes**

Append to `docs/plans/cc-cli-notes.md`:
```markdown
## Confirmed flags (YYYY-MM-DD, version X.Y.Z)
- `-p` / `--print`
- `--resume <session_id>`
- `--output-format stream-json`
- `--verbose`
- `--mcp-config <path>`
- `--permission-mode <mode>` (values: default, acceptEdits, bypassPermissions, plan)
- `--model <name>`
- `--add-dir <path>` (allowlist additional dirs)
```

**Step 4: Commit**

```bash
git add docs/plans/cc-cli-notes.md
git commit -m "docs: capture CC CLI flag verification"
```

---

### Task 0.3: CC CLI discovery — capture real jsonl samples

**Why:** Section 4's TypeScript types are our guess at event shapes. We need actual samples as fixture data before writing a parser/renderer.

**Files:**
- Create: `docs/plans/cc-jsonl-samples/` directory
- Create: `docs/plans/cc-jsonl-samples/01-hello-text.jsonl`
- Create: `docs/plans/cc-jsonl-samples/02-tool-read.jsonl`
- Create: `docs/plans/cc-jsonl-samples/03-resume.jsonl`

**Step 1: Set up a throwaway cwd**

```bash
mkdir -p /tmp/cc-spike/workspace
cd /tmp/cc-spike/workspace
echo "hello" > hello.txt
```

**Step 2: Capture a simple text-only response**

Run:
```bash
claude -p --output-format stream-json --verbose \
  --permission-mode bypassPermissions \
  "say hi in one word" \
  > /tmp/cc-sample-01.jsonl 2>&1
```
(use `2>&1` to catch stderr too, strip later)

Copy first 200 lines to `docs/plans/cc-jsonl-samples/01-hello-text.jsonl`.

Expected structure (verify by reading):
- First line is `{"type":"system","subtype":"init",...}` with `session_id` field
- One or more `{"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}` lines
- Final line `{"type":"result","duration_ms":...,"total_cost_usd":...,"usage":...}`

If structure differs, update design Section 4's TypeScript types to match actual shapes.

**Step 3: Extract session_id for resume test**

```bash
head -1 /tmp/cc-sample-01.jsonl | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['session_id'])"
```
Save the value to env var: `export CC_SID=<value>`

**Step 4: Capture a tool_use flow (Read)**

```bash
cd /tmp/cc-spike/workspace
claude -p --output-format stream-json --verbose \
  --permission-mode bypassPermissions \
  "read hello.txt and tell me what's inside" \
  > /tmp/cc-sample-02.jsonl
```

Copy to `docs/plans/cc-jsonl-samples/02-tool-read.jsonl`. Verify:
- `assistant` event containing `{"type":"tool_use","name":"Read",...}` block
- `user` event containing `{"type":"tool_result","tool_use_id":"<id>","content":...}` block
- final `assistant` + `result` events

Record `tool_use_id` correlation: does it match between tool_use and tool_result? This is critical for M2's reducer.

**Step 5: Capture a resume**

```bash
claude -p --resume "$CC_SID" \
  --output-format stream-json --verbose \
  --permission-mode bypassPermissions \
  "what did i just ask?" \
  > /tmp/cc-sample-03.jsonl
```

Copy to `docs/plans/cc-jsonl-samples/03-resume.jsonl`. Verify:
- session_id in init event matches CC_SID (or is logically same session)
- previous context is referenced in the response

**Step 6: Document findings**

Append to `docs/plans/cc-cli-notes.md`:
```markdown
## Confirmed event types
- `system.init` with fields: session_id, model, cwd, tools[], mcp_servers[]
- `assistant` with message.content[] (blocks: text, thinking?, tool_use)
- `user` with message.content[] (blocks: tool_result)
- `result` with duration_ms, total_cost_usd, usage

## Session jsonl location
- `ls ~/.claude/projects/` to see how cwd is hashed into dir name
- Document the exact path pattern for a known cwd
```

Run:
```bash
ls -la ~/.claude/projects/ | head
# Find the dir that corresponds to /tmp/cc-spike/workspace
```
Note the exact naming scheme (is it `-tmp-cc-spike-workspace` with leading dash and dashes replacing slashes? verify).

**Step 7: Commit samples + notes**

```bash
git add docs/plans/cc-jsonl-samples/ docs/plans/cc-cli-notes.md
git commit -m "docs: capture CC jsonl samples as fixtures"
```

---

### Task 0.4: CC CLI discovery — verify MCP config + skill discovery

**Files:**
- Create: `docs/plans/cc-jsonl-samples/04-with-mcp.jsonl`
- Create: `docs/plans/cc-jsonl-samples/05-with-skill.jsonl`

**Step 1: Minimal MCP config**

Write `/tmp/mcp-test.json`:
```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/cc-spike/workspace"]
    }
  }
}
```

Run:
```bash
cd /tmp/cc-spike/workspace
claude -p --output-format stream-json --verbose \
  --permission-mode bypassPermissions \
  --mcp-config /tmp/mcp-test.json \
  "list available tools briefly" \
  > /tmp/cc-sample-04.jsonl
```

Copy to `docs/plans/cc-jsonl-samples/04-with-mcp.jsonl`. Verify first `system.init` event has `mcp_servers` array with entry for "filesystem".

**Step 2: Minimal skill**

Create `/tmp/cc-spike/workspace/.claude/skills/hello-skill/SKILL.md`:
```markdown
---
name: hello-skill
description: A test skill that greets the world
---

When asked to greet, always respond with "Hello from harmony-code skill!".
```

Run:
```bash
claude -p --output-format stream-json --verbose \
  --permission-mode bypassPermissions \
  "greet" \
  > /tmp/cc-sample-05.jsonl
```

Copy to `docs/plans/cc-jsonl-samples/05-with-skill.jsonl`. Verify output includes the exact phrase from the skill.

**Step 3: Verify skill directory discovery path**

Place the same SKILL.md at `/tmp/cc-spike/user-data/.claude/skills/hello-skill/SKILL.md` and run CC with cwd=`/tmp/cc-spike/user-data/workspace/`. Confirm CC finds skill at the parent `.claude/` (this is the layout our design uses).

Document result in `cc-cli-notes.md` under `## Skill discovery path`.

**Step 4: Commit**

```bash
git add docs/plans/cc-jsonl-samples/ docs/plans/cc-cli-notes.md
git commit -m "docs: verify MCP + skill discovery via CC CLI"
```

---

### Task 0.5: Tag M0 exit

Run:
```bash
git tag m0-exit
```

**M0 exit criteria:**
- [x] Git repo initialized with proper ignores
- [x] CC CLI version + flags documented
- [x] Real jsonl samples captured for 5 scenarios
- [x] Skill discovery path confirmed to work with our intended layout

---

## M1: CC Adapter skeleton end-to-end

**Goal of M1:** A developer can `curl POST /api/threads/{tid}/messages` and receive real CC jsonl via SSE, and a second request on the same thread resumes the session correctly. No frontend changes yet. No auth yet.

We add code alongside existing LangGraph code without deleting it (M5 deletes LangGraph).

### Task 1.1: Create cc_adapter package skeleton

**Files:**
- Create: `deer-flow-main/backend/app/cc_adapter/__init__.py`
- Create: `deer-flow-main/backend/app/cc_adapter/types.py`
- Create: `deer-flow-main/backend/app/cc_adapter/stream_parser.py`
- Create: `deer-flow-main/backend/app/cc_adapter/lifecycle.py`
- Create: `deer-flow-main/backend/app/cc_adapter/adapter.py`
- Create: `deer-flow-main/backend/tests/cc_adapter/__init__.py`

**Step 1: Stub `types.py`**

```python
"""CC event TypedDicts. Only fields we actually consume."""
from typing import Any, Literal, TypedDict


class CCSystemInit(TypedDict, total=False):
    type: Literal["system"]
    subtype: Literal["init"]
    session_id: str
    model: str
    cwd: str
    tools: list[str]
    mcp_servers: list[dict[str, Any]]


class CCResultEvent(TypedDict, total=False):
    type: Literal["result"]
    duration_ms: int
    total_cost_usd: float
    usage: dict[str, Any]


# Note: We deliberately do NOT type assistant/user events strictly.
# They get forwarded as raw dicts — only the adapter extracts session_id from init,
# everything else is transport.
```

**Step 2: Stub `stream_parser.py`**

```python
"""Parse CC stdout jsonl line-by-line, extract session_id, pass through everything else."""
import json
from typing import AsyncIterator


class StreamParser:
    """Stateful parser: reads lines, yields (event_dict, raw_line).
    Tracks the first observed session_id.
    """

    def __init__(self) -> None:
        self.session_id: str | None = None

    def feed_line(self, raw_line: bytes) -> tuple[dict | None, bytes]:
        """Parse one line; return (parsed_event or None if invalid, raw_line).
        Side effect: capture session_id on first system.init event.
        """
        line = raw_line.strip()
        if not line:
            return None, raw_line
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None, raw_line
        if (self.session_id is None
                and event.get("type") == "system"
                and event.get("subtype") == "init"):
            self.session_id = event.get("session_id")
        return event, raw_line
```

**Step 3: Run existing backend tests to establish baseline**

Run:
```bash
cd deer-flow-main/backend
make test
```

Expected: whatever currently passes/fails, record in `cc-cli-notes.md` under `## Pre-M1 test baseline`. We want to avoid regressing existing passing tests.

**Step 4: Commit skeleton**

```bash
git add deer-flow-main/backend/app/cc_adapter/
git add deer-flow-main/backend/tests/cc_adapter/
git commit -m "feat(cc_adapter): add empty package skeleton"
```

---

### Task 1.2: Write failing test for StreamParser

**Files:**
- Create: `deer-flow-main/backend/tests/cc_adapter/test_stream_parser.py`

**Step 1: Write the failing test**

```python
import json
from pathlib import Path

import pytest

from app.cc_adapter.stream_parser import StreamParser

SAMPLE_PATH = Path(__file__).resolve().parents[3] / "docs/plans/cc-jsonl-samples/01-hello-text.jsonl"


def test_parser_extracts_session_id_from_init():
    parser = StreamParser()
    first_line = SAMPLE_PATH.read_text().splitlines()[0].encode()
    event, raw = parser.feed_line(first_line)
    assert event is not None
    assert event["type"] == "system"
    assert event["subtype"] == "init"
    assert parser.session_id is not None
    assert raw == first_line


def test_parser_ignores_empty_lines():
    parser = StreamParser()
    event, raw = parser.feed_line(b"\n")
    assert event is None
    assert parser.session_id is None


def test_parser_ignores_malformed_json():
    parser = StreamParser()
    event, raw = parser.feed_line(b"not json\n")
    assert event is None
    assert parser.session_id is None


def test_parser_passes_through_non_init_events():
    parser = StreamParser()
    fake_event = json.dumps({"type": "assistant", "message": {"id": "x"}}).encode()
    event, raw = parser.feed_line(fake_event)
    assert event is not None
    assert event["type"] == "assistant"
    assert parser.session_id is None  # unchanged
```

Note on path: the plan writes samples under `/Users/shine/PycharmProjects/harmony-code/docs/plans/cc-jsonl-samples/`, but the test lives under `deer-flow-main/backend/tests/`. Decide on import strategy: either (a) symlink samples into backend, or (b) use relative path. The `parents[3]` above assumes `deer-flow-main/backend/tests/cc_adapter/test_stream_parser.py` → up 3 = `deer-flow-main/`, then up one more to repo root. Adjust `parents[N]` until the path resolves.

**Step 2: Run, expect PASS (implementation is already in place from Task 1.1)**

Run: `cd deer-flow-main/backend && pytest tests/cc_adapter/test_stream_parser.py -v`

Expected: 4 tests pass.

If FAIL: fix `stream_parser.py` until passing.

**Step 3: Commit**

```bash
git add deer-flow-main/backend/tests/cc_adapter/test_stream_parser.py
git commit -m "test(cc_adapter): StreamParser session_id extraction + passthrough"
```

---

### Task 1.3: Write lifecycle.py — spawn / wait / cancel

**Files:**
- Modify: `deer-flow-main/backend/app/cc_adapter/lifecycle.py`
- Create: `deer-flow-main/backend/tests/cc_adapter/test_lifecycle.py`

**Step 1: Write failing test first**

```python
# tests/cc_adapter/test_lifecycle.py
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
```

**Step 2: Run, expect FAIL (no impl)**

Run: `pytest tests/cc_adapter/test_lifecycle.py -v`
Expected: ImportError or NotImplementedError.

**Step 3: Implement `lifecycle.py`**

```python
"""Subprocess lifecycle: spawn, stream stdout lines, capture stderr, terminate."""
from __future__ import annotations

import asyncio
import signal
from collections import deque
from typing import AsyncIterator


class CCProcess:
    """Async wrapper over a CC subprocess. Stream stdout line-by-line, capture stderr."""

    STDERR_TAIL_LINES = 500

    def __init__(self, cmd: list[str], cwd: str, env: dict[str, str]) -> None:
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
        assert self._proc is not None
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
        except asyncio.TimeoutError:
            self._proc.kill()
            await self._proc.wait()

    def stderr_tail(self) -> bytes:
        return b"".join(self._stderr_buf)

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None
```

**Step 4: Run tests to verify PASS**

Run: `pytest tests/cc_adapter/test_lifecycle.py -v`
Expected: 3 tests pass.

**Step 5: Commit**

```bash
git add deer-flow-main/backend/app/cc_adapter/lifecycle.py deer-flow-main/backend/tests/cc_adapter/test_lifecycle.py
git commit -m "feat(cc_adapter): CCProcess spawn/stream/terminate with stderr tail"
```

---

### Task 1.4: Adapter core — minimal spawn + passthrough

**Files:**
- Modify: `deer-flow-main/backend/app/cc_adapter/adapter.py`
- Create: `deer-flow-main/backend/tests/cc_adapter/test_adapter.py`

**Step 1: Write failing test**

```python
# tests/cc_adapter/test_adapter.py
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
```

**Step 2: Run, expect FAIL (no impl)**

Run: `pytest tests/cc_adapter/test_adapter.py -v`

**Step 3: Implement adapter**

Add to `types.py`:
```python
from dataclasses import dataclass, field


@dataclass
class SpawnConfig:
    cwd: str
    user_prompt: str
    resume_session_id: str | None = None
    mcp_config_path: str | None = None
    model: str | None = None
    add_dirs: list[str] = field(default_factory=list)
    permission_mode: str = "bypassPermissions"
    extra_env: dict[str, str] = field(default_factory=dict)
```

Implement `adapter.py`:
```python
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
            code = await proc.wait()
            if code != 0:
                yield {
                    "type": "_adapter",
                    "subtype": "error",
                    "code": "cc_nonzero_exit",
                    "exit_code": code,
                    "stderr_tail": proc.stderr_tail().decode("utf-8", errors="replace")[-2000:],
                }
```

**Step 4: Run tests (real CC will be invoked — network needed)**

Run: `pytest tests/cc_adapter/test_adapter.py -v`

Expected: both pass. If resume test fails because CC does not honor the number recall, relax assertion to "response references the previous prompt" or similar.

**Step 5: Commit**

```bash
git add deer-flow-main/backend/app/cc_adapter/ deer-flow-main/backend/tests/cc_adapter/test_adapter.py
git commit -m "feat(cc_adapter): CCAdapter spawn + stream + resume"
```

---

### Task 1.5: Session store — DB schema + mapping

**Files:**
- Modify: existing threads-related models in `deer-flow-main/backend/app/` (find the existing thread model)
- Create: `deer-flow-main/backend/app/cc_adapter/session_store.py`
- Create: `deer-flow-main/backend/tests/cc_adapter/test_session_store.py`

**Step 1: Find existing thread model**

Run: `rg -l "class.*Thread" deer-flow-main/backend/app/ --type py | head`

If an existing SQLAlchemy/SQLModel thread model exists in gateway routers or a shared models module, we ADD columns there. If none exists (deer-flow stores threads in LangGraph's own checkpointer — very possible given current design), we introduce a minimal SQLite table just for our mapping.

Document finding in `cc-cli-notes.md` under `## Thread model location`.

**Step 2: Write failing test**

```python
# tests/cc_adapter/test_session_store.py
import pytest
from pathlib import Path
from app.cc_adapter.session_store import SessionStore


def test_create_and_get_mapping(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.ensure_schema()
    store.create("t_abc", cwd="/tmp/foo")
    row = store.get("t_abc")
    assert row is not None
    assert row.thread_id == "t_abc"
    assert row.cwd == "/tmp/foo"
    assert row.session_id is None


def test_set_session_id(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.ensure_schema()
    store.create("t_abc", cwd="/tmp/foo")
    store.set_session_id("t_abc", "01HW_SID")
    row = store.get("t_abc")
    assert row.session_id == "01HW_SID"


def test_delete_mapping(tmp_path):
    store = SessionStore(db_path=str(tmp_path / "sessions.db"))
    store.ensure_schema()
    store.create("t_abc", cwd="/tmp/foo")
    store.delete("t_abc")
    assert store.get("t_abc") is None
```

**Step 3: Run, expect FAIL**

Run: `pytest tests/cc_adapter/test_session_store.py -v`

**Step 4: Implement session_store.py**

```python
"""Minimal SQLite-backed store for thread_id → (session_id, cwd). M1 scope.
Later merged with deer-flow's proper thread table if one exists."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class Mapping:
    thread_id: str
    session_id: str | None
    cwd: str


class SessionStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def ensure_schema(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS cc_thread_session (
                    thread_id   TEXT PRIMARY KEY,
                    session_id  TEXT,
                    cwd         TEXT NOT NULL
                )
            """)

    def create(self, thread_id: str, cwd: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO cc_thread_session(thread_id, cwd) VALUES (?, ?)",
                (thread_id, cwd),
            )

    def get(self, thread_id: str) -> Mapping | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT thread_id, session_id, cwd FROM cc_thread_session WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
        return Mapping(*row) if row else None

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE cc_thread_session SET session_id=? WHERE thread_id=?",
                (session_id, thread_id),
            )

    def delete(self, thread_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM cc_thread_session WHERE thread_id=?", (thread_id,))
```

**Step 5: Run, verify PASS**

Run: `pytest tests/cc_adapter/test_session_store.py -v`

**Step 6: Commit**

```bash
git add deer-flow-main/backend/app/cc_adapter/session_store.py deer-flow-main/backend/tests/cc_adapter/test_session_store.py
git commit -m "feat(cc_adapter): SessionStore SQLite mapping for thread↔session"
```

---

### Task 1.6: SSE router — `POST /api/threads/{tid}/messages`

**Files:**
- Create: `deer-flow-main/backend/app/gateway/routers/messages.py`
- Modify: `deer-flow-main/backend/app/gateway/app.py` (mount new router)
- Create: `deer-flow-main/backend/tests/gateway/test_messages_router.py`

**Step 1: Write failing test**

```python
# tests/gateway/test_messages_router.py
import pytest
from fastapi.testclient import TestClient

from app.gateway.app import app


client = TestClient(app)


def test_create_thread_then_send_message_streams_sse(tmp_path, monkeypatch):
    # point adapter at a sqlite in tmp_path via env var or settings (define in implementation)
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))

    r = client.post("/api/threads", json={})
    assert r.status_code == 200
    tid = r.json()["id"]

    r2 = client.post(f"/api/threads/{tid}/messages",
                     json={"content": "say hi in one word"},
                     headers={"Accept": "text/event-stream"})
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("text/event-stream")
    body = r2.text
    # Expect at least one system.init frame and a final result frame
    assert '"type":"system"' in body
    assert '"subtype":"init"' in body
    assert '"type":"result"' in body or "event: done" in body


def test_busy_thread_returns_409(tmp_path, monkeypatch):
    # Skeleton: use a slow-spawn fixture; detail in implementation
    ...
```

**Step 2: Run, expect FAIL**

Run: `pytest tests/gateway/test_messages_router.py -v`

**Step 3: Implement router**

```python
# app/gateway/routers/messages.py
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.cc_adapter.adapter import CCAdapter
from app.cc_adapter.session_store import SessionStore
from app.cc_adapter.types import SpawnConfig


router = APIRouter(prefix="/api")


class SendMessageBody(BaseModel):
    content: str
    attachments: list[str] = []


def _data_dir() -> Path:
    return Path(os.environ.get("HARMONY_DATA_DIR", ".harmony-data"))


def _store() -> SessionStore:
    p = _data_dir() / "sessions.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    s = SessionStore(str(p))
    s.ensure_schema()
    return s


def _thread_cwd(thread_id: str) -> Path:
    return _data_dir() / "threads" / thread_id / "user-data" / "workspace"


_inflight: set[str] = set()
_inflight_lock = asyncio.Lock()


@router.post("/threads")
def create_thread() -> dict:
    from uuid import uuid4
    tid = f"t_{uuid4().hex[:12]}"
    cwd = _thread_cwd(tid)
    cwd.mkdir(parents=True, exist_ok=True)
    (cwd.parent / "uploads").mkdir(parents=True, exist_ok=True)
    (cwd.parent / "outputs").mkdir(parents=True, exist_ok=True)
    _store().create(tid, str(cwd))
    return {"id": tid, "cwd": str(cwd)}


@router.post("/threads/{tid}/messages")
async def send_message(tid: str, body: SendMessageBody, request: Request):
    store = _store()
    row = store.get(tid)
    if row is None:
        raise HTTPException(404, "thread not found")

    async with _inflight_lock:
        if tid in _inflight:
            raise HTTPException(409, "thread_busy")
        _inflight.add(tid)

    adapter = CCAdapter()
    cfg = SpawnConfig(
        cwd=row.cwd,
        user_prompt=body.content,
        resume_session_id=row.session_id,
        permission_mode="bypassPermissions",
    )

    async def event_gen() -> AsyncIterator[dict]:
        try:
            async for ev in adapter.run(cfg):
                if await request.is_disconnected():
                    break
                # capture session_id on first init
                if (ev.get("type") == "system"
                        and ev.get("subtype") == "init"
                        and row.session_id is None):
                    sid = ev.get("session_id")
                    if sid:
                        store.set_session_id(tid, sid)
                yield {"data": json.dumps(ev)}
            yield {"event": "done", "data": "{}"}
        finally:
            async with _inflight_lock:
                _inflight.discard(tid)

    return EventSourceResponse(event_gen())
```

Add to `app.py`:
```python
from app.gateway.routers import messages as messages_router
app.include_router(messages_router.router)
```

Add dependency:
```bash
cd deer-flow-main/backend
uv add sse-starlette
```

**Step 4: Run tests, verify PASS**

Run: `pytest tests/gateway/test_messages_router.py -v`

**Step 5: Manual end-to-end**

Run backend:
```bash
cd deer-flow-main/backend && make gateway
```

In another terminal:
```bash
# Create thread
TID=$(curl -sX POST http://localhost:8001/api/threads | jq -r .id)
echo "Thread: $TID"

# Send message, expect SSE
curl -N -X POST http://localhost:8001/api/threads/$TID/messages \
  -H "Content-Type: application/json" \
  -d '{"content":"say hi in one word"}'
```

Expected: SSE frames stream to terminal.

Second message same tid — should `--resume`:
```bash
curl -N -X POST http://localhost:8001/api/threads/$TID/messages \
  -H "Content-Type: application/json" \
  -d '{"content":"what did i just say?"}'
```

Expected: CC references first turn.

**Step 6: Commit**

```bash
git add deer-flow-main/backend/app/gateway/routers/messages.py \
        deer-flow-main/backend/app/gateway/app.py \
        deer-flow-main/backend/tests/gateway/test_messages_router.py \
        deer-flow-main/backend/pyproject.toml
git commit -m "feat(gateway): POST /api/threads/{tid}/messages with SSE streaming + resume"
```

---

### Task 1.7: Cancel endpoint + client-disconnect path

**Files:**
- Modify: `deer-flow-main/backend/app/gateway/routers/messages.py`
- Create: `deer-flow-main/backend/tests/gateway/test_cancel.py`

**Step 1: Write failing test**

```python
# tests/gateway/test_cancel.py
import asyncio
import httpx
import pytest


@pytest.mark.asyncio
async def test_client_disconnect_kills_cc(tmp_path, monkeypatch, gateway_server):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    base = gateway_server.url
    async with httpx.AsyncClient() as client:
        tid = (await client.post(f"{base}/api/threads")).json()["id"]
        async with client.stream("POST", f"{base}/api/threads/{tid}/messages",
                                 json={"content": "say a 500-word poem slowly"}) as r:
            # read a few bytes then abort
            it = r.aiter_bytes()
            _ = await it.__anext__()
            await r.aclose()
        # Now immediately send a new message; should not 409 (previous canceled)
        await asyncio.sleep(0.5)
        r2 = await client.post(f"{base}/api/threads/{tid}/messages",
                               json={"content": "hi"})
        assert r2.status_code == 200
```

The `gateway_server` fixture must start the gateway in a subprocess; add fixture in `conftest.py`.

**Step 2: Run, expect FAIL**

**Step 3: Wire the disconnect handling into `event_gen` (already partially in Task 1.6)**

The key is: the `async for ev in adapter.run(cfg)` loop must stop iterating on client disconnect AND must also tell the adapter to kill CC. Refactor adapter to accept a cancellation signal, or let event_gen close the async generator (which triggers `finally` in adapter.run → terminate proc).

Modify `adapter.py` to make `adapter.run` responsive to being closed:
```python
async def run(self, cfg: SpawnConfig) -> AsyncIterator[dict]:
    ...
    proc = CCProcess(cmd=cmd, cwd=cfg.cwd, env=env)
    ...
    try:
        async for raw in proc.stream():
            ...
            yield event
    except asyncio.CancelledError:
        await proc.terminate(grace_seconds=2.0)
        raise
    finally:
        if proc._proc and proc._proc.returncode is None:
            await proc.terminate(grace_seconds=2.0)
        ...
```

In `event_gen`, explicitly close the async generator on disconnect:
```python
gen = adapter.run(cfg)
try:
    async for ev in gen:
        if await request.is_disconnected():
            await gen.aclose()
            break
        ...
```

Also add explicit `POST /api/threads/{tid}/cancel`:
```python
@router.post("/threads/{tid}/cancel")
async def cancel_thread(tid: str):
    # Simplest: rely on client-disconnect path; explicit cancel requires tracking
    # the in-flight task. Add later if needed.
    async with _inflight_lock:
        if tid not in _inflight:
            return {"canceled": False, "reason": "no_inflight"}
    # TODO: signal the inflight task
    return {"canceled": True}
```

For MVP M1, client-disconnect path is enough. Explicit `/cancel` hooked up in M5 with task tracking.

**Step 4: Run tests, verify PASS**

**Step 5: Commit**

```bash
git add deer-flow-main/backend/app/cc_adapter/adapter.py \
        deer-flow-main/backend/app/gateway/routers/messages.py \
        deer-flow-main/backend/tests/gateway/test_cancel.py
git commit -m "feat(gateway): cancel via client-disconnect kills CC subprocess"
```

---

### Task 1.8: Debug page `/dev/cc` on frontend

**Files:**
- Create: `deer-flow-main/frontend/src/app/dev/cc/page.tsx`

**Step 1: Write the page**

```tsx
"use client";

import { useRef, useState } from "react";

export default function DevCCPage() {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [content, setContent] = useState("say hi in one word");
  const [log, setLog] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const createThread = async () => {
    const r = await fetch("/api/threads", { method: "POST" });
    const j = await r.json();
    setThreadId(j.id);
    setLog((l) => [...l, `[created thread ${j.id}]`]);
  };

  const send = async () => {
    if (!threadId) return;
    abortRef.current = new AbortController();
    const r = await fetch(`/api/threads/${threadId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
      signal: abortRef.current.signal,
    });
    const reader = r.body!.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      for (const frame of buf.split("\n\n").slice(0, -1)) {
        setLog((l) => [...l, frame]);
      }
      buf = buf.split("\n\n").slice(-1)[0];
    }
  };

  return (
    <div style={{ fontFamily: "monospace", padding: 16 }}>
      <button onClick={createThread}>New thread</button>
      <span style={{ marginLeft: 12 }}>thread: {threadId ?? "(none)"}</span>
      <div style={{ marginTop: 12 }}>
        <input value={content} onChange={(e) => setContent(e.target.value)} style={{ width: 600 }} />
        <button onClick={send} disabled={!threadId}>Send</button>
        <button onClick={() => abortRef.current?.abort()}>Stop</button>
      </div>
      <pre style={{ marginTop: 16, background: "#111", color: "#0f0", padding: 8, maxHeight: 500, overflow: "auto" }}>
        {log.join("\n\n")}
      </pre>
    </div>
  );
}
```

Assumes frontend is already proxying `/api/*` to gateway (check `next.config.js`; deer-flow has nginx for this but in dev you can add a rewrite rule).

If no proxy: add to `next.config.js`:
```js
async rewrites() {
  return [
    { source: "/api/:path*", destination: "http://localhost:8001/api/:path*" },
  ];
}
```

**Step 2: Manual verification**

Run:
```bash
cd deer-flow-main/frontend && pnpm dev
# visit http://localhost:3000/dev/cc
```

Click New thread → Send → observe raw SSE frames in the pre. Click Send again → verify session resumes (CC remembers context).

**Step 3: Commit**

```bash
git add deer-flow-main/frontend/src/app/dev/cc/page.tsx deer-flow-main/frontend/next.config.js
git commit -m "feat(frontend): /dev/cc debug page for SSE jsonl verification"
```

---

### Task 1.9: Tag M1 exit

Run:
```bash
git tag m1-exit
```

**M1 exit criteria:**
- [x] `curl POST /api/threads/{tid}/messages` returns SSE with real CC jsonl
- [x] Second request with same tid uses `--resume` and references prior context
- [x] Client disconnect kills CC subprocess (no zombies)
- [x] `/dev/cc` page shows raw frames end-to-end
- [x] All unit + integration tests pass

---

## M2: Frontend CC-native renderer

**Goal of M2:** Kill the `/dev/cc` raw log. Real thread page at `/workspace/chats/[thread_id]` uses the new CC SSE client and renders a visually polished conversation: text streamed, thinking collapsible, tool_use with specific renderers for Read/Write/Edit/Bash/Glob/Grep/WebFetch, Task nesting, TodoWrite sidebar, SystemInitBanner, ResultFooter.

### Task 2.1: Event parser + types

**Files:**
- Create: `deer-flow-main/frontend/src/core/cc-events/types.ts`
- Create: `deer-flow-main/frontend/src/core/cc-events/parse.ts`
- Create: `deer-flow-main/frontend/tests/unit/core/cc-events/parse.test.ts`

**Step 1: Write the failing test**

```ts
// tests/unit/core/cc-events/parse.test.ts
import { describe, expect, it } from "vitest";
import { parseSSEFrame } from "@/core/cc-events/parse";

describe("parseSSEFrame", () => {
  it("parses a data-only frame", () => {
    const frame = 'data: {"type":"assistant","message":{"id":"m1"}}';
    const r = parseSSEFrame(frame);
    expect(r.kind).toBe("data");
    if (r.kind === "data") expect(r.event.type).toBe("assistant");
  });

  it("parses a done event", () => {
    const frame = "event: done\ndata: {}";
    const r = parseSSEFrame(frame);
    expect(r.kind).toBe("done");
  });

  it("parses an error event", () => {
    const frame = 'event: error\ndata: {"code":"boom"}';
    const r = parseSSEFrame(frame);
    expect(r.kind).toBe("error");
    if (r.kind === "error") expect(r.payload.code).toBe("boom");
  });

  it("handles malformed json by returning invalid", () => {
    const frame = "data: not-json";
    const r = parseSSEFrame(frame);
    expect(r.kind).toBe("invalid");
  });
});
```

**Step 2: Run, expect FAIL**

Run: `pnpm --filter ./frontend test core/cc-events/parse` (or appropriate vitest command)

**Step 3: Implement types.ts**

Copy from design Section 4 (the `CCBlock`, `CCAssistantEvent`, etc.) verbatim. Do not add fields we don't consume.

**Step 4: Implement parse.ts**

```ts
import type { StreamEvent } from "./types";

type ParsedFrame =
  | { kind: "data"; event: StreamEvent }
  | { kind: "done" }
  | { kind: "error"; payload: any }
  | { kind: "invalid"; reason: string };

export function parseSSEFrame(frame: string): ParsedFrame {
  const lines = frame.split("\n");
  let eventName = "";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("event: ")) eventName = line.slice(7).trim();
    else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
  }
  const dataStr = dataLines.join("\n");
  if (eventName === "done") return { kind: "done" };
  if (eventName === "error") {
    try { return { kind: "error", payload: JSON.parse(dataStr) }; }
    catch { return { kind: "invalid", reason: "error payload not json" }; }
  }
  try {
    return { kind: "data", event: JSON.parse(dataStr) as StreamEvent };
  } catch {
    return { kind: "invalid", reason: "data not json" };
  }
}

export async function* drainSSE(
  body: ReadableStream<Uint8Array>
): AsyncGenerator<ParsedFrame> {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buf += dec.decode(value, { stream: true });
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";
    for (const f of frames) if (f.trim()) yield parseSSEFrame(f);
  }
}
```

**Step 5: Verify tests pass**

**Step 6: Commit**

```bash
git add deer-flow-main/frontend/src/core/cc-events/ deer-flow-main/frontend/tests/unit/core/cc-events/
git commit -m "feat(frontend): CC event types + SSE frame parser"
```

---

### Task 2.2: Message reducer

**Files:**
- Create: `deer-flow-main/frontend/src/core/messages/reducer.ts`
- Create: `deer-flow-main/frontend/tests/unit/core/messages/reducer.test.ts`

**Step 1: Write failing test (cover these cases)**

```ts
// tests/unit/core/messages/reducer.test.ts
import { describe, expect, it } from "vitest";
import { messageReducer, initialMessageState } from "@/core/messages/reducer";

describe("messageReducer", () => {
  it("appends a system_init on init event", () => {
    let s = initialMessageState();
    s = messageReducer(s, { type: "ingest", event: {
      type: "system", subtype: "init", session_id: "s1", model: "m1", cwd: "/t", tools: ["Read"], mcp_servers: []
    } as any });
    expect(s.messages[0].kind).toBe("system_init");
  });

  it("merges multiple assistant frames with same message.id into one UIMessage", () => {
    let s = initialMessageState();
    const mk = (text: string) => ({
      type: "assistant" as const,
      message: { id: "m1", role: "assistant" as const, content: [{ type: "text" as const, text }] }
    });
    s = messageReducer(s, { type: "ingest", event: mk("Hel") });
    s = messageReducer(s, { type: "ingest", event: mk("lo") });
    const am = s.messages.find(m => m.kind === "assistant");
    expect(am).toBeDefined();
    if (am?.kind === "assistant") {
      expect(am.blocks.length).toBe(1);
      if (am.blocks[0].kind === "text") expect(am.blocks[0].text).toBe("Hello");
    }
  });

  it("backfills tool_result onto matching tool_use block", () => {
    let s = initialMessageState();
    s = messageReducer(s, { type: "ingest", event: {
      type: "assistant",
      message: { id: "m1", content: [{ type: "tool_use", id: "tu1", name: "Read", input: { path: "x" } }] }
    } as any });
    s = messageReducer(s, { type: "ingest", event: {
      type: "user",
      message: { content: [{ type: "tool_result", tool_use_id: "tu1", content: "file content", is_error: false }] }
    } as any });
    const am = s.messages.find(m => m.kind === "assistant");
    if (am?.kind === "assistant") {
      const block = am.blocks[0];
      if (block.kind === "tool_use") {
        expect(block.status).toBe("ok");
        expect(block.result).toBeDefined();
      }
    }
  });

  it("diverts TodoWrite tool_use to todos slot, not assistant blocks", () => {
    let s = initialMessageState();
    s = messageReducer(s, { type: "ingest", event: {
      type: "assistant",
      message: { id: "m1", content: [{ type: "tool_use", id: "tu1", name: "TodoWrite",
                                       input: { todos: [{ content: "task A", status: "pending" }] } }] }
    } as any });
    expect(s.todos).toEqual([{ content: "task A", status: "pending" }]);
    const am = s.messages.find(m => m.kind === "assistant");
    if (am?.kind === "assistant") {
      expect(am.blocks.length).toBe(0);  // TodoWrite NOT in blocks
    }
  });

  it("captures result event as footer", () => {
    let s = initialMessageState();
    s = messageReducer(s, { type: "ingest", event: {
      type: "result", duration_ms: 1234, total_cost_usd: 0.01,
      usage: { input_tokens: 100, output_tokens: 50 }
    } as any });
    expect(s.result).toMatchObject({ duration_ms: 1234 });
  });
});
```

**Step 2: Run, expect FAIL**

**Step 3: Implement reducer.ts**

```ts
import type { StreamEvent, CCBlock } from "@/core/cc-events/types";

export type UIBlock =
  | { kind: "text"; text: string; streaming: boolean }
  | { kind: "thinking"; text: string; streaming: boolean; expanded: boolean }
  | {
      kind: "tool_use";
      id: string;
      name: string;
      input: unknown;
      status: "running" | "ok" | "error";
      result?: UIBlock[] | string | null;
    };

export type UIMessage =
  | { kind: "user"; id: string; text: string; attachments: string[] }
  | {
      kind: "assistant";
      id: string;
      blocks: UIBlock[];
      stopReason?: string;
      parentToolUseId?: string;
    }
  | {
      kind: "system_init";
      sessionId: string;
      model: string;
      cwd: string;
      tools: string[];
      mcpServers: Array<{ name: string; status: string }>;
    };

export type ResultState = {
  duration_ms: number;
  total_cost_usd?: number;
  usage?: any;
};

export type Todo = { content: string; status: string; activeForm?: string };

export type MessageState = {
  messages: UIMessage[];
  todos: Todo[];
  result: ResultState | null;
};

export function initialMessageState(): MessageState {
  return { messages: [], todos: [], result: null };
}

export type Action =
  | { type: "ingest"; event: StreamEvent }
  | { type: "reset" };

export function messageReducer(state: MessageState, action: Action): MessageState {
  if (action.type === "reset") return initialMessageState();
  const ev = action.event;

  if (ev.type === "system" && (ev as any).subtype === "init") {
    const init = ev as any;
    return {
      ...state,
      messages: [...state.messages, {
        kind: "system_init",
        sessionId: init.session_id,
        model: init.model,
        cwd: init.cwd,
        tools: init.tools ?? [],
        mcpServers: init.mcp_servers ?? [],
      }],
    };
  }

  if (ev.type === "result") {
    return { ...state, result: ev as any };
  }

  if (ev.type === "assistant") {
    const aev = ev as any;
    const mid = aev.message.id;
    const blocks = aev.message.content as CCBlock[];
    let todos = state.todos;
    const nonTodoBlocks: UIBlock[] = [];
    for (const b of blocks) {
      if (b.type === "tool_use" && (b as any).name === "TodoWrite") {
        const input = (b as any).input ?? {};
        if (Array.isArray(input.todos)) todos = input.todos;
        continue; // diverted, do not add to blocks
      }
      nonTodoBlocks.push(blockToUIBlock(b));
    }

    const existing = state.messages.find(m => m.kind === "assistant" && m.id === mid);
    if (existing && existing.kind === "assistant") {
      const merged = mergeBlocks(existing.blocks, nonTodoBlocks);
      return {
        ...state,
        todos,
        messages: state.messages.map(m =>
          m === existing ? { ...existing, blocks: merged, stopReason: aev.message.stop_reason ?? existing.stopReason } : m
        ),
      };
    }
    return {
      ...state,
      todos,
      messages: [...state.messages, {
        kind: "assistant",
        id: mid,
        blocks: nonTodoBlocks,
        stopReason: aev.message.stop_reason,
        parentToolUseId: aev.parent_tool_use_id,
      }],
    };
  }

  if (ev.type === "user") {
    // Only handle tool_result blocks here (regular user text comes from local send, not stream)
    const uev = ev as any;
    const updates = state.messages.map(m => {
      if (m.kind !== "assistant") return m;
      const blocks = m.blocks.map(b => {
        if (b.kind !== "tool_use") return b;
        const hit = uev.message.content.find(
          (c: any) => c.type === "tool_result" && c.tool_use_id === b.id
        );
        if (!hit) return b;
        return {
          ...b,
          status: hit.is_error ? "error" as const : "ok" as const,
          result: typeof hit.content === "string" ? hit.content : hit.content,
        };
      });
      return { ...m, blocks };
    });
    return { ...state, messages: updates };
  }

  // _adapter and anything else: ignore in reducer for now (status is handled by hook)
  return state;
}

function blockToUIBlock(b: CCBlock): UIBlock {
  if (b.type === "text") return { kind: "text", text: (b as any).text, streaming: false };
  if (b.type === "thinking") return { kind: "thinking", text: (b as any).thinking, streaming: false, expanded: false };
  if (b.type === "tool_use") return {
    kind: "tool_use",
    id: (b as any).id,
    name: (b as any).name,
    input: (b as any).input,
    status: "running",
  };
  // tool_result blocks inside assistant message are unusual; ignore defensively
  return { kind: "text", text: "", streaming: false };
}

function mergeBlocks(prev: UIBlock[], next: UIBlock[]): UIBlock[] {
  // For text/thinking: concat if same kind at same index; for tool_use: keep last
  const out: UIBlock[] = [...prev];
  for (const nb of next) {
    if (nb.kind === "tool_use") {
      const idx = out.findIndex(x => x.kind === "tool_use" && x.id === nb.id);
      if (idx >= 0) out[idx] = { ...nb, ...(out[idx] as any) };
      else out.push(nb);
    } else {
      // text/thinking: append new block (CC usually sends deltas, but if it sends whole blocks,
      // we trust CC). Refine in real testing.
      out.push(nb);
    }
  }
  return out;
}
```

**Step 4: Run, verify PASS** (iterate until passes)

**Step 5: Commit**

```bash
git add deer-flow-main/frontend/src/core/messages/reducer.ts \
        deer-flow-main/frontend/tests/unit/core/messages/reducer.test.ts
git commit -m "feat(frontend): message reducer with TodoWrite divert + tool_result backfill"
```

---

### Task 2.3: Thread stream hook

**Files:**
- Create: `deer-flow-main/frontend/src/core/threads/cc-stream.ts`
- Modify: `deer-flow-main/frontend/src/core/threads/hooks.ts` (rewrite `useThreadStream`)

**Step 1: Implement `cc-stream.ts`**

```ts
import { drainSSE } from "@/core/cc-events/parse";
import type { StreamEvent } from "@/core/cc-events/types";

export async function* openMessageStream(
  threadId: string,
  payload: { content: string; attachments?: string[] },
  signal: AbortSignal
): AsyncGenerator<StreamEvent | { type: "_error"; payload: any } | { type: "_done" }> {
  const resp = await fetch(`/api/threads/${threadId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  if (!resp.body) throw new Error("no body");

  for await (const frame of drainSSE(resp.body)) {
    if (frame.kind === "data") yield frame.event;
    else if (frame.kind === "done") yield { type: "_done" };
    else if (frame.kind === "error") yield { type: "_error", payload: frame.payload };
    // 'invalid' frames: log-and-skip
  }
}
```

**Step 2: Rewrite `useThreadStream`**

```ts
// core/threads/hooks.ts
import { useCallback, useReducer, useRef, useState } from "react";
import { openMessageStream } from "./cc-stream";
import { initialMessageState, messageReducer } from "@/core/messages/reducer";

export function useThreadStream(threadId: string) {
  const [state, dispatch] = useReducer(messageReducer, undefined, initialMessageState);
  const [status, setStatus] = useState<"idle" | "running" | "error">("idle");
  const [error, setError] = useState<any>(null);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (content: string, attachments: string[] = []) => {
      setStatus("running");
      setError(null);
      abortRef.current = new AbortController();
      try {
        for await (const ev of openMessageStream(threadId, { content, attachments }, abortRef.current.signal)) {
          if (ev.type === "_done") break;
          if (ev.type === "_error") { setError(ev.payload); setStatus("error"); return; }
          dispatch({ type: "ingest", event: ev as any });
        }
        setStatus("idle");
      } catch (e: any) {
        if (e.name === "AbortError") setStatus("idle");
        else { setError(e); setStatus("error"); }
      }
    },
    [threadId]
  );

  const cancel = useCallback(() => abortRef.current?.abort(), []);

  return { ...state, status, error, send, cancel };
}
```

**Step 3: Unit test with mocked fetch**

Add test in `tests/unit/core/threads/hooks.test.ts` using `@testing-library/react` + mock fetch returning a stream. (Detail out when implementing; pattern: create a `ReadableStream` that yields a hand-crafted SSE body with 2 frames.)

**Step 4: Commit**

```bash
git add deer-flow-main/frontend/src/core/threads/ \
        deer-flow-main/frontend/tests/unit/core/threads/
git commit -m "feat(frontend): rewrite useThreadStream to consume CC SSE"
```

---

### Task 2.4: Block renderers — text, thinking, tool_use, tool_result, system_init, result

**Files:** (one task per component, keep bite-sized — grouping here for brevity)
- Create: `deer-flow-main/frontend/src/components/workspace/cc-blocks/TextBlock.tsx`
- Create: `.../cc-blocks/ThinkingBlock.tsx`
- Create: `.../cc-blocks/ToolUseBlock.tsx`
- Create: `.../cc-blocks/ToolResultBlock.tsx`
- Create: `.../cc-blocks/SystemInitBanner.tsx`
- Create: `.../cc-blocks/ResultFooter.tsx`

For each component: write a Storybook-style test page (or a Vitest snapshot) with a fixture from `cc-jsonl-samples/`, render, visually check, commit.

**TextBlock.tsx:**
```tsx
import { Markdown } from "@/components/ai-elements/markdown";

export function TextBlock({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <div className="cc-text-block">
      <Markdown>{text}</Markdown>
      {streaming && <span className="cursor-blink">▌</span>}
    </div>
  );
}
```

**ThinkingBlock.tsx:**
```tsx
import { useState } from "react";

export function ThinkingBlock({ text, streaming }: { text: string; streaming: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <details open={open} onToggle={(e) => setOpen((e.target as any).open)}
             className="cc-thinking-block rounded border border-neutral-700/30 bg-neutral-100 dark:bg-neutral-900 p-2 my-2">
      <summary className="cursor-pointer text-xs text-neutral-500">
        💭 thinking {streaming && "…"}
      </summary>
      <pre className="whitespace-pre-wrap text-xs text-neutral-600 dark:text-neutral-400">{text}</pre>
    </details>
  );
}
```

**ToolUseBlock.tsx:**
```tsx
import type { UIBlock } from "@/core/messages/reducer";
import { ReadRenderer, WriteRenderer, EditRenderer, BashRenderer, GlobGrepRenderer,
         WebFetchRenderer, DefaultMcpRenderer } from "./tool-renderers";
import { NestedMessages } from "./NestedMessages";

const rendererMap: Record<string, React.FC<any>> = {
  Read: ReadRenderer,
  Write: WriteRenderer,
  Edit: EditRenderer,
  Bash: BashRenderer,
  Glob: GlobGrepRenderer,
  Grep: GlobGrepRenderer,
  WebFetch: WebFetchRenderer,
  WebSearch: WebFetchRenderer,
};

export function ToolUseBlock({ block }: { block: Extract<UIBlock, { kind: "tool_use" }> }) {
  const Renderer = rendererMap[block.name] ?? DefaultMcpRenderer;
  return (
    <div className="cc-tool-use rounded border my-2 p-2 bg-neutral-50 dark:bg-neutral-900">
      <header className="flex items-center gap-2 text-xs">
        <ToolIcon name={block.name} />
        <span className="font-mono">{block.name}</span>
        <StatusDot status={block.status} />
      </header>
      <Renderer input={block.input} result={block.result} status={block.status} />
      {block.name === "Task" && <NestedMessages parentToolUseId={block.id} />}
    </div>
  );
}

function ToolIcon({ name }: { name: string }) {
  // Map to lucide or text
  return <span>🔧</span>;
}

function StatusDot({ status }: { status: string }) {
  const color = status === "ok" ? "bg-green-500" : status === "error" ? "bg-red-500" : "bg-amber-500 animate-pulse";
  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} />;
}
```

**ToolResultBlock.tsx:** (unused in current reducer shape since results live on tool_use; keep a stub for shape consistency or remove)

**SystemInitBanner.tsx:**
```tsx
export function SystemInitBanner({ sessionId, model, cwd, tools, mcpServers }: {
  sessionId: string; model: string; cwd: string; tools: string[];
  mcpServers: Array<{ name: string; status: string }>;
}) {
  return (
    <div className="cc-init-banner text-xs text-neutral-500 border-b py-1 px-2 my-2 flex gap-3 flex-wrap">
      <span>session <code>{sessionId.slice(0, 8)}</code></span>
      <span>model <code>{model}</code></span>
      <span>cwd <code title={cwd}>{cwd.split("/").slice(-2).join("/")}</code></span>
      <span>{tools.length} tools</span>
      {mcpServers.map((m) => (
        <span key={m.name}>
          mcp:{m.name} {m.status === "connected" ? "✓" : "✗"}
        </span>
      ))}
    </div>
  );
}
```

**ResultFooter.tsx:**
```tsx
export function ResultFooter({ duration_ms, total_cost_usd, usage }:
  { duration_ms: number; total_cost_usd?: number; usage?: any }) {
  return (
    <div className="cc-result-footer text-xs text-neutral-500 border-t py-1 px-2 my-2 flex gap-3">
      <span>{(duration_ms / 1000).toFixed(1)}s</span>
      {total_cost_usd != null && <span>${total_cost_usd.toFixed(4)}</span>}
      {usage && <span>{usage.input_tokens}→{usage.output_tokens} tok</span>}
    </div>
  );
}
```

Commit per component OR one "block renderers" commit; prefer one per component.

---

### Task 2.5: Specific tool renderers

**Files:**
- Create: `deer-flow-main/frontend/src/components/workspace/cc-blocks/tool-renderers/index.ts`
- Create: `.../tool-renderers/ReadRenderer.tsx`
- Create: `.../tool-renderers/WriteRenderer.tsx`
- Create: `.../tool-renderers/EditRenderer.tsx`
- Create: `.../tool-renderers/BashRenderer.tsx`
- Create: `.../tool-renderers/GlobGrepRenderer.tsx`
- Create: `.../tool-renderers/WebFetchRenderer.tsx`
- Create: `.../tool-renderers/DefaultMcpRenderer.tsx`

For each renderer: accept `{ input, result, status }` props; render input preview (file path / cmd / url) + result if present.

**Example BashRenderer.tsx:**
```tsx
export function BashRenderer({ input, result, status }: { input: any; result: any; status: string }) {
  return (
    <div className="font-mono text-xs mt-1">
      <div className="bg-neutral-800 text-neutral-100 p-2 rounded">
        $ {input?.command ?? ""}
      </div>
      {result != null && (
        <pre className="bg-neutral-900 text-neutral-100 p-2 rounded mt-1 whitespace-pre-wrap max-h-96 overflow-auto">
          {typeof result === "string" ? result : JSON.stringify(result)}
        </pre>
      )}
    </div>
  );
}
```

(Implement each similarly; keep minimal, iterate on polish later.)

**Commit per renderer.**

---

### Task 2.6: Replace `/workspace/chats/[thread_id]/page.tsx` with new stack

**Files:**
- Modify: `deer-flow-main/frontend/src/app/workspace/chats/[thread_id]/page.tsx`
- Modify: `deer-flow-main/frontend/src/components/workspace/chats/` (main chat container)

**Step 1: Wire new hook + block renderers**

Compose:
```tsx
// simplified
const { messages, todos, result, status, send, cancel } = useThreadStream(threadId);

return (
  <div className="thread-root">
    <div className="thread-main">
      {messages.map(m => {
        if (m.kind === "system_init") return <SystemInitBanner key={m.sessionId} {...m} />;
        if (m.kind === "user") return <UserBubble key={m.id} text={m.text} />;
        if (m.kind === "assistant") return (
          <AssistantMessage key={m.id}>
            {m.blocks.map((b, i) => {
              if (b.kind === "text") return <TextBlock key={i} {...b} />;
              if (b.kind === "thinking") return <ThinkingBlock key={i} {...b} />;
              if (b.kind === "tool_use") return <ToolUseBlock key={b.id} block={b} />;
            })}
          </AssistantMessage>
        );
      })}
      {result && <ResultFooter {...result} />}
      <Composer onSubmit={send} onStop={cancel} status={status} />
    </div>
    <aside>
      <TodosPanel todos={todos} />
      <FileBrowser threadId={threadId} />  {/* stub in M2, real in M4 */}
    </aside>
  </div>
);
```

**Step 2: Visual verification**

Run both dev servers; open `/workspace/chats/new-thread-id` (create thread first via /dev/cc); send real prompts; verify:
- Text streams (looks good)
- Thinking blocks fold
- Tool_use blocks render with correct specific renderer
- Result footer appears

**Step 3: Commit**

```bash
git add deer-flow-main/frontend/src/app/workspace/chats/ deer-flow-main/frontend/src/components/workspace/chats/
git commit -m "feat(frontend): thread page uses new CC-native renderer stack"
```

---

### Task 2.7: Tag M2 exit

```bash
git tag m2-exit
```

**M2 exit criteria:**
- [x] `/workspace/chats/[tid]` fully uses new stack (no LangGraph SDK imported)
- [x] One full conversation with 3+ tool calls visually polished
- [x] TodoWrite goes to sidebar, not messages
- [x] Thinking blocks collapsible
- [x] Result footer shows duration + cost

---

## M3: Config flow (MCP / Skills / Models)

### Task 3.1: DB schema — mcp_servers, skills, user_prefs

**Files:**
- Create: `deer-flow-main/backend/alembic.ini` (if not exists)
- Create: `deer-flow-main/backend/alembic/versions/001_harmony_schema.py`
- Modify: `deer-flow-main/backend/pyproject.toml` — add `alembic`

**Step 1: init alembic**

```bash
cd deer-flow-main/backend
uv add alembic
alembic init alembic
# edit alembic.ini: sqlalchemy.url = sqlite:///${HARMONY_DATA_DIR}/harmony.db
```

**Step 2: Write migration**

```python
# alembic/versions/001_harmony_schema.py
"""harmony schema"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None

def upgrade():
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, nullable=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("transport", sa.String, nullable=False),
        sa.Column("command", sa.String, nullable=True),
        sa.Column("args_json", sa.String, nullable=True),
        sa.Column("url", sa.String, nullable=True),
        sa.Column("headers_json", sa.String, nullable=True),
        sa.Column("env_json", sa.String, nullable=True),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_mcp_user_name", "mcp_servers", ["user_id", "name"], unique=True)

    op.create_table(
        "skills",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, nullable=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_skills_user_name", "skills", ["user_id", "name"], unique=True)

    op.create_table(
        "user_prefs",
        sa.Column("user_id", sa.String, primary_key=True),
        sa.Column("default_model", sa.String, nullable=True),
        sa.Column("extras_json", sa.String, nullable=True),
    )

    op.create_table(
        "cc_thread_session",
        sa.Column("thread_id", sa.String, primary_key=True),
        sa.Column("user_id", sa.String, nullable=True),
        sa.Column("session_id", sa.String, nullable=True),
        sa.Column("cwd", sa.String, nullable=False),
    )

def downgrade():
    op.drop_table("cc_thread_session")
    op.drop_table("user_prefs")
    op.drop_index("ix_skills_user_name")
    op.drop_table("skills")
    op.drop_index("ix_mcp_user_name")
    op.drop_table("mcp_servers")
```

**Step 3: Run migration**

```bash
HARMONY_DATA_DIR=.harmony-data alembic upgrade head
```

Verify tables created: `sqlite3 .harmony-data/harmony.db ".tables"`

**Step 4: Commit**

```bash
git add deer-flow-main/backend/alembic/ deer-flow-main/backend/alembic.ini deer-flow-main/backend/pyproject.toml
git commit -m "feat(db): harmony schema migration 001"
```

---

### Task 3.2: compose.py — MCP config + skills dir

**Files:**
- Modify: `deer-flow-main/backend/app/cc_adapter/compose.py`
- Create: `deer-flow-main/backend/tests/cc_adapter/test_compose.py`

**Step 1: Write failing test**

```python
# tests/cc_adapter/test_compose.py
import json
from pathlib import Path

import pytest

from app.cc_adapter.compose import compose_mcp_config, compose_skills_dir


def test_compose_mcp_config_combines_user_and_global(tmp_path, db_with_rows):
    db_with_rows.insert_mcp(user_id="u1", name="personal", transport="stdio",
                            command="echo", args=["a"], env={"X": "1"})
    db_with_rows.insert_mcp(user_id=None, name="team_fs", transport="stdio",
                            command="npx", args=["-y", "fs"])
    out = compose_mcp_config(db=db_with_rows, user_id="u1", thread_id="t_abc",
                             tmp_root=tmp_path)
    data = json.loads(Path(out).read_text())
    assert set(data["mcpServers"].keys()) == {"personal", "team_fs"}
    assert data["mcpServers"]["personal"]["env"] == {"X": "1"}


def test_compose_skills_dir_symlinks(tmp_path, db_with_rows):
    (tmp_path / "skill1" / "SKILL.md").parent.mkdir(parents=True)
    (tmp_path / "skill1" / "SKILL.md").write_text("---\nname: skill1\n---")
    db_with_rows.insert_skill(user_id="u1", name="skill1", path=str(tmp_path / "skill1"))
    target = tmp_path / "threads/t1/user-data/.claude/skills"
    compose_skills_dir(db=db_with_rows, user_id="u1", skills_dir=target)
    assert (target / "skill1").is_symlink()
    assert (target / "skill1" / "SKILL.md").exists()
```

Fixture `db_with_rows` in `conftest.py` wires sqlalchemy to a tmp sqlite.

**Step 2: Run, expect FAIL**

**Step 3: Implement compose.py**

```python
"""Compose per-spawn CC config from deer-flow DB."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def compose_mcp_config(*, db, user_id: str, thread_id: str, tmp_root: Path) -> Path:
    rows = db.query_mcp_for_user(user_id=user_id, enabled_only=True)
    servers: dict = {}
    for r in rows:
        entry: dict = {}
        if r.transport == "stdio":
            entry["command"] = r.command
            if r.args_json: entry["args"] = json.loads(r.args_json)
        else:
            entry["url"] = r.url
            if r.headers_json: entry["headers"] = json.loads(r.headers_json)
        if r.env_json: entry["env"] = json.loads(r.env_json)
        servers[r.name] = entry
    out_path = tmp_root / f"mcp-{thread_id}-{os.getpid()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"mcpServers": servers}))
    return out_path


def compose_skills_dir(*, db, user_id: str, skills_dir: Path) -> None:
    if skills_dir.exists():
        shutil.rmtree(skills_dir)
    skills_dir.mkdir(parents=True)
    rows = db.query_skills_for_user(user_id=user_id, enabled_only=True)
    for r in rows:
        (skills_dir / r.name).symlink_to(r.path)
```

**Step 4: Run, verify PASS**

**Step 5: Commit**

```bash
git add deer-flow-main/backend/app/cc_adapter/compose.py \
        deer-flow-main/backend/tests/cc_adapter/test_compose.py
git commit -m "feat(cc_adapter): compose MCP config + skills symlinks per spawn"
```

---

### Task 3.3: Wire compose.py into the messages router

**Files:**
- Modify: `deer-flow-main/backend/app/gateway/routers/messages.py`

**Step 1: Extend the spawn flow**

In the `send_message` handler, before building `SpawnConfig`, call `compose_mcp_config` and `compose_skills_dir`, and pass the paths into `SpawnConfig`.

```python
# In send_message, before adapter.run:
data_dir = _data_dir()
tmp_root = data_dir / "tmp"
tmp_root.mkdir(parents=True, exist_ok=True)
mcp_path = compose_mcp_config(db=_db(), user_id=user_id, thread_id=tid, tmp_root=tmp_root)
thread_root = data_dir / "threads" / tid / "user-data"
compose_skills_dir(db=_db(), user_id=user_id, skills_dir=thread_root / ".claude" / "skills")

cfg = SpawnConfig(
    cwd=str(thread_root / "workspace"),
    user_prompt=body.content,
    resume_session_id=row.session_id,
    mcp_config_path=str(mcp_path),
    add_dirs=[str(thread_root / "uploads")],
    permission_mode="bypassPermissions",
)
```

`user_id` comes from auth dep (stub to "u_default" for now; M5 wires real auth).

**Step 2: Integration test**

```python
def test_end_to_end_new_mcp_appears_in_init(tmp_path, monkeypatch, gateway_server):
    monkeypatch.setenv("HARMONY_DATA_DIR", str(tmp_path))
    # POST /api/mcp to add a server
    # POST /api/threads
    # POST /api/threads/{tid}/messages
    # Parse SSE, find system.init, assert mcp_servers includes our server
    ...
```

**Step 3: Commit**

```bash
git add deer-flow-main/backend/app/gateway/routers/messages.py
git commit -m "feat(gateway): compose MCP + skills on each spawn"
```

---

### Task 3.4: Rewrite `/api/mcp` router

**Files:**
- Modify: `deer-flow-main/backend/app/gateway/routers/mcp.py`
- Modify: tests

**Step 1: Write failing tests**

Cover: GET list (user + global merge), POST create, PATCH enable/disable, DELETE.

**Step 2: Delete existing LangGraph-coupled implementation body; replace with DB CRUD**

```python
# routers/mcp.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/mcp")


class MCPServerIn(BaseModel):
    name: str
    transport: str  # stdio | sse | http
    command: str | None = None
    args: list[str] = []
    url: str | None = None
    headers: dict[str, str] = {}
    env: dict[str, str] = {}
    enabled: bool = True


class MCPServerOut(MCPServerIn):
    id: str
    user_id: str | None


@router.get("", response_model=list[MCPServerOut])
def list_mcp(user_id: str = Depends(current_user_id)):
    return _db().list_mcp_for_user(user_id=user_id)


@router.post("", response_model=MCPServerOut)
def create_mcp(body: MCPServerIn, user_id: str = Depends(current_user_id)):
    return _db().insert_mcp(user_id=user_id, **body.dict())


@router.delete("/{mcp_id}")
def delete_mcp(mcp_id: str, user_id: str = Depends(current_user_id)):
    row = _db().get_mcp(mcp_id)
    if not row: raise HTTPException(404)
    if row.user_id and row.user_id != user_id: raise HTTPException(403)
    _db().delete_mcp(mcp_id)
    return {"ok": True}


@router.patch("/{mcp_id}")
def update_mcp(mcp_id: str, patch: dict, user_id: str = Depends(current_user_id)):
    row = _db().get_mcp(mcp_id)
    if not row: raise HTTPException(404)
    if row.user_id and row.user_id != user_id: raise HTTPException(403)
    _db().update_mcp(mcp_id, patch)
    return {"ok": True}
```

**Step 3: Verify tests pass**

**Step 4: Frontend side — align `src/core/mcp/` clients to new API shapes**

**Step 5: Commit**

---

### Task 3.5: Rewrite `/api/skills` router

Pattern same as 3.4. Support:
- POST multipart/form-data with zip upload → extract to `skills_store/{id}/`
- POST with `{ source: "git", url: "..." }` → git clone

**Files:**
- Modify: `deer-flow-main/backend/app/gateway/routers/skills.py`
- Create: `deer-flow-main/backend/app/skills/installer.py` (upload + git clone helpers)

**Commit per feature** (upload path, then git path).

---

### Task 3.6: `/api/models` router

Minimal: static list of CC-supported models (configured in `config.yaml`) + user preference CRUD via `user_prefs` table.

---

### Task 3.7: Tag M3 exit

```bash
git tag m3-exit
```

**M3 exit criteria:**
- [x] UI add MCP server → next thread's init event lists it
- [x] UI upload skill (zip) → CC uses it in next thread
- [x] UI change model → next spawn uses `--model` with new value
- [x] `extensions_config.json` one-time migrated into DB (run a migration script)

---

## M4: Workspace browser + file flow

### Task 4.1: Workspace router (file tree + download)

**Files:**
- Create: `deer-flow-main/backend/app/gateway/routers/workspace.py`
- Create: tests

**Step 1: Write failing test (path escape blocked)**

```python
def test_workspace_path_escape_blocked(gateway_server, tmp_path, monkeypatch):
    ...
    # Create a file outside workspace
    (tmp_path / "secret.txt").write_text("pwned")
    # Try to access via crafted path
    r = client.get(f"/api/threads/{tid}/workspace/files/..%2F..%2F..%2Fsecret.txt")
    assert r.status_code in (400, 403, 404)  # NOT 200
```

**Step 2: Implement with strict resolution**

```python
def _safe_resolve(cwd: Path, rel: str) -> Path:
    full = (cwd / rel).resolve()
    if not str(full).startswith(str(cwd.resolve())):
        raise HTTPException(400, "path_out_of_scope")
    return full
```

**Step 3: Commit**

---

### Task 4.2: Frontend file browser component

**Files:**
- Create: `deer-flow-main/frontend/src/components/workspace/file-browser/`

Basic tree + preview (md rendered, code highlighted, images inline).

---

### Task 4.3: Uploads router now writes to `{thread_cwd}/uploads/`

Modify existing `uploads.py` to use thread-scoped path; update DB `uploads` table with `thread_id`.

---

### Task 4.4: Delete `src/core/artifacts/` and `components/workspace/artifacts/`

```bash
git rm -r deer-flow-main/frontend/src/core/artifacts/
git rm -r deer-flow-main/frontend/src/components/workspace/artifacts/
# Clean up any imports that still reference these; build will fail until done
pnpm build
# fix each error
git add -A
git commit -m "refactor(frontend): remove artifacts, replaced by workspace browser"
```

---

### Task 4.5: Tag M4 exit

```bash
git tag m4-exit
```

---

## M5: Delete LangGraph + enable auth

### Task 5.1: Delete harness

```bash
cd deer-flow-main/backend
git rm -r packages/harness/
git rm langgraph.json
git rm app/gateway/routers/agents.py
git rm app/gateway/routers/runs.py
git rm app/gateway/routers/thread_runs.py
git rm app/gateway/routers/assistants_compat.py
git rm app/gateway/routers/suggestions.py
# Clean up imports; pytest + ruff will flag stragglers
make lint
# fix errors
uv sync
# remove langchain/langgraph deps from pyproject
git commit -m "chore: remove LangGraph harness, delete langgraph.json + agent routers"
```

Update `Makefile`: remove `dev` target's LangGraph spawn; `dev` now starts only gateway + frontend + nginx.

Update `docker/`: drop langgraph container.

Update root `config.yaml`: drop LangGraph section, add `cc_adapter` section:
```yaml
cc_adapter:
  timeout_seconds: 600
  max_concurrent_per_user: 3
  max_concurrent_total: 20
  env_allowlist: ["PATH", "HOME", "LANG", "LC_ALL", "TZ"]
  env_prefix_allowlist: ["CLAUDE_CODE_"]
```

---

### Task 5.2: Enable better-auth

**Files:**
- Modify: `deer-flow-main/backend/app/server/` (activate)
- Create: `deer-flow-main/backend/app/admin/cli.py`

Follow better-auth Python adapter docs to mount `/api/auth/*` with session cookie. Implement:
```bash
python -m app.admin create-user --email x@y.com --password ...
```

---

### Task 5.3: Thread isolation — user_id in threads + enforce in every endpoint

Add `Depends(current_user)` to every `/api/threads/*` route; query only threads where `user_id == current_user.id`.

Write test: two user fixtures, create threads, assert cross-access 403.

---

### Task 5.4: Audit logging

Simple structured JSON logger to stdout (or file); write spawn + result events per Section 5.

---

### Task 5.5: Tag M5 exit

```bash
git tag m5-exit
```

---

## M6: Hardening + docs

### Task 6.1: Hardening test pass

Write tests for each edge case listed in design Section 2 & 5:
- Timeout
- Per-thread 409
- Per-user 429
- Server-wide 503
- Path escape
- Env var leaked through

---

### Task 6.2: Rewrite root README.md

Face harmony-code: what it is, how to install, how to run, how to add a skill/MCP, how to invite a user.

---

### Task 6.3: Rewrite `CLAUDE.md` files

Each subtree's `CLAUDE.md` updated to reflect harmony-code architecture (not deer-flow's LangGraph setup).

---

### Task 6.4: Example tutorial — "install a skill"

Doc walking through installing a user skill (can reference emergency_plan as the example user, **without** including its code in the repo).

---

### Task 6.5: Tag v1.0.0

```bash
git tag v1.0.0 -m "harmony-code MVP"
```

---

## Global conventions

- **Run tests after every task**. Red → green → commit.
- **Commit after every task**. Small commits, clear messages.
- **When CC behavior differs from design assumption**, update `cc-cli-notes.md` AND the design doc (in the same PR/commit), don't silently work around.
- **Never skip tests with `-x` / `--no-verify`**. Fix the root cause.
- **When in doubt about CC's jsonl shape**, grep `docs/plans/cc-jsonl-samples/` — that's the ground truth.

## When things go wrong

- **"This is just a small deviation from the plan"** — write it in `cc-cli-notes.md` first, then adjust the task. Don't drift silently.
- **Tests take too long to set up** — use `pytest --collect-only` to sanity-check paths; use `vitest --run -t "specific test name"` to iterate fast.
- **CC subprocess hangs** — `lsof -p <pid>` to see what it's waiting on; likely a stuck MCP stdio server, or a skill that requires user input.
- **Session jsonl in `~/.claude/projects/` gets corrupted in dev** — just delete the offending directory; CC will rebuild; the adapter's `session_reset` path handles it at runtime.

---

## Summary table

| M | Tasks | Exit tag | Key deliverable |
|---|---|---|---|
| M0 | 0.1–0.5 | `m0-exit` | Repo + CC CLI verified |
| M1 | 1.1–1.9 | `m1-exit` | `/messages` SSE end-to-end with resume |
| M2 | 2.1–2.7 | `m2-exit` | Polished CC-native UI |
| M3 | 3.1–3.7 | `m3-exit` | MCP/skill/model config pipeline |
| M4 | 4.1–4.5 | `m4-exit` | Workspace browser, artifacts deleted |
| M5 | 5.1–5.5 | `m5-exit` | LangGraph gone, auth on, isolation tested |
| M6 | 6.1–6.5 | `v1.0.0` | Hardened, documented, MVP done |

Total ~55 tasks. Each task is 2–30 min of work, TDD-shaped where meaningful.
