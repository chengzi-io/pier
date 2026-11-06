---
name: pier
description: >-
  Run Pi coding-agent subagents from reusable agent Markdown files: one md ->
  one `pi` call. Use when the user wants a named or custom Pi role, a batch of
  Pi tasks, a dry-run of the exact Pi argv, or to create/validate an agent md.
---

# pier

Host-agnostic CLI. Call by relative path from this skill directory:

```bash
python3 <skill>/scripts/pier.py <command> ...
# or: <skill>/scripts/pier <command> ...
```

Needs the Pi CLI (`pi` on PATH or `PI_BIN=/abs/path/pi`). pier does not install
Pi or manage API keys. Structured output defaults to TOON; pass `--json` for JSON.
`pier result` prints raw text by default.

## Run

```bash
pier run reviewer "review src/auth.py"              # named agent, sync
pier run reviewer --file task.md                    # long task ('-' = stdin)
pier run --agent-file /tmp/role.md --file task.md   # md used directly
pier run --instructions "You are terse." "summarise X" --model provider/id --tools read,grep
pier run reviewer "x" --dry-run                     # argv + prompt; never runs pi
pier agents show reviewer --resolved                # frontmatter -> Pi argv
pier start reviewer "x"  /  pier await JOB  /  pier result JOB  /  pier cancel JOB
pier resume JOB "follow-up"                         # continues that job's Pi session
```

CLI flags override frontmatter. Agent body / `--instructions` become Pi
`--system-prompt` (or `--append-system-prompt` when `systemPromptMode: append`).
User prompt is only handoff + task. `resume` does not re-send the system prompt
(Pi keeps the session's original). Jobs / sessions live under `~/.pier/`
(`PIER_HOME`); `--cwd` is only Pi's workdir + project agent root.

## Batch

```bash
pier batch run --file jobs.json [--concurrency 4]
pier batch await|status|result|cancel BATCH_ID
```

## Agents

```bash
pier agents list                         # name + description
pier agents new NAME --file def.md       # -> <cwd>/.pier/agents/ (default)
pier agents new NAME --file def.md --user   # -> ~/.pier/agents/
pier agents validate def.md
pier agents paths
```

Lookup (later wins): `<skill>/agents` < `--agent-dir`/`PIER_AGENT_DIRS` <
`~/.pier/agents` < `<cwd>/.pier/agents`. Pier never reads pi-subagents dirs,
`~/.pi/agent/agents`, `.pi/agents`, or `.agents`.

Creating/editing an agent: read [Agent 编写规范](references/agent-authoring.md),
validate + dry-run, then `agents new`. Do not edit this skill's `agents/`
(examples only). Do not write into pi-subagents dirs unless the user names that
path and you use `--dir`.
