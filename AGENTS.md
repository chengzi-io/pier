# AGENTS.md

pier = a skill + stdlib-only Python 3.9 CLI that turns agent Markdown files into `pi`
argv, so a host agent delegates work to Pi on any model. The caller is an AI: every
byte pier prints costs its tokens.

## Layout

- `SKILL.md`: what the host agent reads. `README.md`: for humans.
  `references/agent-authoring.md`: frontmatter reference, read only when writing agents.
- `scripts/pier`: CLI entry (argparse, commands). `pier_agents.py`: frontmatter, lookup,
  validate. `pi_runtime.py`: RunSpec -> Pi flags, running Pi, output budget.
  `pi_state.py`: `$PIER_HOME`, sidecars, locks, worktrees. `toon.py`: TOON encoder.
- `agents/`: bundled examples. `mock/mock_pi`: fake Pi for tests.

## Invariants

- stdout = the reply only. stderr: `pier: <ref> started (<agent>)` first, then the
  summary with tokens and cost; on failure a final `pier: error: …` and non-zero exit.
- Behaviour lives in the agent file; the CLI adds only `--agent-file`, `--model`,
  `--worktree`, `--dry-run`. Sessions are addressed by ref only. Keep it that way:
  a new flag needs a reason the agent file cannot cover.
- Pi does not persist system prompt/tools/skills/extensions: the sidecar stores the
  flags and `send` replays them.
- State only under `$PIER_HOME`; never write into the project or Pi's config.
- Unreleased: change contracts freely, no compat shims.

## Changing things

- A CLI or frontmatter change updates `SKILL.md`, `README.md`,
  `references/agent-authoring.md` and tests in the same change.
- SKILL.md is loaded on every use: add a line only if it changes what the caller
  types or prevents a retry.
- Test: `python3 -m unittest discover -s tests` (mock Pi, no model calls);
  `PIER_TEST_REAL_PI=$(command -v pi)` adds a real-Pi argv check.
