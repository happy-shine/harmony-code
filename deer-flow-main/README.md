# harmony-code

A multi-user gateway for the Claude Code CLI.

harmony-code is a fork of [deer-flow](https://github.com/bytedance/deer-flow)
that replaces the original LangGraph-based agent runtime with the `claude`
CLI as the execution substrate. The rest of deer-flow — the Next.js
frontend, the FastAPI gateway, uploads, a workspace file tree — is kept
and rewired.

> [!NOTE]
> harmony-code v1.0 is the first tagged release. It targets
> internal-team and small-deployment use. There is no public hosting.

## What it is

- The gateway sits between a browser and the Claude Code CLI. For every
  user message it spawns one `claude` subprocess, streams CC's
  `stream-json` stdout back to the frontend as SSE, and persists
  `thread_id → session_id` in sqlite so the next turn can pass
  `--resume`.
- The CC subprocess replaces the old LangGraph agent loop, all
  sub-agent / middleware machinery, and the external sandbox container.
  CC is the agent; CC's own tools (read/write/bash/web) are the toolset;
  the per-thread working directory is the sandbox.
- The gateway owns: users, auth sessions, threads, MCP/skills registry,
  per-thread workspace, uploads, user model preferences, audit logs.
- CC owns: the conversation (transcript stored in CC's session jsonl
  under `~/.claude/projects/...`), tool execution, MCP/skill invocation.
  The gateway DB does not store message content.
- Who it's for: small teams who want a shared MCP/skills surface and
  per-user isolation on top of CC without running a model-serving
  stack. CC itself runs under one service-level OAuth on the host
  (one `claude login` once); per-user identity is enforced at the
  gateway layer.

## Architecture at a glance

```
                             harmony-code
 ┌──────────┐   HTTPS   ┌──────────────────┐   ASGI   ┌────────────────────────┐
 │ Browser  │──────────▶│  Next.js (3000)  │─────────▶│ FastAPI gateway (8000) │
 │          │◀── SSE ───│   frontend/      │◀─────────│ app/gateway/           │
 └──────────┘           └──────────────────┘          │  • auth (cookie)       │
                                                      │  • threads, messages   │
                                                      │  • mcp, skills, files  │
                                                      │  • harmony.audit log   │
                                                      └────────┬───────────────┘
                                                               │ spawn per message
                                                               ▼
              ┌───────────────────────────────┐   stream-json  ┌─────────────┐
              │  $HARMONY_DATA_DIR            │◀───stdin/out──▶│  claude CLI │
              │   harmony.db      (metadata)  │                │ (subprocess)│
              │   sessions.db     (tid→sid)   │                └──────┬──────┘
              │   threads/<tid>/user-data/    │                       │
              │     workspace/    (CC cwd)    │                       ▼
              │     uploads/                  │                ~/.claude/
              │   skills_store/               │                  projects/<sid>.jsonl
              └───────────────────────────────┘                  (conversation SoT)

 removed in M5: LangGraph server on port 2024, deerflow-harness package,
 external sandbox container, lead_agent + subagent middleware chain.
```

One sqlite DB for application metadata, one for auth sessions, and a
per-thread directory tree on disk. The conversation itself is not in the
DB — it lives in CC's own session jsonl and is addressed by `session_id`.

## Requirements

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
  installed on the host and logged in (`claude login`, once). All
  gateway users share this OAuth.
- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/) (or `pip` + a
  venv).
- Node.js 22+ and `pnpm` 10.26.2 (pinned via `packageManager` in
  `frontend/package.json`).
- sqlite — bundled with Python, nothing to install.
- No Docker required for development.

## Install

```bash
# TODO: replace with your fork URL once pushed; the repo is not public yet.
git clone <your fork url> harmony-code
cd harmony-code
```

Install dependencies:

```bash
cd backend && uv sync
cd ../frontend && pnpm install
```

Pick a data root and create the schema. `HARMONY_DATA_DIR` is where the
two sqlite DBs, per-thread workspaces, uploads, the `skills_store/`, and
tmp per-spawn MCP configs live. Choose any writable path outside the
repo:

```bash
export HARMONY_DATA_DIR=$PWD/.harmony-data
cd backend && uv run alembic upgrade head
```

Create the first user:

```bash
cd backend
uv run python -m app.admin create-user \
  --email you@example.com --password 'use-a-real-one'
# add --admin to mark the user as admin (sets is_admin=true)
```

Other admin commands (see `backend/app/admin/cli.py` for the full set):

```bash
uv run python -m app.admin list-users
uv run python -m app.admin delete-user --email you@example.com
uv run python -m app.admin reset-password --email you@example.com --password 'new-one'
```

`reset-password` also invalidates existing session cookies for that
user.

> `config.example.yaml` in the repo root is legacy deer-flow and is not
> read by the harmony-code gateway. Configuration is entirely
> environment-variable driven (see below). The file is left in place
> so the legacy setup wizard still works, but you can ignore it.

## Run (dev)

Two processes, two terminals:

```bash
# backend
cd backend
export HARMONY_DATA_DIR=$PWD/../.harmony-data
uv run uvicorn app.gateway.harmony_app:app --reload --host 127.0.0.1 --port 8000
```

```bash
# frontend
cd frontend
pnpm dev
```

Open http://localhost:3000 and sign in at `/login`. The frontend talks
to the backend at `http://127.0.0.1:8000` via Next.js proxy rules.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `HARMONY_DATA_DIR` | *(required)* | Data root. Holds `harmony.db`, `sessions.db`, `threads/<tid>/user-data/`, `skills_store/`, and per-spawn MCP config temp files. |
| `HARMONY_MAX_SERVER` | `20` | Server-wide cap on concurrent CC subprocesses. |
| `HARMONY_MAX_PER_USER` | `3` | Per-user cap on concurrent CC subprocesses. Per-thread is always 1. |
| `HARMONY_CC_TIMEOUT_SECONDS` | *(unset)* | Max wall time per CC spawn. On timeout CC is terminated and the SSE stream yields `{"type":"_adapter","subtype":"error","code":"timeout"}`. Unset means no timeout. |

Concurrency limits are single-process. If you run multiple gateway
replicas, the caps are per-replica; there is no distributed counter.

The CC subprocess environment is an **allowlist**, not a blocklist.
Only these host env vars are forwarded to CC:

- `PATH`, `HOME`, `LANG`, `LC_ALL`, `TZ`
- Any variable whose name starts with `CLAUDE_CODE_`

Everything else (including `AWS_*`, `GCP_*`, `*_TOKEN`, `*_KEY`,
`DATABASE_URL`) is dropped before spawn. See
`backend/app/cc_adapter/adapter.py` (`ENV_PASSTHROUGH`,
`ENV_CLAUDE_PREFIX`).

## Adding a skill

harmony-code ships no end-user skills. Skills are user content, not a
product feature. Two ways to add one:

**Per-user skill.** In the UI, Settings → Skills → Install, or POST a
zip to `/api/skills/upload` (multipart) or a git URL to
`/api/skills/git` with a valid session cookie. The installer extracts
into
`$HARMONY_DATA_DIR/skills_store/<skill_id>/` and inserts a row owned by
the caller. When a thread spawns CC, enabled skills are materialized as
symlinks under the per-thread `.claude/skills/` directory. Full
tutorial: `docs/skills-tutorial.md`.

**System-wide skill.** Install as a user first to get the
`skills_store/` layout right, then update the row so `user_id IS NULL`
— that marks it global and every user's thread will see it. There is
no admin UI for this in v1.0; use `sqlite3` directly against
`harmony.db` or call `app.db.Db.update_skill` from `python -c`. Global
skills are out of MVP scope as a UI feature.

## Adding an MCP server

**Per-user.** UI Settings → MCP → Add server, or `POST /api/mcp` with
transport (`stdio` / `sse` / `http`), command, args, and env. The
gateway writes a per-spawn JSON file referenced by CC's `--mcp-config`,
so servers start on-demand per thread.

**Global (admin only).** Same row shape, `user_id = NULL`. No UI knob
in v1.0; use `python -c 'from app.db import *; Db(get_engine()).insert_mcp(...)'`
or edit the `mcp_servers` row directly.

## Inviting a user

Three steps:

1. Admin creates the account:
   ```bash
   uv run python -m app.admin create-user \
     --email teammate@example.com --password 'temp-secret'
   ```
   There is no email-a-link flow; pass the password out-of-band (a
   password manager share, an encrypted message, etc.). Tell them to
   change it on first login.
2. User visits `/login`, signs in. A `harmony_session` cookie is set
   (HttpOnly, SameSite=Lax, 30-day TTL).
3. Optional: pass `--admin` at create time to mark them as an admin.
   There is no separate "promote existing user" subcommand in v1.0;
   delete and recreate, or update `users.is_admin` directly.

## Isolation and security posture

- **Auth.** `POST /api/auth/sign-in/email` sets `harmony_session` as
  an HttpOnly, SameSite=Lax cookie with a 30-day TTL. `Secure` is
  added automatically for HTTPS requests.
- **Per-thread ownership.** Every `/api/threads/{tid}/*` endpoint
  verifies `threads.user_id == session.user_id` and returns 404 on
  mismatch. Cross-user access is indistinguishable from "thread does
  not exist".
- **Workspace path escape.** Every file served under
  `/api/threads/{tid}/workspace/files/{path}` is resolved and checked
  with `Path.is_relative_to(thread_cwd)` before open.
- **Env allowlist.** See above. The CC subprocess cannot read
  `AWS_*`, `DATABASE_URL`, etc.
- **CC permission mode.** The gateway always spawns CC with
  `--permission-mode bypassPermissions`. This is a design decision for
  a small-team-trusted deployment; rationale is in design Section 2.
  Users inside a thread can drive CC to read/write the entire per-thread
  workspace and run bash inside it.
- **Known gap.** CC runs under one service-level OAuth, so
  `$HOME`-scoped skills and MCP configs can leak across users at the CC
  layer. Safe for a trusted deployment. Will be re-examined before any
  public hosting — see design Section 5.

## Audit logs

- Transport: Python stdlib `logging`, named logger `harmony.audit`,
  level INFO, one compact JSON object per log line. Routing (stdout,
  journald, a log shipper, a file handler) is the operator's
  responsibility. See `backend/app/audit.py`.
- Two events per message:
  - `cc.spawn` — `user_id`, `thread_id`, `session_id`, `model`, an
    argv hash (prompt excluded), `prompt_len`, and the lists of
    enabled MCP servers and skills.
  - `cc.result` — `duration_ms`, `exit_code`, `cost_usd` (or
    `null` if CC never emitted a terminal `result` frame), and a
    `disposition` of `natural` / `disconnected` / `error`.
- Cookies and session tokens never appear in audit lines by
  construction. See `backend/app/audit_events.py` for the exact event
  shapes.

## Tests

```bash
cd backend && uv run pytest -q        # ~330 tests
cd frontend && pnpm test              # vitest
```

## Status and scope

v1.0 is feature-complete for the milestones defined in
`docs/plans/2026-04-15-harmony-code-plan.md`. Known rough edges:

- No admin UI for global MCP / skill rows. Admins poke the DB.
- No public-SaaS posture — single-OAuth CC means `$HOME` is shared at
  the CC layer (see gap above).
- Concurrency limits are per-process only; no distributed counter
  across replicas.
- No message-level rate limiting or billing integration.

Where to go next:

- `docs/plans/2026-04-15-harmony-code-design.md` — full architecture.
- `docs/plans/cc-cli-notes.md` — empirical notes on CC CLI behavior
  (flags, event shapes, jsonl location).
- `backend/docs/` — per-subsystem notes (many are legacy deer-flow and
  being retired in M6 Task 6.3).

## License and credit

MIT, same as upstream — see `LICENSE`.

harmony-code is an independent fork of
[deer-flow](https://github.com/bytedance/deer-flow) and is not
affiliated with or endorsed by ByteDance. Upstream credit belongs to
the original deer-flow authors; see the upstream README and
`LICENSE` for details.

Internationalized READMEs in this repo (`README_zh.md`, `README_ja.md`,
`README_fr.md`, `README_ru.md`) are inherited from the upstream project
and have not been updated for harmony-code yet.
