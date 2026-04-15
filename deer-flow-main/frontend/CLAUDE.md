# CLAUDE.md (harmony-code fork of deer-flow frontend)

> **This tree is being rewritten for harmony-code.** Routes, hooks, and
> message rendering tied to LangGraph are being replaced with a CC-native
> event model.

## Where to read before making changes

- `docs/plans/2026-04-15-harmony-code-design.md` §4 (前端改造) — target event
  model: TS types 1:1 with CC stream-json. Thread hooks consume
  `POST /api/threads/{tid}/messages` SSE instead of LangGraph SDK.
- `docs/plans/2026-04-15-harmony-code-plan.md` M4 — task-level frontend
  changes (thread hooks, artifact panel, skills/MCP UI).
- `docs/plans/cc-cli-notes.md` — canonical CC event shapes (assistant / user
  blocks, tool_use ↔ tool_result correlation by `tool_use_id`, system.init
  fields, rate_limit_event, hook_* frames). Source of truth; the current
  frontend's message types do NOT reflect CC yet.

## Stack (unchanged)

Next.js 16, React 19, TypeScript 5.8, Tailwind 4, pnpm 10.26.2.
Commands (`pnpm dev|build|check|lint|test|typecheck|start`) are in the
existing README; harmony-code does not change the command surface.

## Removed in M4/M5

LangGraph SDK client (`core/api/`), LangGraph-shaped message types, any
assistant-routing logic tied to sub-agents. Replaced with CC jsonl event
streaming.

## If a subagent is about to edit this tree

Check the current task in the plan first. Many files under `core/messages/`
and `core/threads/` will be rewritten — do NOT "improve" them for the old
model; wait for the task that replaces them.
