# Installing a skill

A harmony-code skill is a `SKILL.md` plus any supporting files that Claude
Code (CC) picks up when it runs. On every CC spawn the gateway wipes the
thread's `.claude/skills/` directory and re-symlinks the caller's enabled
skills from `$HARMONY_DATA_DIR/skills_store/<id>/` — edits through the
API take effect on the next message with no server restart. The on-disk
format is the same one Anthropic documents at
<https://code.claude.com/docs/en/skills>; harmony-code just owns the
install pipeline and the per-thread composition.

## A minimal SKILL.md

Front-matter with `name` and `description`, then a markdown body. Keep the
body short — CC loads it when the description matches the user's request,
so the description is what decides whether the skill gets invoked at all.

```markdown
---
name: repo-report
description: Produce a short written summary of a code repository. Use when the user asks for a repo overview, a dependency summary, or "what does this project do".
---

# repo-report

When asked to summarize a repository, do the following:

1. Read the README if one exists, then the top-level config
   (`pyproject.toml`, `package.json`, `go.mod`, etc.) to identify the
   language and entry points.
2. List the top-level directories with a one-line purpose for each.
3. Report in three paragraphs: purpose, structure, notable dependencies.

Do not run tests or network calls. Do not modify files.
```

That is a complete skill. `name` must match the directory name CC sees
under `.claude/skills/` (harmony-code sets that from the front-matter
`name:` field — see `backend/app/skills/installer.py::parse_skill_name`;
falls back to the directory name if the front-matter is missing).

## Authenticate

Both install endpoints require a session cookie. Sign in once and keep
the cookie jar:

```bash
curl -c cookies.txt -X POST http://127.0.0.1:8000/api/auth/sign-in/email \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"..."}'
```

All further commands use `-b cookies.txt` to send that cookie.

The backend runs at `127.0.0.1:8000` by default (see the README's "Run
the backend" section). Adjust the host/port if you deploy elsewhere.

## Route A: zip upload

On-disk authoring. Lay the skill out as a directory with `SKILL.md` at
the root:

```
repo-report/
├── SKILL.md
└── scripts/
    └── check.sh
```

Zip it. The installer accepts two layouts — zip the directory contents
so `SKILL.md` is at the zip root, or zip the wrapping directory
(GitHub-archive convention, the one-top-level-dir is stripped). Anything
else — multiple top-level entries, `SKILL.md` nested two levels deep —
fails validation.

```bash
# Option 1: zip contents (SKILL.md at zip root)
cd repo-report && zip -r ../repo-report.zip . && cd ..

# Option 2: zip the wrapping directory (works identically)
zip -r repo-report.zip repo-report/
```

Upload:

```bash
curl -b cookies.txt \
  -F "file=@repo-report.zip" \
  http://127.0.0.1:8000/api/skills/upload
```

Expected 201 response:

```json
{
  "id": "sk_a1b2c3d4e5f6",
  "user_id": "usr_...",
  "name": "repo-report",
  "source": "upload",
  "path": "/.../.harmony-data/skills_store/sk_a1b2c3d4e5f6",
  "enabled": true
}
```

## Route B: git clone

For skills published as git repositories. The repo must have `SKILL.md`
at the root; nothing else is cloned or built. The installer runs a plain
shallow `git clone` — no credentials are forwarded unless they are
embedded in the URL or a git-credential helper is configured on the
server host.

```bash
curl -b cookies.txt \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8000/api/skills/git \
  -d '{"url":"https://github.com/you/repo-report.git"}'
```

Same 201 shape as above, with `"source": "git"`. You can also pass
`"name": "my-override"` to force a skill name different from the
`SKILL.md` front-matter.

## Verify, toggle, and list

List your skills:

```bash
curl -b cookies.txt http://127.0.0.1:8000/api/skills
```

The new row should appear with `"enabled": true`. Disable it without
removing:

```bash
curl -b cookies.txt \
  -H 'Content-Type: application/json' \
  -X PATCH http://127.0.0.1:8000/api/skills/sk_a1b2c3d4e5f6 \
  -d '{"enabled": false}'
```

Flip it back by sending `{"enabled": true}`. The same PATCH supports
`{"name": "new-name"}` if you want to rename without reinstalling.

## See it in action

Open a thread and send a message whose intent matches the skill's
`description` field. For the `repo-report` example above, something like
"give me an overview of this project" is enough. CC reads the
descriptions of every enabled skill, decides when to pull one in, and
then follows the `SKILL.md` body as standing instructions.

If CC does not invoke the skill, the description is usually to blame —
see Troubleshooting.

## Update it

There is no in-place update. Skills installed from zip are immutable
from the API's point of view; skills installed from git are not auto-
pulled. To push a new version: delete, then reinstall.

```bash
curl -b cookies.txt -X DELETE http://127.0.0.1:8000/api/skills/sk_a1b2c3d4e5f6
# then re-upload or re-clone
```

If you are iterating on a skill's text, zip upload against a local
working copy is the fastest loop — each `zip && curl -F file=@...` cycle
takes effect on the very next message sent in any thread you own.

## Make a skill available to every user

There is no admin UI for this in v1.0. Install the skill as yourself to
get the filesystem layout right, then flip the owning user on the row
to `NULL`:

```bash
sqlite3 "$HARMONY_DATA_DIR/harmony.db" \
  "UPDATE skills SET user_id = NULL WHERE id = 'sk_a1b2c3d4e5f6';"
```

From that point the skill is global: every user's thread sees it in the
list with `enabled=true`, and the symlink composer adds it on every
spawn. Users cannot disable or delete a global row — PATCH and DELETE
both return 403 for rows where `user_id IS NULL`. To retract a global
skill the operator runs another SQL statement (`UPDATE skills SET user_id
= '<uid>' WHERE id = '...'` to give it back to one user, or `DELETE FROM
skills WHERE id = '...'` plus `rm -rf` on the `skills_store/<id>/` dir
to remove it entirely). This is intentional — global skills are
ops-managed, not user-managed.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `400 Skill at ... is missing SKILL.md at the top level` | Zip layout is wrong. Either zip the directory contents or zip exactly one wrapping directory. Multiple top-level entries are not stripped. |
| `400 file must be a .zip` | The upload filename does not end in `.zip`. The check is on the filename, not content sniffing. |
| `400 Zip entry '...' has unsafe path` or `... escapes destination` or `... is a symlink; not supported` | The archive contains an absolute path, `..` traversal, or a symlink entry. Rebuild the zip without those. |
| `400 git clone failed (exit N): ...` | The clone subprocess errored. Check URL, network, and auth. Credentials in the URL, if any, are redacted in the response body but logged in full at DEBUG on the server. |
| `400 Unsupported git URL scheme` | URL does not start with `https://`, `http://`, or `git@`. |
| `409 skill name already exists` on PATCH | Your user already owns another skill with that name. Skill names are unique per user (`ix_skills_user_name`). Rename one of them. |
| `403 not yours` on PATCH or DELETE | The row is global (`user_id IS NULL`) or belongs to another user. Global rows are read-only through the API. Note: skills return 403 here; the threads router returns 404 for cross-user access — the skills surface does not hide the row's existence the same way. |
| `404 skill not found` | No row with that id — either never existed or already deleted. |
| Skill is enabled but CC never invokes it | The `description` field is how CC decides when to load a skill. If it does not mention the kind of task the user is asking about, CC will skip it. Tighten the description's trigger vocabulary. Verify the skill is listed with `enabled=true` via `GET /api/skills`. |

## Why a user might write one

A user with a repeatable domain workflow — generating regulated
enterprise reports, curating and publishing a daily digest, running
through a standard incident-response checklist — can encode the
procedure once in `SKILL.md`: expected file layout, ordered phases,
templates to use, acceptable warnings, stop conditions. Every subsequent
thread they open in harmony-code picks it up automatically, and the
procedure stays in one place instead of being pasted into chat each
time. This is the same pattern the `emergency_plan` project uses to
package its report-generation playbook for a team running through CC;
the skill is user content and lives in the user's own account, not in
this repository.
