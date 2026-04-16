# CLAUDE.md (harmony-code backend)

FastAPI gateway that spawns the Claude Code (CC) CLI as an ephemeral
subprocess per message and streams its stream-json as SSE.

## Layout

- `app/gateway/` — FastAPI app + routers. `harmony_app.py` creates the
  app; `routers/messages.py` is the SSE endpoint
  (`POST /api/threads/{tid}/messages`); `routers/{workspace,uploads,mcp,
  skills,auth,harmony_models}.py` cover the rest; `deps.py` holds
  `current_user` (cookie session validation) and filesystem helpers.
- `app/cc_adapter/` — CC subprocess lifecycle. `adapter.py` (argv builder,
  run loop, timeout, `ENV_PASSTHROUGH` allowlist), `stream_parser.py`,
  `lifecycle.py`, `session_store.py` (raw sqlite3, stores
  `thread_id ↔ session_id + user_id + cwd`; deliberately NOT SQLAlchemy —
  it sits on the spawn hot path), `types.py` (`SpawnConfig`), `compose.py`
  (per-spawn MCP config + skills dir composition from DB).
- `app/db.py` + alembic migrations — SQLAlchemy models for users,
  auth_sessions, mcp_servers, skills, memory, user_prefs.
- `app/auth/` — password hashing + better-auth-style session cookies;
  `app/admin/` — CLI (`python -m app.admin create-user`, etc.).
- `app/audit.py` + `app/audit_events.py` — emit `cc.spawn` / `cc.result`
  on the `harmony.audit` logger. `app/skills/`, `app/model_catalog.py`
  — router support logic.

## Invariants — do not break

- **Per-thread concurrency = 1.** `--resume` needs strict serialization;
  admission in `messages.py` rejects a second in-flight message on the
  same thread with 409.
- **Ownership 404.** Every `/api/threads/*`, `/api/workspace/*`,
  `/api/uploads/*` route returns 404 for unknown OR not-yours. Never leak
  existence of someone else's thread.
- **No PII in audit.** `cc.spawn` / `cc.result` MUST NOT include prompt
  text, tool output, or the argv slice containing the prompt. Keep to
  ids, hashes, counts, durations, costs, disposition.
- **ENV_PASSTHROUGH is an allowlist** in `cc_adapter/adapter.py`
  (`PATH`/`HOME`/`LANG`/`LC_ALL`/`TZ` + the `CLAUDE_CODE_*` prefix).
  Everything else — `AWS_*`, `GCP_*`, `*_TOKEN`, `*_KEY`,
  `DATABASE_URL`, `REDIS_URL`, `OPENAI_*`, `ANTHROPIC_*`,
  `GITHUB_TOKEN`, etc. — is dropped by default. Do not widen the
  allowlist without security review.
- **Migrations run first.** Alembic must be applied before the server
  starts. `sessions.db` self-migrates via `ALTER TABLE` in
  `session_store.ensure_schema`.
- **Admission limits** from `HARMONY_MAX_SERVER`,
  `HARMONY_MAX_PER_USER`, `HARMONY_CC_TIMEOUT_SECONDS`. Three-layer
  rejection 503 (server) / 429 (user) / 409 (thread) — keep distinct.

## Reading order for a new task

1. `README.md` in the repo root — install, env vars, how to run.
2. `../../docs/plans/cc-cli-notes.md` — canonical CC CLI flags, event shapes,
   jsonl location, MCP + skill discovery. Source of truth for anything
   the adapter does.
3. The router for the endpoint you are touching, then follow into
   `cc_adapter/` if the change is below the gateway.

`../../docs/plans/2026-04-15-harmony-code-{design,plan}.md` are historical;
optional background, not required reading.
