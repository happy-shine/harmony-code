# CLAUDE.md (harmony-code fork of deer-flow backend)

> **This tree is being rewritten for harmony-code.** Do NOT treat the rest of
> the backend as authoritative — much of it (LangGraph runtime, agent harness,
> `api/chat/stream` endpoints) is scheduled for removal in M5.

## Where to read before making changes

- `docs/plans/2026-04-15-harmony-code-design.md` — target architecture. CC
  (claude-code CLI) replaces the LangGraph runtime; ephemeral subprocess per
  message with `--resume` for continuity; CC session jsonl is conversation
  SoT; DB only stores `thread_id ↔ session_id + cwd`.
- `docs/plans/2026-04-15-harmony-code-plan.md` — task-by-task implementation
  plan. Current milestone governs what should and shouldn't be touched.
- `docs/plans/cc-cli-notes.md` — empirical facts about the CC CLI (observed
  flags, event shapes, jsonl location, MCP + skill discovery). Source of
  truth for adapter behavior — do NOT guess.

## Kept / Removed (target state)

**Kept** (rewired): gateway routing, auth deps (better-auth), DB models for
threads/mcp/skills/memory, uploads, artifacts → workspace file tree.

**Removed in M5**: `app/agent/`, `app/graph/`, LangGraph entries in
`langgraph.json`, `api/chat/stream` routes.

**New** (M1+): `app/cc_adapter/` (stream_parser, lifecycle, argv builder,
session_store), `app/gateway/routers/messages.py` (SSE), `workspace.py`,
`cancel.py`.

## If a subagent is about to edit this tree

Check the current task in the plan first. If the edit touches code that M5
will delete, prefer leaving it alone over "fixing" it. The plan lists
exact files per task — stay within scope.
