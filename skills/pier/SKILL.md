---
name: pier
description: >-
  Delegate a task to a Pi coding-agent session on any model, from reusable agent
  Markdown files (role, tools, model); `send` continues a session. Use when the
  user wants work done by a Pi agent or role or on another model, a Pi task
  isolated in a git worktree, a follow-up to a Pi session, or to create/validate
  an agent md.
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/pier *)
---

# pier

`pier` below = `${CLAUDE_SKILL_DIR}/scripts/pier`; it is not on `PATH`, so call that
absolute path. Needs the Pi CLI (`pi` on `PATH` or `PI_BIN`).

## Agents

!`${CLAUDE_SKILL_DIR}/scripts/pier agents list`

No table above → run `pier agents list` once; never guess names. Empty model = Pi's default.

## Run

```bash
pier run AGENT "task"                        # new session in the current dir; waits, prints the reply
pier run AGENT <<'EOF'                       # long task via stdin
...
EOF
pier run AGENT "task" --model P/ID[:LEVEL]   # another model; :LEVEL sets thinking
pier run AGENT "task" --worktree             # isolated git worktree of HEAD
pier run --agent-file role.md "task"         # any md as the role (one-off role = a temp .md)
pier send REF "follow-up"                    # same session, same agent setup
pier ps [REF]                                # this dir's recent sessions (TOON)
```

**Pi sees none of this conversation.** Put the goal, exact paths, constraints and what
the reply must contain into the task; a vague brief costs extra `send` rounds. The reply
leads with the result and cites `path:line`: spot-check it, do not redo the work.

- stdout = the reply only. stderr starts with `pier: <ref> started (<agent>)`: keep the
  8-char `<ref>` for `send`/`ps`. After the run: `pier: <ref> ok|failed`. Lines
  `pier: <ref> running …` (every 2 min) are progress; no action needed.
- Failure = non-zero exit and a final `pier: error: …` line. A reply cut at the model's
  token limit is printed and exits non-zero: `pier send <ref> "continue"`.
- A run that hits its deadline (20 min default) exits 124 with no reply: the session
  survives, so continue it with `pier send <ref> "continue"`. If it timed out while
  still queued (`waiting for a slot`), Pi never started: `send` has nothing to resume,
  start the task again later instead. A line `pier: <ref> waiting for a slot (…)` means
  the run is queued behind other runs, not stuck: do not kill it.
- A reply over the budget (24k chars, `PIER_MAX_CHARS`) arrives as a 2k preview + the
  full-text path on stdout's last line: read that file, do not re-run.
- Runs take minutes: run pier in the background (Claude Code: Bash
  `run_in_background: true`); you are notified at exit, so do not poll. Read its output
  file with Read, not `cat`/`tail`: Bash output may be capped. Independent
  tasks = one background `pier run` each, in parallel. If pier is killed, Pi stops too
  and pier exits 128+signal with a `failed` line: `pier send <ref> "continue"`.
- `--worktree`: a clean worktree is removed at exit; a changed one is kept (path on
  stderr) for you to review, and `send` continues in it.
- `send` while that session's turn is running fails (exit 2): wait for the first.
- Pi runs in the current directory; for another repo, `cd` there first. A task starting
  with `-` goes after `--`.

## Agent files

Before writing one, read `${CLAUDE_SKILL_DIR}/references/agent-authoring.md`. Save it as
`.pier/agents/<name>.md` (project) or `~/.pier/agents/<name>.md` (user); the project
wins on the same name. Do not edit this skill's `agents/` (examples).

```bash
pier agents validate role.md
pier run --agent-file role.md "sample" --dry-run   # exact Pi command; runs nothing
pier agents show NAME                              # path + file
```
