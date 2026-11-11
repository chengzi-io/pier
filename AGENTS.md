# AGENTS.md

pier = a skill + stdlib-only Python 3.9 CLI that turns agent Markdown files into `pi`
argv, so a host agent delegates work to Pi on any model. The caller is an AI: every
byte pier prints costs its tokens.

## Layout

- `skills/pier/` is the skill (and the plugin's only skill): `SKILL.md` is what the host
  agent reads, `references/agent-authoring.md` is the frontmatter reference (read only
  when writing agents). `README.md` is for humans.
- `skills/pier/scripts/pier`: CLI entry (argparse, commands). `pier_agents.py`: frontmatter,
  lookup, validate. `pi_runtime.py`: RunSpec -> Pi flags, running Pi, output budget.
  `pi_state.py`: `$PIER_HOME`, sidecars, locks, worktrees. `toon.py`: TOON encoder.
- `skills/pier/agents/`: bundled examples. `mock/mock_pi`: fake Pi for tests.
- `.claude-plugin/`: `plugin.json` + `marketplace.json`, so the repo installs as a Claude
  Code plugin from its own marketplace.

## Invariants

- stdout = the reply only. stderr: `pier: <ref> started (<agent>)` first, then
  `pier: <ref> ok|failed (<agent>)`; a failure adds a final `pier: error: …` and a
  non-zero exit (124 when the run deadline passed).
- Behaviour lives in the agent file; the CLI adds only `--agent-file`, `--model`,
  `--worktree`, `--dry-run`. Sessions are addressed by ref only. Keep it that way:
  a new flag needs a reason the agent file cannot cover.
- Pi does not persist system prompt/tools/skills/extensions (or the run deadline):
  the sidecar stores the flags and `send` replays them.
- Pi stays in pier's process group and is killed as a tree, on a deadline and on a
  signal alike. Every run holds a `$PIER_HOME/locks` flock slot (global + per caller
  session); a queued run says so once and keeps its deadline.
- State only under `$PIER_HOME`; never write into the project or Pi's config.
- Unreleased: change contracts freely, no compat shims.

## Changing things

- A CLI or frontmatter change updates `skills/pier/SKILL.md`, `README.md`,
  `skills/pier/references/agent-authoring.md` and tests in the same change.
- SKILL.md is loaded on every use: add a line only if it changes what the caller
  types or prevents a retry.
- Test: `python3 -m unittest discover -s tests` (mock Pi, no model calls);
  `PIER_TEST_REAL_PI=$(command -v pi)` adds a real-Pi argv check.
