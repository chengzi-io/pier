# ADR-0003 — Agent session lifecycle states

- **Date**: 2026-06-18
- **Status**: accepted
- **Related**: ../designs/pier-agent-format.md, ../designs/pier-mcp-interface.md

## Context

Each dispatched agent session in Pier goes through a series of observable states. The dashboard and MCP clients need a consistent status model to display progress, detect terminal states, and decide next actions (retry, abort, follow up, steer, etc.).

MCP and dashboard clients need the same lifecycle vocabulary. The state machine must be simple enough for clients to reason about, yet cover all common termination causes.

## Decision

A flat six-state model with no nesting or substates. All states are single-word strings.

```
                  ┌──────────┐
                  │ starting │
                  └────┬─────┘
                       │
                  ┌──────────┐
                  │ running  │
                  └────┬─────┘
                       │
          ┌────────────┼────────────┐
          │            │            │
          ▼            ▼            ▼
     completed       error       aborted    stopped
          │
          └────────────── follow_up ───────▶ running
```

| State | Type | Meaning |
|-------|------|---------|
| `starting` | Active | Pi subprocess is starting, RPC initialization is running, or first prompt is being accepted |
| `running` | Active | Pi subprocess is started, RPC connected, prompt being processed |
| `completed` | Idle | Latest turn completed successfully; session can accept `follow_up` |
| `error` | Terminal | Subprocess crashed, RPC protocol violation, or unexpected exit code |
| `aborted` | Terminal | Abort requested by client or timeout reached |
| `stopped` | Terminal | User manually stopped the session via dashboard UI or signal |

### Transitions

- **→ `starting`**: Pier validates input and starts the Pi subprocess.
- **`starting` → `running`**: RPC handshake succeeds, `get_state` returns `sessionId`, and first prompt is accepted.
- **`running` → `completed`**: Pi finishes the current turn and the session becomes reusable.
- **`completed` → `running`**: MCP client sends `follow_up` and Pi accepts the command.
- **`running` → `error`**: Subprocess exits with non‑zero code, JSONL parse failure, or unexpected stream end.
- **`running` → `aborted`**: MCP client calls `agent.abort`, a request cancellation maps unambiguously to an active session, or internal timeout fires.
- **`running` → `stopped`**: Dashboard stop button clicked, SIGTERM sent to subprocess.

## Consequences

- **+** Clients get clear semantics for startup, active work, reusable completion, and terminal failure/stop states.
- **+** Dashboard can render distinct icons/colours per visible state (green check, red X, yellow warning, grey stop).
- **+** Simple to implement: a single `status` field on the session object, updated by event handlers.
- **-** `stopped` and `aborted` are semantically close; clients may conflate them. We keep both because the initiating actor differs (user via UI vs client via protocol).
- **-** No "cancelling" transient state — the transition to `aborted`/`stopped` is instantaneous in the model; the actual subprocess kill may lag.

## Altarnatives

- **Two‑state (running / done)** — too lossy; clients can't distinguish success from failure.
- **Three‑state with `failed` only** — loses distinction between abort vs error vs user stop.
- **State machine with `pending`, `queued`, `compacting`, etc.** — unnecessary for Pier's public MCP contract; Pi internals are opaque to Pier.

## Follow-ups

- Implement `status` field on session object, updated at each transition.
- Expose status through MCP `agent.status` and advisory lifecycle/progress notifications.
- Add dashboard badge component for each state.
