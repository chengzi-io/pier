---
name: pier
description: >-
  Run Pi coding-agent sessions from reusable agent Markdown files; `send`
  continues an existing session. Use when the user wants a named or custom Pi
  role, a Pi task isolated in a git worktree, a follow-up to a Pi session, or
  to create/validate an agent md.
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/pier *)
---

# pier

`pier` below = `${CLAUDE_SKILL_DIR}/scripts/pier`, the `scripts/pier` beside this
SKILL.md. `pier` is not on `PATH`: expand that absolute path in every command below.
Other hosts: use the absolute path of `scripts/pier` next to this SKILL.md.
Needs the Pi CLI (`pi` on PATH or `PI_BIN`); pier does not install Pi or manage API keys.

## Agents

!`${CLAUDE_SKILL_DIR}/scripts/pier agents list`

If no table appears above (the host does not expand it), run
`${CLAUDE_SKILL_DIR}/scripts/pier agents list` once and pick from it; do not guess names.

## Run

One `pier run` starts one `pi` session and prints its reply; `pier send`
continues that session. An agent md is a reusable role, not a session.

```bash
pier run AGENT "task"                            # one run = one session; waits, prints the reply
pier run AGENT --file task.md                    # long task ('-' = stdin)
pier run --agent-file role.md "task"             # any md; a one-off role = a temp .md
pier run AGENT "task" --worktree                 # isolate in a git worktree of HEAD
pier send SESSION "follow-up"                    # same session, same agent setup
pier result SESSION                              # last reply again
pier ps                                          # recent sessions of this cwd (TOON)
```

Options: `--cwd DIR --model provider/id --thinking LEVEL --tools a,b | --no-tools
--exclude-tools a,b --name HANDLE --max-chars N --dry-run --extra <raw pi args, last>`.
CLI flags override frontmatter.

- stdout = the reply only. stderr line 1 = `pier: <ref> started (<agent>)`; keep the
  8-char ref (or a `--name` handle) for `send`. The last stderr line has tokens + cost.
- Failure: `pier: error: ...` on stderr, non-zero exit. A model error or abort is a
  failure even if Pi printed text earlier in the turn. A reply cut off at the token
  limit is printed and still exits non-zero; `pier send <ref> "continue"` extends it.
- Replies over 24k chars (`--max-chars`/`PIER_MAX_CHARS`) arrive as a 2k preview plus a
  path to the full text on stdout's last line; read that file, don't re-run.
- Pi runs take minutes. When your host can run a shell command in the background
  (Claude Code: Bash `run_in_background: true`), use it for runs that may outlast the
  foreground timeout; you are notified when it ends, so do not poll. If the host kills
  pier, Pi stops too: `pier send <ref> "continue"`. With `--worktree`, a clean
  worktree is auto-removed at exit; a changed one is kept and its path printed on stderr.

## Agent files

```bash
pier agents validate def.md
pier run --agent-file def.md "sample" --dry-run   # exact Pi command, runs nothing
pier agents new NAME --file def.md                # -> <cwd>/.pier/agents ; --user -> $PIER_HOME/agents
pier agents show NAME                             # path + file
pier agents paths                                 # lookup dirs
```

Before creating or editing one, read [agent authoring](references/agent-authoring.md).
The body becomes Pi `--system-prompt` (`--append-system-prompt` with `systemPromptMode: append`).
Lookup, later wins: `<skill>/agents` < `$PIER_AGENT_DIRS` < `$PIER_HOME/agents` <
`<cwd>/.pier/agents`. Do not edit this skill's `agents/` (examples). pier never reads or
writes pi-subagents dirs; use `--dir` only for a path the user names.

State lives only in `$PIER_HOME` (default `~/.pier`).
