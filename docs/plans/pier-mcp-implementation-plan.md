# Implementation Plan: Pier MCP Layer

## Overview

本计划定义 `Pier MCP` 层的实现拆解，用于把当前 demo 级 MCP server 收敛为可交付 MVP。目标是基于 `Hono + @modelcontextprotocol/sdk` 暴露稳定的 workflow tools，让 MCP client 可以发现 agent、启动 Pi session、查询 session 状态、控制运行中 session，并通过 `notifications/pier/session_status_changed` 获取 best-effort 生命周期通知。MVP 不实现 `notifications/progress`，最终结果的可靠来源始终是 `agent.status`。

## Architecture Decisions

- 使用 Streamable HTTP，MCP endpoint 固定为 `/mcp`，客户端 JSON-RPC 消息统一走 `POST /mcp`。
- MCP 层只暴露稳定工具：`list_agents`、`list_models`、`agent`，不做动态 tool-per-agent。
- `Mcp-Session-Id` 是 transport session；Pi `sessionId` 是业务 session，两者必须严格分离。
- `agent.start` 只有在 Pi 已返回真实 `sessionId` 且首个 `prompt` 被接受后才返回成功。
- 生命周期通知只实现 `notifications/pier/session_status_changed`；不实现 `notifications/progress`。
- `agent.status` 是长任务结果、断线恢复、终态确认的唯一可靠接口。
- 全局 agent 在 Pier 启动时加载；项目 agent 仅根据调用方提供的 `cwd` 动态发现。

## Dependency Graph

```text
MCP tool contract
    │
    ├── Session manager interface
    │       │
    │       ├── agent.start / status
    │       ├── follow_up / steer / abort
    │       └── lifecycle notification bridge
    │
    └── Hono /mcp wiring
            │
            └── REST session endpoints for dashboard
```

## Task List

### Phase 1: Foundation

## Task 1: Replace the Demo MCP Tool Surface

**Description:** Replace the current demo-oriented `dispatch_task` tool with the final MVP workflow tool surface. This task defines the public MCP contract first, so every later runtime integration targets a stable protocol shape.

**Acceptance criteria:**
- [ ] `tools/list` only exposes `list_agents`, `list_models`, and `agent`
- [ ] `agent` input schema supports `start`, `status`, `abort`, `follow_up`, and `steer`
- [ ] No tool schema contains `progressToken`, `runId`, or dynamic per-agent tool definitions
- [ ] `list_agents` accepts optional `cwd`; `agent.start` requires `cwd`

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "mcp|tool"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Inspect tool names and input schema through MCP Inspector

**Dependencies:** None

**Files likely touched:**
- `server/src/mcp/server.ts`

**Estimated scope:** Small: 1-2 files

## Task 2: Introduce MCP-Facing Session Contracts

**Description:** Add the MCP-facing session types and session manager interface so the MCP layer no longer mutates in-memory session state directly. This creates the seam between protocol handling and runtime orchestration.

**Acceptance criteria:**
- [ ] MCP layer depends on a session manager interface instead of direct registry mutation
- [ ] Shared session DTO includes `sessionId`, `agentId`, `status`, timestamps, and result/error summaries
- [ ] Session states align with ADR-0003: `starting`, `running`, `completed`, `error`, `aborted`, `stopped`

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "session|status"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: `server/src/mcp/server.ts` no longer stores state by `agentId`

**Dependencies:** Task 1

**Files likely touched:**
- `server/src/mcp/server.ts`
- `server/src/agents/registry.ts`
- `server/src/runtime/session-manager.ts`
- `server/src/runtime/session-store.ts`

**Estimated scope:** Medium: 3-5 files

### Checkpoint: Foundation

- [ ] MCP tool contract is stable and reviewable
- [ ] Session state model exists and is no longer demo-only
- [ ] `pnpm --filter @pier/server typecheck` passes
- [ ] Human review confirms no `notifications/progress` work is planned in MVP

### Phase 2: Core Features

## Task 3: Implement `agent.start` and `agent.status`

**Description:** Wire the MCP layer to the session manager for the two critical operations: starting a session and querying it. This is the minimum vertical slice that makes the server useful even before follow-up controls and notifications are complete.

**Acceptance criteria:**
- [ ] `agent.start` returns success only when a real Pi `sessionId` is available
- [ ] `agent.start` returns tool error immediately if Pi startup or first prompt acceptance fails
- [ ] `agent.status` returns structured state for known sessions and clear errors for unknown sessions

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "agent.start|agent.status"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Start a session, copy the returned `sessionId`, then query `agent.status`

**Dependencies:** Task 2

**Files likely touched:**
- `server/src/mcp/server.ts`
- `server/src/runtime/session-manager.ts`
- `server/src/runtime/session-store.ts`

**Estimated scope:** Medium: 3-5 files

## Task 4: Implement `follow_up`, `steer`, and `abort`

**Description:** Add the remaining control actions for existing sessions. This task completes the MCP-side control surface, including state precondition checks and error translation for invalid operations.

**Acceptance criteria:**
- [ ] `follow_up` works for reusable sessions and running sessions through the session manager
- [ ] `steer` is rejected for non-running sessions with a clear `session_not_running`-style error
- [ ] `abort` transitions running sessions toward `aborted` and returns structured status

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "follow_up|steer|abort"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Attempt `steer` on a completed session and verify the returned error is explicit

**Dependencies:** Task 3

**Files likely touched:**
- `server/src/mcp/server.ts`
- `server/src/runtime/session-manager.ts`

**Estimated scope:** Small: 1-2 files

## Task 5: Add Lifecycle Notification Bridging

**Description:** Implement a best-effort notification bridge that emits `notifications/pier/session_status_changed` when session state changes. This must remain advisory and must not become the only path to completion results.

**Acceptance criteria:**
- [ ] Session status changes publish `notifications/pier/session_status_changed`
- [ ] Notification payload includes `sessionId`, `agentId`, and `status`
- [ ] Notification delivery failure does not mutate or block session state changes

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "notification|session_status_changed"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Observe at least one lifecycle notification during a start-to-complete session

**Dependencies:** Task 3

**Files likely touched:**
- `server/src/mcp/server.ts`
- `server/src/runtime/session-manager.ts`
- `server/src/app.ts`

**Estimated scope:** Medium: 3-5 files

### Checkpoint: Core Features

- [ ] `agent.start`, `status`, `follow_up`, `steer`, and `abort` all exist
- [ ] Lifecycle notifications work as best-effort only
- [ ] No code path depends on `notifications/progress`
- [ ] Core MCP flow works end-to-end in Inspector

### Phase 3: Integration and Polish

## Task 6: Align REST Session Endpoints with MCP State

**Description:** Expose REST endpoints for dashboard and debugging use, backed by the same session manager used by MCP. This keeps the dashboard off of Pi RPC details and guarantees one source of truth for session state.

**Acceptance criteria:**
- [ ] `GET /api/agents` returns registry-backed agent data and accepts optional `cwd`
- [ ] `GET /api/sessions` returns current sessions from the session manager
- [ ] `GET /api/sessions/:sessionId` returns the same state shape as MCP status queries where practical

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "api|sessions"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Compare REST session data with `agent.status` for the same `sessionId`

**Dependencies:** Task 3

**Files likely touched:**
- `server/src/app.ts`
- `server/src/runtime/session-manager.ts`

**Estimated scope:** Small: 1-2 files

## Task 7: Add MCP-Layer Integration Tests

**Description:** Add focused integration tests for the MCP layer using a fake or stub session manager so tool behavior, validation, and error mapping can be verified without a real Pi process.

**Acceptance criteria:**
- [ ] Tests cover tool discovery and the five `agent.action` branches
- [ ] Tests assert that `agent.status` is the reliable result path
- [ ] Tests assert that no progress notification behavior is required in MVP

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test`
- [ ] Build succeeds: `pnpm --filter @pier/server build`
- [ ] Manual check: Review test names and ensure every tool action is represented

**Dependencies:** Tasks 1-6

**Files likely touched:**
- `server/src/mcp/server.ts`
- `server/src/mcp/server.test.ts`
- `server/src/app.test.ts`

**Estimated scope:** Medium: 3-5 files

## Task 8: Add Inspector CLI Smoke Coverage

**Description:** Add a second testing layer that launches the real Pier HTTP server and drives it through the official `@modelcontextprotocol/inspector --cli` client over Streamable HTTP. This covers the transport boundary, session headers, and real MCP request flow beyond in-process tests.

**Acceptance criteria:**
- [ ] Repository includes a reusable smoke command based on `@modelcontextprotocol/inspector --cli`
- [ ] Smoke command starts a real Pier server and hits `/mcp` over Streamable HTTP
- [ ] Smoke command verifies at least `tools/list`, `list_models`, `list_agents(cwd)`, `agent.start`, and `agent.status`
- [ ] Smoke command writes a human-readable report under `.tmp` and does not require committed test agents

**Verification:**
- [ ] `pnpm test:mcp:smoke` runs successfully in a machine with working Pi credentials/config
- [ ] Report contains both transport-level and business-level identifiers
- [ ] Human review confirms the smoke uses Inspector CLI rather than a custom ad hoc client only

**Dependencies:** Tasks 3-6

**Files likely touched:**
- `scripts/mcp-inspector-smoke.mjs`
- `package.json`
- `docs/plans/pier-mcp-implementation-plan.md`

**Estimated scope:** Small: 1-3 files

### Checkpoint: Complete

- [ ] All acceptance criteria met
- [ ] `pnpm --filter @pier/server test` passes
- [ ] `pnpm --filter @pier/server build` succeeds
- [ ] MCP Inspector happy path works
- [ ] Official Inspector CLI smoke path exists
- [ ] Ready for review

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| MCP layer accidentally reintroduces `notifications/progress` assumptions | High | Treat `agent.status` as the only reliable result path in tests and review |
| MCP transport session and Pi `sessionId` get conflated | High | Use separate types and explicit field names in DTOs and tool responses |
| Notification bridge becomes stateful business logic | Medium | Keep notifications event-driven and best-effort only; never derive final state from delivery success |
| REST endpoints drift from MCP session truth | Medium | Back both surfaces with the same session manager API |

## Resolved Decisions

- `GET /mcp` 在 MVP 内保留为 Streamable HTTP transport 的可选 SSE listen stream，继续由 SDK transport 处理；Pier 的业务语义仍以 `POST /mcp` JSON-RPC 和 `agent.status` 为准。
- `list_models` 在 MVP 直接读取 `~/.pi/agent/models.json` 的只读摘要，不通过运行中 Pi session 做 RPC 查询，也不暴露 provider secret。

## Verification

- [ ] Every task has acceptance criteria
- [ ] Every task has a verification step
- [ ] Task dependencies are identified and ordered correctly
- [ ] No task touches more than ~5 files
- [ ] Checkpoints exist between major phases
- [ ] The human has reviewed and approved the plan
