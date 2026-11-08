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

`pier` below = the `scripts/pier` file beside this SKILL.md, at
`${CLAUDE_SKILL_DIR}/scripts/pier`. It is not on `PATH`: substitute its absolute
path in every command below.

pier needs the Pi CLI (`pi` on `PATH` or `PI_BIN`); it does not install Pi or
manage API keys.

## Agents

!`${CLAUDE_SKILL_DIR}/scripts/pier agents list`

If no table appeared above (host did not expand it), run `pier agents list` once;
do not guess names.

## Run

One `pier run` = one `pi` session; it waits and prints the reply. `pier send`
continues that session. An agent md is a reusable role, not a session.

```bash
pier run AGENT "task"                     # new session; waits for the reply
pier run AGENT --file task.md             # long task ('-' = stdin)
pier run --agent-file role.md "task"      # any md; a one-off role = a temp .md
pier run AGENT "task" --worktree          # snapshot of HEAD in a git worktree
pier send SESSION "follow-up"             # same session, same agent setup
pier result SESSION                       # last reply again
pier ps [REF]                             # recent sessions of this cwd (TOON)
```

Options: `--cwd DIR --model P/ID [--provider P] --thinking LEVEL --tools a,b |
--exclude-tools a,b | --no-tools --name HANDLE --file PATH --max-chars N --dry-run`.
CLI flags override agent md frontmatter. A task starting with `-` goes after `--`
(or in `--file`).

- stdout = the reply only. stderr line 1 = `pier: <ref> started (<agent>)`; keep
  the 8-char `<ref>` or the `--name` handle (default: agent name; newest wins) for
  `send`. The last stderr line has tokens + cost.
- Failure: `pier: error: ...` on stderr, non-zero exit. A model error or abort fails
  even if Pi printed text earlier. A reply cut at the token limit is printed and
  exits non-zero; `pier send <ref> "continue"` extends it.
- Replies over `--max-chars` (default 24000; `PIER_MAX_CHARS`) arrive as a preview
  (up to 2k) plus the full-text path on stdout's last line; read that file, do not
  re-run (`0` = path only).
- Pi runs take minutes. Run it in the background when it may outlast the foreground
  timeout (Claude Code: Bash `run_in_background: true`); you are notified at exit,
  so do not poll. If the host kills pier, Pi stops too: `pier send <ref> "continue"`.
  `--worktree` auto-removes a clean worktree; a changed one is kept (path on
  stderr) and `send` uses it until removed, then the project.
- `send`/`result` fail (exit 2) while the session's turn is running; concurrent
  `send`s are refused, not queued.

## Agent files

```bash
pier agents validate def.md
pier run --agent-file def.md "sample" --dry-run   # exact Pi command, runs nothing
pier agents new NAME --file def.md                # -> <cwd>/.pier/agents ; --user -> $PIER_HOME/agents
pier agents show NAME                             # path + file
pier agents paths                                 # lookup dirs, low -> high
```

Before creating or editing one, read [agent authoring](references/agent-authoring.md).
The agent md body becomes Pi `--system-prompt` (`--append-system-prompt` with
`systemPromptMode: append`). Lookup, later wins: `<skill>/agents` <
`$PIER_AGENT_DIRS` < `$PIER_HOME/agents` < `<cwd>/.pier/agents`. Do not edit this
skill's `agents/` (examples). pier never touches pi-subagents dirs; use
`agents new --dir` only for a path the user names.

State lives only in `$PIER_HOME` (default `~/.pier`).
