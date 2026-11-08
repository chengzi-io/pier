# pier

Run [Pi](https://pi.dev) coding-agent subagents from reusable Markdown agent files.
One `pier run`, one isolated `pi` session, one reply.

pier is a small, dependency-free Python CLI packaged as an agent skill. It is built to be
called by another AI agent (Claude Code, Codex, or anything that can run a shell
command), so its interface is optimised for few tool calls and few tokens:

- **Synchronous.** `pier run` waits for Pi and prints only the reply. No job ids, no polling.
- **One handle.** An 8-char session ref (or a `--name` handle) is the only thing to keep;
  `send`, `result` and `ps` take it.
- **Bounded output.** Replies over 24k chars are previewed and spilled to a file, so the
  caller never re-reads a huge blob to fetch one detail.
- **One way to do each thing.** No aliases or overlapping flags.
- **Honest failures.** A provider error or abort is a failure even when Pi printed text
  earlier in the turn. A killed pier takes Pi down with it.

The agent file format follows [pi-subagents](https://github.com/nicobailon/pi-subagents)
(portable fields only). pier does not depend on that extension, install Pi, manage API
keys, or change Pi configuration.

## Requirements

- Python 3.9+ (standard library only)
- The Pi CLI on `PATH`, or `PI_BIN=/abs/path/to/pi`

pier is tested with Pi 1.4.0. Any Pi release works if it supports the flags pier passes:
`--print`, `--session-dir`, `--session-id`, `--session`, `--system-prompt`, plus whichever
your agents use (`--model`, `--provider`, `--thinking`, `--tools`, `--no-tools`,
`--exclude-tools`, `--name`, `--append-system-prompt`, `--skill`, `--no-skills`,
`--extension`, `--no-extensions`, `--no-context-files`). Use `--dry-run` to see the exact
command.

## Installation

As a skill:

```bash
npx skills add <path-or-repo> -y --skill pier
```

Or clone the repository and call `scripts/pier` directly; it needs no install step
and locates its own skill directory from its path, so the copy works under any host that
can run a shell command. The repository root is the skill: `SKILL.md`, `agents/`,
`references/` and `scripts/` live side by side.

In Claude Code, the skill's `SKILL.md` injects the agent list when the skill loads
(`` !`…/scripts/pier agents list` ``) and pre-approves `scripts/pier` through
`allowed-tools`, so the calling agent can pick an agent without spending a tool call on
listing. Hosts that do not expand that line must run that absolute path once instead.

## Quick start

```bash
./scripts/pier agents list
./scripts/pier run reviewer "review src/auth.py"
# stderr: pier: f3b2a1c0 started (reviewer)
# stdout: the review
# stderr: pier: f3b2a1c0 ok (reviewer) · 12k tok · $0.04

./scripts/pier send f3b2a1c0 "also check the token refresh path"
./scripts/pier ps                        # recent sessions of this project
```

## Usage

`pier` in the tables below is shorthand for `scripts/pier` from the repository root.

| Command | Description |
|---|---|
| `pier run AGENT TASK...` | Start a new session, wait, print the reply |
| `pier run AGENT --file task.md` | Read the task from a file (`-` = stdin) |
| `pier run --agent-file role.md TASK...` | Use an agent file directly; frontmatter is optional |
| `pier send SESSION TASK...` | Follow up in the same session with the same agent setup |
| `pier result SESSION` | Print the last turn's reply again |
| `pier ps [SESSION] [--cwd DIR]` | Recent sessions (default: this cwd, newest 10) as TOON rows |
| `pier agents list \| show \| paths \| validate \| new` | Inspect, validate and save agent files |

`run` options: `--cwd DIR`, `--model PROVIDER/ID`, `--provider`, `--thinking LEVEL`,
`--tools CSV` or `--no-tools`, `--exclude-tools CSV`, `--name HANDLE`, `--worktree`,
`--file PATH`, `--max-chars N`, `--dry-run`. Command-line values override frontmatter.
A task that starts with `-` goes after `--`. `pier <command> -h` documents every argument.

### Handles

`run` prints an 8-char ref on stderr; the full session uuid and a `--name` handle
(newest wins on repeats) resolve everywhere a session is taken, in that order before a
unique uuid prefix. An unknown ref errors with a hint of the five most recent sessions, so
the caller recovers without a second tool call.

### Output and exit codes

| Situation | stdout | stderr | Exit |
|---|---|---|---|
| Success | reply | `pier: <ref> started (agent)` + `pier: <ref> ok (agent) · Nk tok · $X` | 0 |
| Model error or abort | — | `pier: error: model run error: …` | Pi's code, else 1 |
| Reply cut off at the token limit | partial reply + `pier: error: … (continue it: pier send <ref> "continue")` | Pi's code, else 1 |
| Pi exited non-zero | — | `pier: error: pi exited N: <stderr tail>` | N |
| Session busy: its turn is still running, or another `send` holds it | — | `pier: error: session <ref> is running/is already in use …` | 2 |
| Invalid arguments, unknown agent or session, Pi not found | — | `pier: error: …` | 2 |

The started line is printed before Pi starts, so the ref survives a killed run. The final
stderr line carries tokens and cost from the session's usage, so the caller gets the price
without another tool call. `--dry-run` prints `cd <cwd> && pi …` and runs and writes
nothing; values longer than 400 chars (task, system prompt) are clipped and shown with
their full length, and with `--worktree` the printed `cd` is the worktree placeholder
(`$PIER_HOME/worktrees/<ref>`) with a note naming the project it snapshots. `agents list`,
`ps`, `paths`, `validate` and `new` print [TOON](https://github.com/toon-format/spec).

### Output budget

The reply is capped at `--max-chars` (default 24,000, env `PIER_MAX_CHARS`; `0` = path only;
a negative value is an error).
An over-budget reply is previewed (at most `min(2000, max-chars)` chars) and spilled
byte-identical to `$PIER_HOME/out/<sid>.md`; stdout's last line is the path, so the caller
reads the file directly instead of re-running with a bigger budget. `pier send <ref>
"continue"` extends a reply cut off at the model's output token limit.

### Long runs

Pi runs often take minutes. If the host can run a command in the background (Claude Code:
Bash with `run_in_background`), use that instead of a long foreground timeout; the host is
notified when pier exits.

Pi runs in pier's process group, and pier terminates it on `SIGTERM`, `SIGHUP` or
`SIGINT`. A host timeout therefore never leaves Pi running with nobody to read its reply.
The session keeps everything up to the interruption: `pier send SESSION "continue"`.
`SIGKILL` cannot be intercepted: if only pier's pid gets it, Pi keeps running until it
finishes.

### Isolation: `--worktree`

`pier run --worktree` gives the session a fresh `git worktree` of the caller's `HEAD`
(detached, so uncommitted changes are not inherited). The worktree is created before the
session is recorded, and it is the directory Pi runs in, so `send` continues there. At
exit, including when pier is terminated, a pristine worktree (unmoved `HEAD`, clean
`git status`) is removed automatically; anything else is kept and its path printed on
stderr, so the caller can diff or commit the agent's changes. Once the worktree is
removed, `send` runs in the project directory again. `pier ps` lists a worktree session
under its project, not under the worktree path.
`send` and `result` refuse a session while its turn is running, and one `send` per session
at a time (a lock left by a killed pier is reclaimed when its pid is gone).

## Agent files

```markdown
---
name: reviewer
description: Code review focused on correctness, security, and clarity. Read-mostly.
tools: read, grep, find, ls, bash
excludeTools: write, edit
thinking: medium
---

You are a rigorous code reviewer. ...
```

The body becomes Pi's system prompt. See [Writing agent files](references/agent-authoring.md)
for every supported field. Example agents live in [`agents/`](agents/).

Lookup order, lowest to highest priority (a later directory wins on the same name):

1. `<skill>/agents` — bundled examples
2. `$PIER_AGENT_DIRS` — extra directories, `:`-separated
3. `$PIER_HOME/agents` — user agents (`agents new --user`)
4. `<cwd>/.pier/agents` — project agents (`agents new` default)

pier never reads pi-subagents directories (`~/.pi/agent/agents`, `.pi/agents`, `.agents`).

### Frontmatter mapping

| Field | Pi argument |
|---|---|
| `name` | `--name`; defaults to the file name; lookup key |
| `description` | none (shown by `pier agents list`; `validate` warns if missing) |
| `model` | `--model` verbatim; `inherit` or empty = omitted |
| `thinking` | `--thinking`, unless `model` has a `:level` suffix |
| `defaultProvider` | `--provider`, only when `model` has no `provider/` prefix |
| `tools` | `--tools a,b`; empty = `--no-tools`; extension-only tool names dropped |
| `excludeTools` | `--exclude-tools a,b` |
| `systemPromptMode` | `replace` (default) = `--system-prompt`; `append` = `--append-system-prompt` |
| `skills` / `skill` | repeated `--skill <resolved path>`; empty or `inheritSkills: false` = `--no-skills` |
| `skillPath` | repeated `--skill <path>`, relative to the agent file |
| `extensions`, `subagentOnlyExtensions` | `--no-extensions` + repeated `--extension`; empty `extensions` = `--no-extensions` only |
| `inheritProjectContext: false` | `--no-context-files` |
| anything else, including `advertise` / `async` | ignored, no effect (`validate` and `--dry-run` list it) |

pier always adds `--print`, `--session-dir $PIER_HOME/sessions`, then `--session-id <new
uuid>` (run) or `--session <session file>` (send), then `--append-system-prompt` with the
report contract (the caller sees only the final message: result first, evidence as
path:line, no preamble), then `-- <task>`. Pi's stdin and stdout go to `/dev/null`; the
reply is read from the session file. Tasks over 100,000 bytes go through a temporary file
passed as `@path`, deleted when Pi exits.

## Files

pier writes only these:

| Path | Content | When |
|---|---|---|
| `$PIER_HOME/sessions/<sid>.pier.json` | cwd, flags, name, pid + its start time, worktree of the run | before Pi starts |
| `$PIER_HOME/sessions/<sid>.lock` | pid of the `send` that holds the session | while `send` runs |
| `$PIER_HOME/sessions/--<cwd>--/<time>_<sid>.jsonl` | Pi's own session file | while Pi runs |
| `$PIER_HOME/out/<sid>.md` | over-budget reply, byte-identical | spill on `--max-chars` |
| `$PIER_HOME/worktrees/<ref>/` | `--worktree` checkout | while the session runs; kept when dirty |
| `<tmp>/pier-*/prompt.md` | tasks over 100 KB | while Pi runs |
| `<cwd>/.pier/agents/`, `$PIER_HOME/agents/` or `--dir` | agent files | `agents new` only |

It reads the four agent directories, files you pass (`--file`, `--agent-file`),
and Pi's skill locations when resolving `skills:` names
(`<cwd>/.pi/skills`, `<cwd>/.agents/skills`, `pi.skills` in `package.json` files,
`.pi/settings.json`, `~/.pi/agent/skills`, `~/.agents/skills`, `~/.pi/agent/settings.json`).
Pi itself reads and writes project files according to the agent's tools.

Directories pier creates are `0700`; the sidecars, spilled replies and task files are
`0600`. A sidecar that is not valid JSON is skipped with a `pier: note:` line instead of
breaking `ps` or ref resolution.

Old files under `$PIER_HOME` can be removed whenever their sessions are done.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PI_BIN` | `pi` | Pi executable |
| `PIER_HOME` | `~/.pier` | Sessions, spilled replies, worktrees, user agents |
| `PIER_AGENT_DIRS` | — | Extra agent directories, `:`-separated |
| `PIER_MAX_CHARS` | `24000` | Default `--max-chars` |

## Design notes

**Why `--print` and not JSON or RPC mode.** In JSON mode Pi exits 0 when the final reply
errors or is aborted; print mode exits non-zero. pier also checks the final message's
`stopReason` in the session file, so a failed turn is never reported as success. RPC mode
suits a long-lived controller; pier sends one prompt per process, so RPC would add a
protocol client without making anything observable that the session file does not already
show. See [docs/RESEARCH.md](docs/RESEARCH.md).

**Why a sidecar file.** Pi's session file records cwd, model, thinking and messages, but
not the system prompt, tools, skills or extensions. `<sid>.pier.json` keeps those flags
so `send` continues with the same role; its pid also lets `pier ps` show which sessions
are running.

## Development

```bash
python3 -m unittest discover -s tests -v
PIER_TEST_REAL_PI=$(command -v pi) python3 -m unittest discover -s tests
```

Tests use [`mock/mock_pi`](mock/mock_pi) and never call a model. The optional real-Pi
test points Pi at an empty config directory, so it only checks that Pi accepts pier's
arguments and fails on the missing API key.

## License

[MIT](LICENSE)
