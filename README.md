# pier

Let your coding agent hand work to [Pi](https://pi.dev) subagents, on whatever model you
choose.

Claude Code, Codex and similar agents burn their own context and quota on every file
they read. pier lets them delegate instead: you describe a role once in a Markdown file
(system prompt, tools, model), and the agent runs `pier run reviewer "…"`. Pi does the
work on the model that file names (a cheap one for lookups, a strong one for review),
and only the final reply comes back. One `pier run` is one isolated Pi session with one
reply.

pier is a small, dependency-free Python CLI packaged as an agent skill. Its interface is
built for an AI caller: few tool calls, few tokens.

- **Synchronous.** `pier run` waits for Pi and prints only the reply. No job ids, no polling.
- **One handle.** An 8-char session ref is the only thing to keep; `send` and `ps` take it.
- **Small surface.** Behaviour lives in the agent file (`AGENT` or `--agent-file`); the
  command line only adds `--model`, `--worktree` and `--dry-run`.
- **Bounded output.** Replies over 24k chars are previewed and spilled to a file.
- **Honest failures.** A provider error or abort is a failure even when Pi printed text
  earlier in the turn. A killed pier takes Pi down with it.

The agent file format follows [pi-subagents](https://github.com/nicobailon/pi-subagents)
(portable fields only). pier does not depend on that extension, install Pi, manage API
keys, or change Pi configuration.

## Requirements

- Python 3.9+ (standard library only)
- The Pi CLI on `PATH`, or `PI_BIN=/abs/path/to/pi`, with your providers logged in

Tested with Pi 1.4.0. `--dry-run` prints the exact Pi command, so you can check
another Pi version.

## Installation

As a skill:

```bash
npx skills add chengzi-io/pier -y --skill pier
```

Or clone the repository and call `scripts/pier` directly. There is no install step. The
repository root is the skill: `SKILL.md`, `agents/`, `references/` and `scripts/` sit
side by side.

In Claude Code, `SKILL.md` puts the agent list into the skill when it loads and
pre-approves `scripts/pier` through `allowed-tools`, so the agent can pick a role without
an extra tool call.

## Quick start

Write a role that runs on a cheap model (use any id from `pi --list-models SEARCH`):

```markdown
<!-- .pier/agents/scout.md -->
---
name: scout
description: Fast read-only lookups in this repo.
model: deepseek/deepseek-v4.1-flash
tools: read, grep, find, ls
thinking: low
---

You answer questions about this codebase. Cite path:line. Never edit files.
```

Use it (your agent does the same):

```bash
./scripts/pier agents list
./scripts/pier run scout "where is the session lock taken and released?"
# stderr: pier: f3b2a1c0 started (scout)
# stdout: the answer
# stderr: pier: f3b2a1c0 ok (scout)

./scripts/pier send f3b2a1c0 "and who reclaims a stale one?"
./scripts/pier run scout "same question" --model anthropic/claude-sonnet-4-5:high
./scripts/pier ps
```

## Usage

| Command | Description |
|---|---|
| `pier run AGENT [TASK...]` | Start a session in the current directory, wait, print the reply |
| `pier run --agent-file role.md [TASK...]` | Use any Markdown file as the role; frontmatter is optional |
| `pier send REF [TASK...]` | Follow up in the same session with the same agent setup |
| `pier ps [REF]` | This directory's newest 10 sessions, as TOON rows |
| `pier agents list` / `show NAME` / `validate PATH` | Inspect and check agent files |

`run` options:

- `--model PROVIDER/ID[:LEVEL]` overrides the agent's model; `:LEVEL` sets thinking.
- `--worktree` runs Pi in a git worktree of `HEAD`.
- `--dry-run` prints the Pi command and runs nothing.

Everything else (tools, prompt, skills, extensions) lives in the agent file. With no
task words, the task is read from stdin. A task that starts with `-` goes after `--`.
To work in another repository, `cd` there first. `pier <command> -h` documents every
argument.

### Long runs

Pi runs often take minutes. If the host can run a command in the background (Claude
Code: Bash with `run_in_background`), use that instead of a long foreground timeout.
Independent tasks can run in parallel, one `pier run` each.

Pi runs in pier's process group, and pier terminates it on `SIGTERM`, `SIGHUP` or
`SIGINT`, so a host timeout never leaves Pi running with nobody to read its reply. The
session keeps everything up to the interruption: `pier send REF "continue"`. `SIGKILL`
cannot be intercepted; if only pier's pid gets it, Pi runs to completion.

### Isolation: `--worktree`

`pier run --worktree` gives the session a fresh detached `git worktree` of the caller's
`HEAD` under `$PIER_HOME/worktrees/<ref>`, so uncommitted changes are not inherited. Pi
runs there, and `send` continues there. At exit, including when pier is terminated, a
pristine worktree (unmoved `HEAD`, clean `git status`) is removed; anything else is kept
and its path printed on stderr, so you can diff or commit the agent's work. Once it is
removed (by pier or by you), `send` runs in the project directory again. `pier ps` lists a worktree
session under its project.

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

The body becomes Pi's system prompt. [Writing agent files](references/agent-authoring.md)
covers every field; examples live in [`agents/`](agents/).

Lookup order, lowest to highest priority (a later directory wins on the same name):

1. `<skill>/agents`: bundled examples
2. `$PIER_HOME/agents`: your agents
3. `<cwd>/.pier/agents`: project agents

pier never reads pi-subagents directories (`~/.pi/agent/agents`, `.pi/agents`, `.agents`).

### Frontmatter mapping

| Field | Pi argument |
|---|---|
| `name` | `--name`; defaults to the file name; lookup key |
| `description` | none (shown by `pier agents list`; `validate` warns if missing) |
| `model` | `--model` verbatim; `inherit` or empty = omitted; `pier run --model` overrides it |
| `thinking` | `--thinking`, unless the model has a `:level` suffix |
| `defaultProvider` | `--provider`, only when the model has no `provider/` prefix |
| `tools` | `--tools a,b` verbatim; empty = `--no-tools` |
| `excludeTools` | `--exclude-tools a,b` |
| `systemPromptMode` | `replace` (default) = `--system-prompt`; `append` = `--append-system-prompt` |
| `skills` | repeated `--skill <resolved path>`; empty or `inheritSkills: false` = `--no-skills` |
| `skillPath` | repeated `--skill <path>`, relative to the agent file |
| `extensions`, `subagentOnlyExtensions` | `--no-extensions` + repeated `--extension`; empty `extensions` = `--no-extensions` only |
| `inheritProjectContext: false` | `--no-context-files` |
| anything else, including `advertise` / `async` | ignored, no effect (`validate` and `--dry-run` list it) |

`tools` entries reach Pi unchanged. Pi accepts built-in, extension and custom tool
names and ignores one that matches nothing, so `mcp__<server>__*` narrows MCP tools the
way Pi's own `--tools` does, and `subagent` works while pi-subagents is loaded. pier
never adds `--no-mcp`: to keep MCP tools away from the model, write
`excludeTools: mcp__*`.

pier always adds `--print`, `--session-dir $PIER_HOME/sessions`, then `--session-id <new
uuid>` (run) or `--session <session file>` (send), an `--append-system-prompt` with the
report contract (the caller sees only the final message: result first, evidence as
`path:line`, no preamble), then `-- <task>`. Pi's stdin and stdout go to `/dev/null`;
the reply is read from the session file. Tasks over 100,000 bytes go through a temporary
file passed as `@path`.

## Reference

### Output and exit codes

| Situation | stdout | stderr | Exit |
|---|---|---|---|
| Success | reply | `pier: <ref> started (agent)` … `pier: <ref> ok (agent)` | 0 |
| Model error or abort | — | `pier: error: model run error: …` | Pi's code, else 1 |
| Reply cut off at the token limit | partial reply | `pier: error: … (continue it: pier send <ref> "continue")` | Pi's code, else 1 |
| Turn ended on a tool call with no answer (Pi exited mid-turn) | — | `pier: error: turn ended at a tool call …` | Pi's code, else 1 |
| Pi exited non-zero | — | `pier: error: pi exited N: <stderr tail>` | N |
| Session busy (turn running, or another `send` holds it) | — | `pier: error: session <ref> is running / is already in use …` | 2 |
| Invalid arguments, unknown agent or session, Pi not found | — | `pier: error: …` | 2 |

The started line is printed before Pi starts, so the ref survives a killed run. An
unknown ref errors with the five most recent sessions. `--dry-run` prints
`cd <cwd> && pi …`; values over 400 chars are clipped and shown with their length. With
`--worktree` the `cd` target is the placeholder `$PIER_HOME/worktrees/<ref>`, and a note
names the project it would snapshot.
`agents list`, `agents validate` and `ps` print [TOON](https://github.com/toon-format/spec).

An over-budget reply (default 24,000 chars, `PIER_MAX_CHARS`; `0` = path only) is
previewed (at most 2,000 chars) and saved byte-identical to `$PIER_HOME/out/<sid>.md`;
stdout's last line is the path.

### Files

pier writes only these:

| Path | Content | When |
|---|---|---|
| `$PIER_HOME/sessions/<sid>.pier.json` | cwd, Pi flags, agent, pid + start time, worktree | before Pi starts |
| `$PIER_HOME/sessions/<sid>.lock` | pid of the `send` that holds the session | while `send` runs |
| `$PIER_HOME/sessions/<time>_<sid>.jsonl` | Pi's own session file | while Pi runs |
| `$PIER_HOME/out/<sid>.md` | over-budget reply | on spill |
| `$PIER_HOME/worktrees/<ref>/` | `--worktree` checkout | while the session runs; kept when changed |
| `<tmp>/pier-*/prompt.md` | tasks over 100 KB | while Pi runs |

It reads the three agent directories, files you pass with `--agent-file`, and Pi's
standard skill directories when resolving `skills:` names (`<cwd>/.pi/skills`,
`<cwd>/.agents/skills`, `~/.pi/agent/skills`, `~/.agents/skills`). Pi itself reads and
writes project files according to the agent's tools.

Directories pier creates are `0700`; sidecars, spilled replies and task files are `0600`.
Old files under `$PIER_HOME` can be deleted once their sessions are done.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PI_BIN` | `pi` | Pi executable |
| `PIER_HOME` | `~/.pier` | Sessions, spilled replies, worktrees, user agents |
| `PIER_MAX_CHARS` | `24000` | Reply budget on stdout |

### Design notes

**Why `--print` and not JSON or RPC mode.** In JSON mode Pi exits 0 when the final reply
errors or is aborted; print mode exits non-zero. pier also checks the final message's
`stopReason` in the session file, so a failed turn is never reported as success. RPC
mode suits a long-lived controller; pier sends one prompt per process. See
[docs/RESEARCH.md](docs/RESEARCH.md).

**Why a sidecar file.** Pi's session file records cwd, model, thinking and messages, but
not the system prompt, tools, skills or extensions. `<sid>.pier.json` keeps those flags
so `send` continues with the same role; its pid lets `pier ps` show running sessions.

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
