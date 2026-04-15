# CC CLI flag verification notes

Purpose: verify that the installed `claude` binary exposes the flags the
harmony-code design depends on, before we write adapter code around them.

## Version

```
$ claude --version
2.1.92 (Claude Code)
```

Binary path: `/opt/homebrew/bin/claude`.

## Help capture

Full `claude --help` output was captured to `/tmp/cc-help.txt` (not tracked in
the repo). The grep commands below were run against that file.

## Grep results (first matching line per flag)

```
$ grep -E -- "--print|-p" /tmp/cc-help.txt
  -p, --print                                       Print response and exit (useful for pipes). ...

$ grep -E -- "--resume" /tmp/cc-help.txt
  -r, --resume [value]                              Resume a conversation by session ID, or open interactive picker with optional search term

$ grep -E -- "--output-format" /tmp/cc-help.txt
  --output-format <format>                          Output format (only works with --print): "text" (default), "json" (single result), or "stream-json" (realtime streaming) (choices: "text", "json", "stream-json")

$ grep -E -- "--verbose" /tmp/cc-help.txt
  --verbose                                         Override verbose mode setting from config

$ grep -E -- "--mcp-config" /tmp/cc-help.txt
  --mcp-config <configs...>                         Load MCP servers from JSON files or strings (space-separated)

$ grep -E -- "--permission-mode" /tmp/cc-help.txt
  --permission-mode <mode>                          Permission mode to use for the session (choices: "acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan")

$ grep -E -- "--model" /tmp/cc-help.txt
  --model <model>                                   Model for the current session. Provide an alias for the latest model (e.g. 'sonnet' or 'opus') or a model's full name (e.g. 'claude-sonnet-4-6').

$ grep -E -- "--add-dir" /tmp/cc-help.txt
  --add-dir <directories...>                        Additional directories to allow tool access to
```

Every required flag is present — no missing flags.

## Flag value observations vs. plan assumptions

- `--output-format`: `--help` enumerates `"text"`, `"json"`, `"stream-json"`.
  Our assumed value `stream-json` is valid.
- `--permission-mode`: `--help` enumerates `"acceptEdits"`, `"auto"`,
  `"bypassPermissions"`, `"default"`, `"dontAsk"`, `"plan"`. Plan's assumed
  four (`default`, `acceptEdits`, `bypassPermissions`, `plan`) are all valid;
  two additional modes exist that the plan didn't list (`auto`, `dontAsk`).
  No design change required — just a superset.
- `--resume` takes an optional `[value]` (session ID). When omitted it opens
  an interactive picker. For spawn use we'll always pass the session ID.

  > **Adapter hazard (M1):** If `--resume` is emitted without a value — empty string, undefined-stringified, or trailing argv position — CC opens an interactive picker and blocks the subprocess waiting for TTY input the adapter cannot provide. The adapter MUST validate `session_id` is non-empty *before* appending `--resume <id>` to argv; an absent session ID must result in the flag being omitted entirely, not passed with an empty value.
- `--mcp-config` accepts multiple configs (variadic `<configs...>`,
  space-separated). The design template uses a single config path which is
  fine — variadic accepts 1+.
- `--add-dir` is also variadic (`<directories...>`).

## Confirmed flags (2026-04-15, version 2.1.92)
- `-p` / `--print`
- `--resume <session_id>`
- `--output-format stream-json` (also supports `text`, `json`)
- `--verbose`
- `--mcp-config <path>` (variadic, space-separated 1+ configs)
- `--permission-mode <mode>` (values: `acceptEdits`, `auto`, `bypassPermissions`, `default`, `dontAsk`, `plan`)
- `--model <name>` (alias e.g. `sonnet`/`opus`, or full name e.g. `claude-sonnet-4-6`)
- `--add-dir <path>` (variadic, allowlist additional dirs)

---

# Task 0.3: live jsonl capture

Samples captured on 2026-04-15 from cwd `/tmp/cc-spike/workspace/` (resolved
to `/private/tmp/cc-spike/workspace` by macOS), stored under
`docs/plans/cc-jsonl-samples/`. All three `claude -p` invocations exited 0 with
empty stderr.

## Confirmed event types (stream-json output, not on-disk session jsonl)

Observed event `type` values emitted on stdout by
`claude -p --output-format stream-json --verbose`:

- `system` — with `subtype ∈ { hook_started, hook_response, init }`
  - `system.init` fields: `type`, `subtype`, `cwd`, `session_id`, `tools[]`,
    `mcp_servers[]`, `model`, `permissionMode`, `slash_commands[]`,
    `apiKeySource`, `claude_code_version`, `output_style`, `agents[]`,
    `skills[]`, `plugins[]`, `uuid`, `fast_mode_state`
  - `system.hook_started` / `system.hook_response` are emitted BEFORE `init`
    when the user has a `SessionStart` hook configured (e.g. the
    `superpowers` plugin injects extra context via this mechanism). These
    events have their OWN `session_id` distinct from the conversation's
    session_id (see "Observed shape deviations" below).
- `assistant` — top-level keys: `type`, `message`, `parent_tool_use_id`,
  `session_id`, `uuid`
  - `message` keys: `model`, `id`, `type`, `role`, `content`, `stop_reason`,
    `stop_sequence`, `stop_details`, `usage`, `context_management`
  - `content[]` block types observed: `text`, `thinking`, `tool_use`
- `user` — top-level keys: `type`, `message`, `parent_tool_use_id`,
  `session_id`, `uuid`
  - `content[]` block types observed: `tool_result`
- `rate_limit_event` — NEW type not in plan's Section 4. Top-level keys:
  `type`, `rate_limit_info`, `uuid`, `session_id`. Appears between assistant
  turn and `result`.
- `result` — fields: `type`, `subtype` (`"success"`), `is_error`,
  `duration_ms`, `duration_api_ms`, `num_turns`, `result` (final text),
  `stop_reason`, `session_id`, `total_cost_usd`, `usage`, `modelUsage`,
  `permission_denials[]`, `terminal_reason`, `fast_mode_state`, `uuid`.
  - `usage` keys: `input_tokens`, `cache_creation_input_tokens`,
    `cache_read_input_tokens`, `output_tokens`, `server_tool_use`,
    `service_tier`, `cache_creation`, `inference_geo`, `iterations`, `speed`

### Content block shapes (`message.content[]`)

- `{ type: "text", text: string }` — matches plan
- `{ type: "thinking", thinking: string, signature: string }` — plan omitted
  `signature`
- `{ type: "tool_use", id: string, name: string, input: object, caller: ? }` —
  plan omitted `caller` (value was null in sample 02's Read call, may carry
  subagent info)
- `{ type: "tool_result", tool_use_id: string, content: string | ... }` —
  matches plan. In sample 02, `content` is a string containing the
  `cat -n`-style Read output (`"1\thello\n2\t\n"`). May also be an array of
  blocks in other cases (not observed here).

### tool_use_id correlation (sample 02)

- Observed `tool_use.id` = `toolu_01RgmDebR1VNGzQhLNZQSCDB` on assistant turn
  (line 5)
- Same `tool_use_id` appears on the subsequent `user`/`tool_result` block
  (line 7)
- **Confirmed:** id is stable and can be used as the correlation key for
  M2's reducer.

### Resume continuity (sample 03)

- Invoked with `--resume 679d94b0-4b05-4d48-8b4d-fd56490083b1`.
- The `system.init` frame and all subsequent `assistant` / `result` /
  `rate_limit_event` frames carry the SAME `session_id`
  (`679d94b0-...`) — i.e. CC does NOT fork a new session for a resumed `-p`
  invocation. The session file on disk is appended to.
- The response `"You asked me to say hi in one word."` confirms prior
  context was recovered.
- Caveat: the pre-init `system.hook_started` / `system.hook_response` events
  carry a DIFFERENT `session_id` (e.g. `71b5ab97-...`), which appears to be
  a per-invocation hook correlation id. **M1 parser must ignore / skip these
  hook frames for session continuity purposes** — the canonical session_id
  is the one on the `init` frame and beyond.

## Session jsonl location

- Listing: `ls ~/.claude/projects/`
- For cwd `/tmp/cc-spike/workspace/` the project directory name is:
  **`-private-tmp-cc-spike-workspace`**
- Naming scheme (observed): leading dash + the REALPATH of the cwd with
  every `/` replaced by `-`. On macOS, `/tmp` is a symlink to `/private/tmp`,
  so the CWD realpath is `/private/tmp/cc-spike/workspace`, which becomes
  `-private-tmp-cc-spike-workspace`. **The plan's guess
  `-tmp-cc-spike-workspace` was wrong** — this matters for M1's SessionStore:
  we must `realpath()` the thread cwd before computing the project-dir name,
  or CC will not find the session file to append to on resume.
- Inside the project dir, each session is one file: `<session_id>.jsonl`.
  Two files were produced by this task:
  - `679d94b0-4b05-4d48-8b4d-fd56490083b1.jsonl` — sample 01 and the
    sample 03 resume both appended to this file (same session).
  - `eed622ae-e2bf-44b4-b912-89d368c467ce.jsonl` — sample 02 (a fresh non-
    resumed `-p` call spawned a new session).

### On-disk session jsonl has a DIFFERENT shape than stream-json stdout

Top-level keys of events inside `<sid>.jsonl`:
`type`, `operation`, `timestamp`, `sessionId` (camelCase!), `content`

Event `type` values seen on disk include `queue-operation`, `user`,
`attachment`, `assistant`. These do NOT appear on stdout. Takeaway:
the on-disk session file is CC's internal format and is NOT the same as
the `--output-format stream-json` wire format. The Gateway should forward
the stdout stream to the frontend, not re-read the on-disk jsonl (except
for debug / history-replay endpoints which must parse the on-disk shape
separately).

## Observed shape deviations (vs. plan Section 4)

Design doc at `docs/plans/2026-04-15-harmony-code-design.md` Section 4
declared these TypeScript types; the following deviations should be
reconciled when M2's parser is implemented. **This notes file does NOT
edit the design doc itself** — it records the facts for later decision.

1. **`system.init` has many more fields than listed.** Plan lists
   `session_id, model, cwd, tools[], mcp_servers[]`. Actual additional
   fields: `permissionMode`, `slash_commands[]`, `apiKeySource`,
   `claude_code_version`, `output_style`, `agents[]`, `skills[]`,
   `plugins[]`, `uuid`, `fast_mode_state`. Parser should tolerate unknown
   fields; UI can optionally surface e.g. `claude_code_version` for debug.

2. **Two pre-init `system` subtypes exist when user has a `SessionStart`
   hook:** `hook_started` and `hook_response`. Plan did not anticipate
   these. They carry their own per-invocation `session_id` distinct from
   the conversation session_id. Parser must either (a) recognize and skip
   subtypes other than `init`, or (b) pass them through as diagnostic
   events but NEVER use their `session_id` as the canonical one.

3. **New event type `rate_limit_event`** (not in plan). Appears between
   assistant turn and `result`. Shape:
   `{ type: "rate_limit_event", rate_limit_info: { status, resetsAt,
   rateLimitType, overageStatus, overageResetsAt, isUsingOverage },
   uuid, session_id }`. Frontend should ignore for message flow; Gateway
   may surface to a status UI later.

4. **`assistant` / `user` events carry top-level `session_id` and `uuid`**
   not listed in plan (plan only had `message` + `parent_tool_use_id`).
   The top-level `session_id` is identical across all non-hook frames in
   a turn, so reducer keys off `message.id` as planned; `uuid` is a
   per-frame id useful for dedup if resume replays partial frames.

5. **Every frame has a top-level `session_id`.** Plan implied only `init`
   did. Non-issue, just more consistent than expected.

6. **`thinking` blocks include a `signature` field** not in plan's
   `CCBlock` union. Shape: `{ type: "thinking", thinking: string,
   signature: string }`. Likely the anti-forgery signature Anthropic ships
   with extended-thinking content. Parser should preserve as-is (for
   future `--resume`-time replay validation) but does not need to display.

7. **`tool_use` blocks include a `caller` field** (value `null` in our
   sample, likely non-null for subagent-spawned tool calls).
   Shape: `{ type: "tool_use", id, name, input, caller }`. Plan's union
   omitted `caller`.

8. **`result` has many more fields than plan's `{ duration_ms,
   total_cost_usd?, usage? }`:** `subtype`, `is_error`, `duration_api_ms`,
   `num_turns`, `result` (final text), `stop_reason`, `session_id`,
   `modelUsage`, `permission_denials[]`, `terminal_reason`,
   `fast_mode_state`, `uuid`. `usage` itself also has more subfields
   (`cache_creation_input_tokens`, `cache_read_input_tokens`,
   `server_tool_use`, `service_tier`, `cache_creation`, `inference_geo`,
   `iterations`, `speed`). Parser should pass-through; UI picks what to
   render.

9. **Session jsonl directory uses REALPATH, not literal cwd.** Plan
   assumed `-tmp-cc-spike-workspace`; actual is
   `-private-tmp-cc-spike-workspace`. See "Session jsonl location" above.
   This is the most load-bearing deviation for M1's SessionStore — must
   `os.path.realpath(cwd)` before computing the project dir name, else
   session file lookup/cleanup will miss. Mitigation: under Linux (where
   threads live under `backend/.deer-flow/threads/{tid}/.../workspace`)
   there's likely no symlink indirection, so this mostly bites macOS
   devs/tests; still, always realpath for safety.

10. **On-disk session jsonl is a different format** from the stream-json
    stdout wire format (different top-level keys, extra event types like
    `queue-operation` and `attachment`). See "Session jsonl location"
    above. M2 parser design is for stream-json stdout; any feature that
    reads the on-disk file (e.g. `/api/threads/{tid}/session-jsonl`)
    needs a separate parser.
