# Research notes (Pi 1.4.0 / pier 0.3.0)

Checked against Pi CLI **1.4.0** `--help` on the user's Mac (America/Los_Angeles).
pier 0.3.0 maps only flags that binary exposes. No live model run was used for
this redesign; argv mapping is covered by unit tests + `mock/mock_pi`.

## Pi 1.4.0 flags pier uses

| Flag | Pier mapping |
|---|---|
| `--system-prompt <text\|path>` | Agent body / `--instructions` when `systemPromptMode` is omit/replace (default) |
| `--append-system-prompt <text\|path>` (repeatable) | Same when `systemPromptMode: append` |
| `--skill <file\|dir>` (repeatable) | Resolved `skills:` names and `skillPath:` entries |
| `--no-skills` | Empty `skills: []` or `inheritSkills: false` |
| `--extension <path\|spec>` (repeatable) | `extensions` + `subagentOnlyExtensions` (after `--no-extensions`) |
| `--no-extensions` | Empty `extensions: []`, or allowlist prelude when extensions are listed |
| `--no-context-files` | `inheritProjectContext: false` (`inheritGlobalContext` ignored) |
| `--mode json` / `--print` | Default json; `--mode print` |
| `--model` `--provider` `--thinking` `--tools` `--no-tools` `--exclude-tools` `--name` | Frontmatter / CLI |
| `--session-dir` `--session-id` `--session` `--no-session` | Jobs / resume |

## Explicitly removed in 0.3.0

- `--include-pi-subagents` and all reads of `~/.pi/agent/agents`, `.pi/agents`, `.agents`
- Identity by `package.name` / `aliases` / `runtime_name` — identity is frontmatter `name` only
- Splicing agent body into the user prompt — body goes to Pi system-prompt flags
- Launcher / acceptance / nesting fields — ignored, never mapped

## Format note

Agent files still use Markdown + YAML frontmatter (compatible shape with
pi-subagents docs). Pier does not install or invoke the pi-subagents package.
