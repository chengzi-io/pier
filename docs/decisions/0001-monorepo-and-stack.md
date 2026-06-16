# ADR-0001 — Monorepo layout and core stack

- **Date**: 2026-06-16
- **Status**: accepted
- **Related**: AGENTS.md

## Context

Pier must expose Pi agent definitions over MCP Streamable HTTP, serve a REST API for the dashboard, and ship a Preact UI. A single repository needs consistent tooling, shared TypeScript strictness, and independent deployable server and web packages.

## Decision

We adopt a pnpm workspace with `server/` and `web/`, TypeScript strict mode, Biome for format/lint, Vitest for unit tests, and simple-git-hooks with lint-staged on pre-commit.

## Consequences

- **+** One install and aligned versions across packages; clear boundary between MCP/backend and dashboard.
- **-** Contributors must use pnpm; Pi SDK versions may need adjustment as the upstream packages evolve.
- **neutral** Playwright E2E is reserved for a follow-up once dashboard flows exist.

## Alternatives

- npm workspaces without Biome — rejected for weaker unified lint/format story.
- Single package — rejected because server and web have different runtimes and build outputs.

## Follow-ups

- Wire Pi coding agent execution in `dispatch_task`.
- Add Playwright smoke tests for dashboard and MCP health.