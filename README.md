# pier

A Claude Code plugin that makes the main session an orchestrator. The main model keeps
the decisions, specs and verdicts; legwork (codebase sweeps, web lookups, multi-file
edits, long runs) goes to [Pi](https://pi.dev) agents on whatever model you choose,
through the bundled `pier` skill. Only each agent's final reply enters the main context.

Why it pays: Claude Code re-sends the whole context on every request, so a file read in
the main session is paid for again on every later turn until compaction. A delegated
read is paid once, in Pi's session, often by a cheaper model.

| Part | Where | Job |
|---|---|---|
| `pier` skill | [`skills/pier/`](skills/pier/README.md) | The delegation tool: a dependency-free CLI that turns agent Markdown files into Pi runs. Also works without the plugin, in any agent that runs shell commands |
| Agents | [`skills/pier/agents/`](skills/pier/agents/) | `chore`, `debugger`, `librarian`, `researcher`, `reviewer`, `worker`; add your own as Markdown |
| Session reminder | [`hooks/hooks.json`](hooks/hooks.json), [`scripts/session-start/`](scripts/session-start/) | Tells the main session what counts as legwork |
| Settings recipe | [below](#set-up-the-orchestrator) | Output limits and permission denies: Claude Code settings, not code |

## Requirements

- Claude Code (2.1.261+ for the output limits)
- Python 3.9+ and the Pi CLI with your providers logged in
  ([details](skills/pier/README.md#requirements))

## Install

```bash
claude plugin marketplace add chengzi-io/pier
claude plugin install pier@pier
```

This installs the skill and the reminder. For the skill alone in another agent, see
[`skills/pier/README.md`](skills/pier/README.md#installation).

## Set up the orchestrator

Three layers; only the last one ships in the plugin.

**1. Output limits** (built into Claude Code 2.1.261+), in `~/.claude/settings.json`:

```json
{ "bashOutputMaxChars": 4000, "env": { "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS": "8000" } }
```

A Bash result over 4,000 chars arrives as a file path plus a 2,000-char preview; a
whole-file `Read` over 8,000 tokens returns its first page as a `PARTIAL view`. They cap a
foreground `pier run` too, so run pier in the background, as `SKILL.md` says.

**2. Permission denies**, in their own file, started with
`claude --settings ~/.claude/settings.pier.json`:

```json
{
  "permissions": {
    "deny": [
      "Agent", "Workflow", "ScheduleWakeup", "ListAgents", "ReportFindings", "Artifact",
      "WebSearch", "WebFetch", "EnterWorktree", "ExitWorktree",
      "SendMessage", "Monitor", "NotebookEdit", "Glob", "Grep",
      "CronCreate", "CronDelete", "CronList", "RemoteTrigger"
    ]
  }
}
```

- The first row's schemas load into every request; denying them saves about 7.2k tokens
  per request. The rest load on demand, so denying them routes work to pier (web lookups
  → `researcher`, worktrees → `--worktree`) rather than saving tokens.
- Never deny `Bash` or add scoped `Bash(...)` denies: pier runs through Bash, deny beats
  allow, and each denial costs a full extra request. Keep `LSP` if a code intelligence
  plugin is installed; skills built on a denied tool (`/loop`, `/schedule`) stop working.
- A deny binds every session that loads its file, whatever the model, and no later layer
  lifts it; hence a separate file. `--settings` takes one file. Denies apply from the next
  session.

**3. Session reminder**, the plugin's only hook: on `SessionStart` (`startup|clear|compact`),
[`orchestrator_reminder.py`](scripts/session-start/orchestrator_reminder.py) injects

> This is an orchestrator session: legwork is decomposed into bounded tasks and delegated through the pier skill; decisions, specs and verdicts stay here. Legwork means codebase sweeps, web lookups, multi-file edits, long runs, or any step needing over three exploratory commands. Bash here is for checks: git status/diff, one test run, `sed -n` on a cited range.

It is phrased as fact because hook text framed as commands can trip Claude's
prompt-injection defenses. There is no `PreToolUse` gate: a hook denial never shrinks
context, costs an extra request, and another command routes around it. This steers a
model; it is not a sandbox (`curl` still does what `WebFetch` did), and `--bare`,
`--safe-mode` and `disableAllHooks` skip the reminder.

## Development

```bash
python3 -m unittest discover -s tests -v
PIER_TEST_REAL_PI=$(command -v pi) python3 -m unittest discover -s tests
```

Tests use [`mock/mock_pi`](mock/mock_pi) and never call a model. The optional real-Pi
test points Pi at an empty config directory, so it only checks that Pi accepts pier's
arguments and fails on the missing API key. Contributor notes: [AGENTS.md](AGENTS.md).

## License

[MIT](LICENSE)
