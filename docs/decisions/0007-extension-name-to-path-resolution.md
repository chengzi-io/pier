# ADR-0007 — Extension name → entry path resolution

- **Date**: 2026-06-20
- **Status**: accepted
- **Related**: ADR-0004, ../designs/pier-agent-format.md

## Context

ADR-0004 aligned Pier's agent format with the `pi-subagent` community spec, where the `extensions` frontmatter field is a list of extension *names* selected from what the Pi runtime has discovered (e.g. `pi-mcp-adapter`, `kooky`). Pier passes each entry to Pi via `-e <value>` after `--no-extensions`.

The Pi CLI, however, treats `-e / --extension` strictly as a **path** (`--extension, -e <path>`). Passing a bare name like `pi-mcp-adapter` makes Pi resolve it as a relative path against the agent's cwd, producing errors such as:

```
Failed to load extension "/private/tmp/test-pier/pi-mcp-adapter":
Extension path does not exist
```

So every Pier agent that uses `extensions: [name]` was effectively broken at runtime, contradicting both the pi-subagent spec and Pier's own documentation.

The Pi config dir is also relocatable via `PI_CODING_AGENT_DIR` (default `~/.pi/agent`). Any resolution scheme must honor that.

## Decision

Pier resolves each extension reference to an absolute entry path **before** spawning Pi. The resolution order, scoped to the effective Pi agent dir, is:

1. **Absolute path** — used verbatim if the file exists.
2. **Relative path** (starts with `./` or `../`) — resolved against the agent's `cwd`, must exist.
3. **npm package name** — `<piAgentDir>/npm/node_modules/<name>/` is consulted. If `package.json` declares `module`, `main`, or a string/dot-default `exports`, that entry is used. Otherwise `<pkgDir>/index.ts` is the fallback.
4. **Loose file** — `<piAgentDir>/extensions/<name>.ts`.
5. **Loose directory** — `<piAgentDir>/extensions/<name>/index.ts`.

`piAgentDir` resolves through `PI_CODING_AGENT_DIR` (honoring `~` / `~/` expansion) and falls back to `~/.pi/agent`.

If nothing matches, Pier throws `EXTENSION_NOT_FOUND` with a structured error listing the candidate paths it tried. The agent frontmatter `extensions: [...]` keeps the original names; the resolution is internal so `list_agents` continues to expose names to clients.

## Consequences

- **+** Agents using `extensions: [pi-mcp-adapter]`, `extensions: [kooky]`, or absolute paths all work without changes.
- **+** `PI_CODING_AGENT_DIR` overrides are honored, so non-default Pi installs (Nix/Guix, custom locations) keep working.
- **+** `list_agents` output is unchanged — clients still see names, not internal paths.
- **-** Pier must keep its resolution rules in sync with Pi's discovery rules. If Pi later adds new install layouts (e.g. pnpm-style), Pier needs a matching update.
- **-** Names that collide across npm package vs. `extensions/<name>.ts` always resolve to the npm package first; users wanting the loose file must use its path.
- **neutral** The community pi-subagent spec does not formally define name→path resolution; this ADR fills that gap for Pier.

## Alternatives

- **Pass names directly to Pi and let it fail.** Rejected: produces unhelpful path-not-found errors and contradicts the documented `extensions: [name]` contract.
- **Disable Pi's auto-discovery entirely and re-implement it in Pier.** Rejected: duplicates Pi's settings.json-driven discovery for no gain.
- **Resolve at agent file load time and store paths in the resolved agent definition.** Rejected: would leak internal paths through `list_agents` and require reloading agents when the Pi install location changes.

## Follow-ups

- Update `docs/designs/pier-agent-format.md` §5.1 to clarify that the table's `a`/`b` placeholders are names, and that Pier resolves them per this ADR.
- Add a fixture-based test covering `PI_CODING_AGENT_DIR` relocation.
