# ADR-0004 — Simplified agent format (supersedes ADR-0002)

- **Date**: 2026-06-18
- **Status**: accepted
- **Supersedes**: ADR-0002
- **Related**: ADR-0003, ../designs/pier-agent-format.md

## Context

ADR-0002 defined a source + overlay model with multi-format support (Claude Code, Codex, OpenCode, pier-native) and Docker isolation. During implementation discovery, several of those decisions proved unnecessary or misaligned with the actual use case.

## Changes from ADR-0002

### 1. External agent formats dropped

**ADR-0002**: Pier normalizes agent definitions from Claude Code, Codex, OpenCode, and pier-native formats into a common intermediate form.

**Now**: Pier supports only one format — `.md` files aligning with pi-subagent frontmatter conventions (nicobailon/pi-subagents). No external format adapters.

**Why**: Every format introduces translation complexity, field gaps, and maintenance burden. Pier is a Pi gateway; it speaks Pi's native agent format. Users who want to run Claude Code agents use Claude Code directly.

### 2. Overlay config dropped

**ADR-0002**: `~/.pier/agents.json` holds runtime policy (`enabled`, `isolation` override, `model` override, tool overrides) separate from source `.md` files.

**Now**: No overlay. The `.md` file is the single source of truth. All fields, including `enabled`, live in frontmatter.

**Why**: The overlay was introduced to support multi-format normalization — different source formats needed an intermediate policy layer. With a single format, that abstraction is unnecessary. Users edit `.md` directly to change behaviour.

### 3. Docker isolation dropped

**ADR-0002**: `isolation: docker` runs Pi in a Docker container with mounts for `~/.pi/agent/`, session dir, and cwd.

**Now**: No isolation field. All runs execute as host subprocesses.

**Why**: Containerised Pi adds image maintenance, mount complexity, and is not yet requested. Pure subprocess keeps the MVP lean. If isolation becomes necessary, a future ADR will reintroduce it as a pier server setting rather than an agent-level field.

### 4. Session directory layout

**ADR-0002**: Sessions under `~/.pier/sessions/` with auto-generated filenames.

**Now**: Sessions under `~/.pier/sessions/<project-path>/<timestamp>_<uuidv7>.jsonl`, where `<project-path>` is the cwd with `/` replaced by `-` and wrapped in `--` (e.g. `--Users-username-your-working-path--`).

**Why**: Grouping by project path prevents filename collisions across projects and makes session browsing manageable.

### 5. Hardcoded defaults refined

| Setting | ADR-0002 | Now |
|---------|----------|-----|
| `--name <agent-name>` | *(not mentioned)* | Forced — sets session display name from agent name |
| `--approve` | `--no-approve` | Forced — trust project `.pi/` resources by default |
| `PI_OFFLINE=1` | *(implied)* | Explicitly forced |

### 6. `enabled` field in frontmatter

**ADR-0002**: `enabled` lives in overlay config only.

**Now**: `enabled` is a frontmatter field (default `true`). Set to `false` to exclude an agent from MCP `list_agents` without deleting the file.

## Unchanged from ADR-0002

The core integration path remains the same:

- Pier spawns `pi --mode rpc` as a child process per run.
- Strict JSONL framing (LF-delimited; no Node `readline`).
- `--approve` hardcoded (trust project `.pi/` resources).
- `--no-prompt-templates` hardcoded.
- `PI_OFFLINE=1` hardcoded.
- Configuration reuse from `~/.pi/agent/` (models.json, mcp.json).

## Consequences

- **+** Dramatically simpler: no overlay parser, no format adapters, no Docker launcher.
- **+** Single frontmatter schema for users to learn; no "which field goes where" confusion.
- **+** ~300 lines of spec instead of a multi-layer abstraction.
- **-** Breaking change for anyone who already adopted the overlay — they must move overrides into `.md` frontmatter (unlikely before release).
- **-** No Docker isolation path for untrusted cwds; security relies on `PI_OFFLINE=1`.

## Follow-ups

- Update ADR-0002 status to `superseded`.
- Remove overlay and Docker isolation from implementation backlog.
- Implement session dir with project-path subdirectory.
