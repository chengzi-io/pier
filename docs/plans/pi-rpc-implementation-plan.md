# Implementation Plan: Pi RPC Integration Layer

## Overview

本计划定义 `Pi RPC` 集成层的实现拆解，用于把 Pier 从静态 demo registry 升级为可实际启动 `pi --mode rpc` 的 runtime。目标是完成 agent frontmatter 解析、Pi argv 构建、严格 JSONL RPC 客户端、session runtime 生命周期管理，以及 `start / follow_up / steer / abort` 的实际执行。Pi 没有官方 agent 文件规范，因此 `docs/designs/pier-agent-format.md` 必须被视为 Pier 自定义格式，参考社区流行约定并映射到 Pi CLI / RPC，而不是 Pi 官方 schema。

## Architecture Decisions

- 只集成外部 `pi` CLI：`pi --mode rpc`，不引入 Pi npm SDK 的 in-process agent loop。
- 每个运行中的 session 对应一个独立 Pi 子进程。
- RPC 通信使用严格 JSONL over stdin/stdout，按 LF 分帧，不使用 Node `readline`。
- `sessionId` 必须来自 `get_state` 响应，不能由 Pier 自行生成业务 ID。
- `agent.start` 成功的定义固定为：拿到 `sessionId` 且首个 `prompt` 返回 `success: true`。
- 只支持 Pier 自定义 `.md` agent 格式，不支持多格式 agent 适配、overlay config 或 Docker 隔离。

## Dependency Graph

```text
Agent file discovery and parsing
    │
    ├── Resolved agent runtime config
    │       │
    │       ├── Pi argv builder
    │       └── Pi RPC client
    │               │
    │               └── Session runtime / lifecycle manager
    │                       │
    │                       └── MCP layer and REST layer
```

## Task List

### Phase 1: Foundation

## Task 1: Implement Agent File Discovery and Parsing

**Description:** Build the file discovery and frontmatter parsing path for Pier agent definitions so runtime startup no longer depends on `PIER_AGENTS_JSON`. This creates the single source of truth for what sessions can be started.

**Acceptance criteria:**
- [ ] Agent discovery scans the supported directories and loads `.md` agent files
- [ ] Frontmatter and markdown body are parsed into a typed agent definition
- [ ] Disabled agents (`enabled: false`) are excluded from runnable output

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "config|agent"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Add a sample disabled agent file and verify it is not returned

**Dependencies:** None

**Files likely touched:**
- `server/src/config.ts`
- `server/src/config.test.ts`
- `server/src/config/agent-schema.ts`
- `server/src/config/agent-loader.ts`
- `server/src/config/agent-resolver.ts`

**Estimated scope:** Medium: 3-5 files

## Task 2: Implement Resolved Agent Runtime Mapping

**Description:** Normalize parsed agent definitions into a runtime-oriented shape that later tasks can use without repeating frontmatter interpretation logic. This task is where Pier-specific semantics are made explicit.

**Acceptance criteria:**
- [ ] Resolved agent config contains all runtime fields needed by Pi startup
- [ ] `skills`, `thinking`, and `systemPromptMode` semantics are normalized in one place
- [ ] Runtime mapping clearly distinguishes Pier policy from Pi official behavior

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "resolver|mapping"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Read resolved config output for one agent and verify no implicit defaults are hidden

**Dependencies:** Task 1

**Files likely touched:**
- `server/src/config/agent-resolver.ts`
- `server/src/config.test.ts`

**Estimated scope:** Small: 1-2 files

### Checkpoint: Foundation

- [ ] Agent definitions load from files instead of env JSON
- [ ] Resolved runtime config exists and is testable
- [ ] Human review confirms the plan does not treat Pier frontmatter as Pi official schema

### Phase 2: Core Runtime

## Task 3: Implement the Pi Argv Builder

**Description:** Map `ResolvedAgentDefinition + start overrides` to a safe, deterministic `pi --mode rpc` argv list. This task establishes the exact startup contract with Pi before process management and RPC wiring are added.

**Acceptance criteria:**
- [ ] Argv always includes `--mode rpc`
- [ ] Supported fields map to the intended Pi CLI flags without unchecked passthrough
- [ ] Empty, omitted, and default field semantics are explicit and covered by tests

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "argv|cli"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Inspect argv output for at least two different agent definitions

**Dependencies:** Task 2

**Files likely touched:**
- `server/src/pi/argv.ts`
- `server/src/config.test.ts`

**Estimated scope:** Small: 1-2 files

## Task 4: Implement the JSONL Pi RPC Client

**Description:** Build the child-process-backed RPC client that can send commands with IDs, correlate responses, and surface async events. This is the highest-risk foundation piece because protocol framing mistakes will break every higher layer.

**Acceptance criteria:**
- [ ] RPC client writes one JSON object per LF-delimited line
- [ ] RPC client can await a response by command `id`
- [ ] Protocol errors, stdout closure, and non-zero exit are surfaced as explicit failures

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "rpc|jsonl"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Review implementation and confirm Node `readline` is not used

**Dependencies:** Task 3

**Files likely touched:**
- `server/src/pi/jsonl-reader.ts`
- `server/src/pi/jsonl-writer.ts`
- `server/src/pi/rpc-types.ts`
- `server/src/pi/rpc-client.ts`

**Estimated scope:** Medium: 3-5 files

## Task 5: Implement Session Runtime Startup

**Description:** Wrap the Pi RPC client in a `PierSessionRuntime` that performs the startup handshake: spawn Pi, send initialization commands, call `get_state`, send the first prompt, and only then mark the session as started.

**Acceptance criteria:**
- [ ] Successful startup requires `get_state.sessionId` plus first `prompt success=true`
- [ ] Any startup failure cleans up the child process and returns an explicit error
- [ ] Runtime records `sessionId`, `sessionFile`, status, timestamps, and process metadata

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "startup|session runtime"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Review code path and confirm no fake `sessionId` is minted by Pier

**Dependencies:** Task 4

**Files likely touched:**
- `server/src/runtime/pi-session-runtime.ts`
- `server/src/runtime/session-store.ts`
- `server/src/pi/rpc-client.ts`

**Estimated scope:** Medium: 3-5 files

### Checkpoint: Core Runtime

- [ ] Pi argv generation is deterministic
- [ ] JSONL RPC client works against a fake Pi process
- [ ] Session startup returns a real `sessionId`
- [ ] High-risk protocol work is complete early

### Phase 3: Session Control and Recovery

## Task 6: Implement `follow_up`, `steer`, and `abort` Runtime Semantics

**Description:** Add the runtime operations for existing sessions so upper layers can control active Pi processes safely. This task must preserve Pi’s own semantics while enforcing Pier-side state preconditions.

**Acceptance criteria:**
- [ ] `follow_up` routes through Pi’s follow-up semantics for the target session
- [ ] `steer` is only allowed while the session is running
- [ ] `abort` first sends RPC `abort`, then escalates to process cleanup on timeout

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "follow_up|steer|abort"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Force an invalid `steer` and confirm the returned error is explicit

**Dependencies:** Task 5

**Files likely touched:**
- `server/src/runtime/pi-session-runtime.ts`
- `server/src/runtime/session-store.ts`

**Estimated scope:** Small: 1-2 files

## Task 7: Implement Event Aggregation and Status Reduction

**Description:** Reduce raw Pi events into the stable session status model that MCP and the dashboard consume. This keeps upper layers from depending on Pi event internals and creates a single source of truth for result previews and error summaries.

**Acceptance criteria:**
- [ ] Runtime reduces raw Pi activity into `starting`, `running`, `completed`, `error`, `aborted`, or `stopped`
- [ ] Session state stores a bounded `resultPreview` and an error summary where applicable
- [ ] Session status changes emit internal lifecycle events for upper layers

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "status|event"`
- [ ] Build succeeds: `pnpm --filter @pier/server typecheck`
- [ ] Manual check: Inspect a completed and failed session state object

**Dependencies:** Task 5

**Files likely touched:**
- `server/src/runtime/pi-session-runtime.ts`
- `server/src/runtime/session-store.ts`
- `server/src/runtime/session-events.ts`

**Estimated scope:** Medium: 3-5 files

## Task 8: Implement Process Cleanup and Shutdown Handling

**Description:** Finish the runtime with deterministic cleanup behavior so aborted, failed, and server-shutdown scenarios do not leak child processes or leave stale in-memory state.

**Acceptance criteria:**
- [ ] Runtime removes listeners and marks terminal state on normal completion
- [ ] Child processes are cleaned up on abort timeout and unexpected exit
- [ ] Server shutdown path can attempt cleanup of active sessions

**Verification:**
- [ ] Tests pass: `pnpm --filter @pier/server test -- --grep "cleanup|shutdown"`
- [ ] Build succeeds: `pnpm --filter @pier/server build`
- [ ] Manual check: Review code and confirm there is a single cleanup path for process exit

**Dependencies:** Tasks 6-7

**Files likely touched:**
- `server/src/runtime/pi-session-runtime.ts`
- `server/src/runtime/session-store.ts`
- `server/src/index.ts`

**Estimated scope:** Medium: 3-5 files

### Checkpoint: Complete

- [ ] All acceptance criteria met
- [ ] `pnpm --filter @pier/server test` passes
- [ ] `pnpm --filter @pier/server build` succeeds
- [ ] Fake Pi RPC tests cover startup and control flow
- [ ] Ready for review

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Pier frontmatter semantics drift into undocumented behavior | Medium | Keep normalization logic centralized and covered by tests |
| JSONL framing bugs break all RPC operations | High | Test against a fake Pi process and ban `readline` in review |
| `sessionId` is returned before Pi truly accepts the first prompt | High | Treat `prompt success=true` as part of the startup contract |
| Child processes leak after aborts or unexpected exits | High | Implement one cleanup path and test timeout escalation |

## Open Questions

- `skills` 名称解析是否需要在 MVP 就支持全部搜索路径，还是先支持项目级与用户级主路径？
- `list_models` 最终是直接读 Pi 配置文件，还是统一通过 Pi 适配层输出只读摘要？

## Verification

- [ ] Every task has acceptance criteria
- [ ] Every task has a verification step
- [ ] Task dependencies are identified and ordered correctly
- [ ] No task touches more than ~5 files
- [ ] Checkpoints exist between major phases
- [ ] The human has reviewed and approved the plan
