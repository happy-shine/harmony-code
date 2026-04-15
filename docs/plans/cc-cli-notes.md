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
