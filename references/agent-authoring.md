# Writing agent files

An agent is one Markdown file: YAML frontmatter (portable fields) plus a body (the
role). The frontmatter `name` is the agent's identity.

## 1. Minimal template

```markdown
---
name: sql-reviewer
description: Review SQL migrations for correctness and lock risk. Read-only.
tools: read, grep, find, ls
thinking: medium
---

You are a SQL migration reviewer.

Scope:
- Only review; never edit files.
- Focus on correctness, locking/downtime risk, and rollback safety.

Output:
- Findings ordered by severity, each with file:line and a one-line reason.
- End with "OK to merge" or "Changes needed".
```

## 2. Supported frontmatter

| Field | Pi effect | Notes |
|---|---|---|
| `name` | `--name` | Defaults to the file name. Letters, digits, `.` `_` `-`, max 64. Lookup key |
| `description` | none | Shown by `pier agents list`; write one |
| `model` | `--model` verbatim | e.g. `anthropic/claude-sonnet-4-5`, `provider/id:low`. `inherit` or empty = omitted |
| `thinking` | `--thinking` | `off` `minimal` `low` `medium` `high` `xhigh` `max`. A `:level` suffix on `model` wins |
| `defaultProvider` | `--provider` | Only when `model` has no `provider/` prefix |
| `tools` | `--tools` CSV | Pi tool names. Empty = `--no-tools`. MCP tools (`mcp__<server>__<tool>` or `mcp__<server>__*`) pass through; extension-only names are dropped |
| `excludeTools` | `--exclude-tools` CSV | |
| `systemPromptMode` | `--system-prompt` / `--append-system-prompt` | `replace` (default) or `append` |
| `skills` / `skill` | repeated `--skill <resolved path>` | Names resolved from Pi's project, package, settings and user skill locations. Empty list or `inheritSkills: false` = `--no-skills`. An unknown name is an error |
| `skillPath` | repeated `--skill <path>` | Relative to the agent file's directory; absolute paths as is |
| `extensions` | `--no-extensions` + repeated `--extension` | Empty list = only `--no-extensions`. Relative paths resolve against the agent file; `builtin:` `npm:` `git:` `http(s):` `file:` and absolute paths pass through |
| `subagentOnlyExtensions` | joins the `--extension` allowlist | Same rules as `extensions` |
| `inheritProjectContext` | `--no-context-files` | Only when explicitly `false` |
| `inheritSkills` | `--no-skills` | Only when explicitly `false` |

`advertise`, `async`, `inheritGlobalContext`, `launcher`, `timeoutMs`, `acceptance*`,
`memory`, `output`, nesting fields and anything else are **ignored and have no
effect**. `pier agents validate` and `--dry-run` list them.

## 3. Body

The body is passed as Pi `--system-prompt` (or `--append-system-prompt`); the user
prompt is the task text unchanged. Pi's session file does not store the system prompt,
tools, skills or extensions, so `pier send` replays the first run's flags and the role
stays the same in follow-ups.

Keep the body to reusable role rules. Put the concrete task, paths and one-off context in
the task.

## 4. Choosing tools and thinking

- Read-only research or review: `tools: read, grep, find, ls`, optionally
  `excludeTools: write, edit`.
- Code changes: omit `tools` (Pi's default set) or list them.
- Text only: empty `tools:` (= `--no-tools`).
- `thinking`: `low` for lookups, `medium` for review and planning, `high` for hard reasoning.

## 5. Validate and save

```bash
pier agents validate /tmp/sql-reviewer.md
pier run --agent-file /tmp/sql-reviewer.md "sample" --dry-run
pier agents new sql-reviewer --file /tmp/sql-reviewer.md          # <cwd>/.pier/agents/
pier agents new sql-reviewer --file /tmp/sql-reviewer.md --user   # $PIER_HOME/agents/ (default ~/.pier/agents/)
```

- An existing file needs `--force`.
- `validate` checks the name, body, `thinking`, `systemPromptMode` (`replace` or `append`),
  the `inheritProjectContext` / `inheritSkills` booleans, and tool names; it warns about
  fields with no effect (including `defaultProvider` without a `model`).
- Do not write into this skill's `agents/` (examples; updates overwrite them).
