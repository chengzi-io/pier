# ADR-0002 — Pi integration via RPC only

- **Date**: 2026-06-17
- **Status**: superseded
- **Superseded by**: ADR-0004
- **Related**: ADR-0001, AGENTS.md

## Context

Pier is an MCP gateway and agent registry that dispatches work to the Pi runtime. Pi is installed globally as the `pi` CLI (from `@earendil-works/pi-coding-agent`); pier does not bundle or depend on it as an npm package.

Pi intentionally ships without built-in sub-agents or MCP support (see Pi Design Principles). Pier fills exactly those two gaps: it reads external agent definitions (Claude Code, Codex, OpenCode, pier-native), normalizes them, exposes them to MCP clients, and dispatches runs to Pi via RPC.

Two integration paths were considered:

1. **In-process** via `@earendil-works/pi-ai` + `@earendil-works/pi-agent-core` — embed Pi's agent loop inside the pier server process.
2. **Subprocess** via the globally installed `pi` binary in RPC mode (`pi --mode rpc`) — JSONL protocol over stdin/stdout.

The subprocess path is chosen. The in-process path is rejected for MVP.

## Decision

Pier depends **only** on the globally installed `pi` CLI in RPC mode. It does not depend on `@earendil-works/pi-ai`, `@earendil-works/pi-agent-core`, or `@earendil-works/pi-coding-agent` as npm packages.

### Runtime integration

- Pier spawns `pi --mode rpc` as a child process per run.
- Communication is strict JSONL over stdin/stdout (LF-delimited; Node's `readline` is not protocol-compliant because it splits on U+2028/U+2029).
- Pier translates each agent definition into Pi CLI argv (see `pi-rpc-adapter.md`).
- Pi session files are stored under `~/.pier/sessions/` via `--session-dir`; Pi auto-generates filenames (`<timestamp>_<uuidv7>.jsonl`), and pier captures `sessionId` from the `get_state` RPC response for later resume.
- Resume uses `--session <sessionId>` (partial UUID); pier does not use `--resume`/`-r` (interactive picker) or `--fork` (branch from a prior user message, a pi-subagents concept).

### Configuration reuse

Pier reuses the user's existing Pi configuration rather than maintaining a parallel config tree:

- `~/.pi/agent/models.json` is the canonical provider/model configuration. Pier provides a web UI editor (form + JSON dual view) for it but does not duplicate the schema or store it separately.
- `~/.pi/agent/mcp.json` (read by the `pi-mcp-adapter` extension) is the canonical MCP server configuration. Pier provides a web UI editor for it but does not manage MCP servers per-run.
- Pier does not set `PI_CODING_AGENT_DIR`; Pi subprocesses read the user's global `~/.pi/agent/`.

### Agent source and overlay format

Pier uses a source + overlay model:

- Pier-native sources live in `~/.pier/agents/**/*.md` and adopt the pi-subagents frontmatter subset plus pier-specific fields (notably `isolation`). Pier-only exposure policy (`enabled`) lives in the overlay, not in source frontmatter.
- External sources (Claude Code, Codex, OpenCode, pi-subagents packages) are referenced read-only by path. Pier does not copy them into `~/.pier/agents/` and does not write back to them.
- Pier-owned runtime and exposure policy lives in an overlay config such as `~/.pier/agents.json`: `enabled`, `isolation` override, `model` override, `tools` override, and source path.
- A run resolves `source file + overlay + optional MCP start overrides` into a `ResolvedAgentDefinition`, then maps that to Pi CLI argv.

Supported resolved fields map directly to Pi CLI flags:

| Field | Pi CLI flag |
|---|---|
| `model` | `--model` |
| `tools` | `--tools` |
| `systemPromptMode` (`replace`/`append`) | `--system-prompt` / `--append-system-prompt` |
| `inheritProjectContext` | `--no-context-files` (when false) |
| `extensions` (default empty/list) | `--no-extensions` + `-e` list |
| `inheritSkills` + `skills` | `--no-skills` + `--skill` list |
| `thinking` | `--thinking` |

Pier-specific field: `isolation` (`host` / `docker`) controls how pier spawns the Pi process. It is a pier-specific frontmatter field in pier-native sources (required) and can be overridden by overlay; it is not part of the pi-subagents-compatible subset, so external sources do not carry it.

### Docker isolation

When `isolation: docker`, pier runs `docker run` with:

- The user's `~/.pi/agent` mounted read-write at `/root/.pi/agent` so Pi can reuse the user's models, auth, extensions, skills, settings, and trust state without read-only lockfile warnings.
- `~/.pier/sessions` mounted read-write at `/root/.pier/sessions` (so session files persist on the host).
- The run's `cwd` bind-mounted at `/workspace`.
- API keys passed via `-e` environment variables.

Pier uses the `Dockerfile.Pi` published in Pi's containerization docs; it does not publish its own image.

### Security posture

- `--no-approve` is hardcoded to prevent loading untrusted project-local resources (`.pi/`, `.agents/skills`) from the run's `cwd`. This is not about avoiding interactive prompts (RPC mode never prompts); it is about preventing prompt-injection via malicious project config. Read-write access to `~/.pi/agent` is acceptable because it is the user's own global Pi config and matches host-mode Pi behavior.
- `--no-prompt-templates` is hardcoded (pi-subagents has no prompt-template field; pier does not manage prompt templates).
- Pier does not inject its own MCP endpoint or token into Pi subprocesses, to prevent recursive calls.
- When `extensions` includes `pi-mcp-adapter`, pier scans the four `mcp.json` locations at startup and records a `pi.recursive_mcp_warning` event if a server named `pier` or pointing at pier's endpoint is found. This is advisory; pi-mcp-adapter has no server-level denylist.

## Consequences

- **+** Minimal npm dependency tree; pier's server package only needs Hono, MCP SDK, zod.
- **+** Pier-native source files remain portable with pi-subagents; pier-only runtime policy stays in overlay config.
- **+** Configuration reuse: users configure models and MCP servers once in `~/.pi/agent/`, both pier and manual `pi` use the same files.
- **+** Clean separation: pier is a registry + gateway + RPC adapter, not a second coding-agent runtime.
- **-** RPC path requires process management, JSONL framing, and a compatible global `pi` version on the host.
- **-** No in-process typed integration with `pi-agent-core`; pier cannot embed Pi's agent loop in-process. If that becomes necessary, a new ADR will revisit this decision.
- **-** Docker isolation requires the user to build `pi-sandbox` from Pi's `Dockerfile.Pi`; pier does not publish an image.
- **-** MCP server-level control (enable/disable per run) is not possible via CLI; it requires editing `mcp.json`. Pier documents this as a limitation.

## Alternatives

- **In-process via `pi-ai` + `pi-agent-core`** — rejected for MVP. Pulls Pi's agent loop into pier's process, duplicates the global `pi` install, and pier would have to reimplement coding tools that the bundled CLI already provides. The RPC path gives pier the full Pi feature set without coupling.
- **Depend on `pi-coding-agent` as npm package for `AgentSession`** — rejected. The SDK path exists (Pi RPC docs mention `AgentSession` from `@earendil-works/pi-coding-agent`), but it pulls the entire CLI/TUI stack into pier's dependency tree. The globally installed `pi` binary already provides this via RPC.
- **Maintain a parallel pier config tree (`PI_CODING_AGENT_DIR=~/.pier/pi-config/`)** — rejected. Duplicates models.json, requires users to configure providers twice, and complicates docker mounts. Reusing the user's `~/.pi/agent/` is simpler and matches how users already work with Pi.

## Follow-ups

- Implement JSONL RPC client spawning `pi --mode rpc` in the run manager.
- Implement source adapters for pier-native files and Claude Code agent files.
- Implement pier overlay config for source references and runtime policy.
- Implement web UI editors for `~/.pi/agent/models.json` and `~/.pi/agent/mcp.json` (form + JSON dual view).
- Implement docker runtime launcher using `Dockerfile.Pi`.
- Map Pi RPC events to pier `RunEvent` stream (lifecycle events prefixed `pier.run.*`, Pi events passed through with `type: "pi"`).
