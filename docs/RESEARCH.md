# Research notes (Pi 1.4.0)

Checked against Pi CLI **1.4.0** `--help` on the user's Mac (America/Los_Angeles).
pier maps only flags that binary exposes. Mapping is covered by unit tests +
`mock/mock_pi`; `RealPiArgvTests` checks that real Pi parses the argv (no
credentials, so no model call).

## Pi 1.4.0 flags pier uses

| Flag | Pier mapping |
|---|---|
| `--system-prompt <text>` | Agent body when `systemPromptMode` is omitted or `replace` (default) |
| `--append-system-prompt <text\|file>` (repeatable) | Same when `systemPromptMode: append` |
| `--skill <file\|dir>` (repeatable) | Resolved `skills:` names and `skillPath:` entries |
| `--no-skills` | Empty `skills: []` or `inheritSkills: false` |
| `--extension <path\|spec>` (repeatable) | `extensions` + `subagentOnlyExtensions` (after `--no-extensions`) |
| `--no-extensions` | Empty `extensions: []`, or allowlist prelude when extensions are listed |
| `--no-context-files` | `inheritProjectContext: false` (`inheritGlobalContext` ignored) |
| `--print` | Always. Pi exits non-zero when the final reply has stopReason `error`, `aborted` or `length`; `--mode json` exits 0 then (docs/cli-integration.md). stdout is discarded; the reply is read from the session file |
| `--model` `--provider` `--thinking` `--tools` `--no-tools` `--exclude-tools` `--name` | Frontmatter; `--model` also from `pier run --model` |
| `--no-mcp` | `tools:` present without an `mcp__` entry (Pi's `--tools` keeps MCP tools otherwise) |
| `--session-dir` `--session-id` `--session` | New session per run; `send` passes the session file |

## Out of scope

- Reading pi-subagents agent dirs (`~/.pi/agent/agents`, `.pi/agents`, `.agents`)
- Identity by `package.name` / `aliases` — identity is frontmatter `name` only
- Launcher / acceptance / nesting fields — ignored, never mapped
- RPC mode (`--mode rpc`): built for a long-lived controller. pier runs one prompt per
  process, so RPC would add a protocol client (stdout backpressure, extension UI
  requests, `agent_settled` tracking) without changing what pier can observe: provider
  failures still arrive only in the message stream, which pier reads from the session file.

## Format note

Agent files still use Markdown + YAML frontmatter (compatible shape with
pi-subagents docs). Pier does not install or invoke the pi-subagents package.

## Session files (checked on Pi 1.4.0)

`--session-dir D --session-id ID` writes `D/<timestamp>_<ID>.jsonl` (flat, observed on
1.4.0; pier also accepts a `D/--<cwd>--/` subdir).
Entry types seen: `session` (header with `cwd`), `session_info` (`name`),
`model_change`, `thinking_level_change`, `message` (roles `user`, `assistant`,
`toolResult`, ...). System prompt, tools, skills, extensions and context-file
flags are **not** stored, hence pier's `<ID>.pier.json` sidecar for `send`.

