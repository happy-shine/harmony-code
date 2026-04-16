# CLAUDE.md (harmony-code frontend)

Next.js chat UI that consumes the gateway's SSE stream and renders
CC-native events.

## Stack

Next.js 16, React 19, TypeScript 5.8, Tailwind 4, pnpm 10.26.2. Commands
(`pnpm dev|build|check|lint|test|typecheck|start`) are in the root
README — not repeated here.

## Event model

TS event types mirror CC stream-json 1:1 (`system.init`, `assistant`,
`user`, `tool_use`, `tool_result`, `result`, `rate_limit_event`, `hook_*`).
See `../../docs/plans/cc-cli-notes.md` for the canonical frame shapes — that
file is the source of truth, do not guess. Thread hooks under
`src/core/threads/` consume `POST /api/threads/{tid}/messages` SSE and
reduce frames into render state.

## Auth UX

Login page posts to `/api/auth/sign-in/email`. Session cookie is
`harmony_session` (HttpOnly, SameSite=Lax). All API calls MUST use
`credentials: "include"` so the cookie rides along.

## Invariants — do not break

- **No LangGraph SDK.** The gateway streams CC stream-json; do not
  reintroduce `@langchain/langgraph-sdk` or LangGraph-shaped message
  types.
- **tool_use ↔ tool_result correlate by `tool_use_id`.** Never match by
  index, position, or heuristic; the id is the contract (see
  `cc-cli-notes.md`).
- **Cost / rate-limit UI reads from frames only.** Token usage comes
  from the `result` frame; rate-limit state comes from
  `rate_limit_event`. Do not synthesize these client-side.
- **Session cookie is the only auth surface.** Do not introduce bearer
  tokens or `Authorization` headers for app routes.

## Dead code (legacy, not wired in)

`src/core/api/` (`api-client.ts`, `stream-mode.ts`, `index.ts`) is a
LangGraph-era holdover with no remaining importers. Several files under
`src/core/messages/`, `src/core/threads/`, and `src/components/workspace/`
still contain `langgraph`-named symbols that outlived the rewrite.
Before touching any of them, verify they are on the live path; prefer
deleting to "fixing." Do not bulk-delete without verifying.

## Reading order for a new task

1. Root `README.md` — install and run.
2. `../../docs/plans/cc-cli-notes.md` — event shapes.
3. `src/core/threads/cc-hooks.ts` + `cc-stream.ts` — where SSE frames
   enter the UI. `src/core/messages/cc-reducer.ts` — how they become
   render state.

`../../docs/plans/2026-04-15-harmony-code-{design,plan}.md` are historical;
optional background, not required reading.
