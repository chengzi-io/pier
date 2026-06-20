# ADR-0006 — Abort state machine fallbacks

- **Date**: 2026-06-20
- **Status**: accepted
- **Related**: ADR-0003, ../designs/pier-mcp-interface.md

## Context

ADR-0003 specifies that a session moves `running → aborting → aborted`, where the `aborted` transition is driven by Pi emitting `agent_end`. In practice, Pi in RPC mode does **not** emit `agent_end` when a run is aborted: the subprocess simply exits with the in-flight assistant message marked `stopReason: "aborted"`. Without an `agent_end` signal, the session was stuck in `aborting` forever, blocking any subsequent `send` on that `sessionId` because `resume` rejects non-idle sessions.

A second, related race was observed: the original `abort()` implementation called `await send(abort)` before updating local state to `aborting`. During that await window, an in-flight `agent_end` could arrive and the event handler would see status=`running`, mark the session `completed`, and then `abort()` would overwrite it back to `aborting` — same stuck-state outcome.

Finally, clients may invoke `abort` on a session that has already reached a terminal state (network retry, lost notification, late cancel). The previous code happily wrote `aborting` over `completed`/`error`/`aborted`/`stopped`, also producing a stuck `aborting` that no further event would resolve.

## Decision

Three coupled rules govern `abort`:

1. **Process exit is the fallback transition.** When the Pi subprocess exits and the session is not yet in a terminal state, the `onExit` handler picks the terminal status based on the *current* session status:
   - `aborting` → `aborted`
   - any other non-terminal status → `error`

2. **`abort()` writes state before sending the RPC.** The local status is set to `aborting` *before* `send({ type: 'abort' })`. This guarantees that any `agent_end` arriving during the await sees `aborting` and correctly transitions to `aborted`.

3. **`abort()` is idempotent on terminal sessions.** If the session is already in `completed` / `error` / `aborted` / `stopped`, `abort()` returns the current snapshot without writing anything. Clients receive the actual terminal status (e.g. `completed`) and can infer that their abort did not change the outcome.

## Consequences

- **+** Sessions always reach a terminal state, even when Pi exits without `agent_end` or when `agent_end` races with `abort`.
- **+** `abort()` is safe to retry; lost notifications or client retries do not corrupt session state.
- **+** The terminal status returned to the client truthfully reflects *how* the session ended (e.g. `completed` if it finished naturally before the abort landed).
- **-** Clients cannot distinguish "my abort caused the termination" from "the session terminated on its own and my abort was a no-op" using a dedicated flag. They must read the returned `status`: anything other than `aborted` means the abort did not drive the outcome. A future `accepted: boolean` field can be added if real demand appears.
- **neutral** Reliance on `child.once('exit')` (single-fire) is acceptable because we now write `aborting` *before* sending the RPC, so by the time exit fires, status is already `aborting` (or terminal) — `onExit` always sees the right state.

## Alternatives

- **Watchdog timer after `abort`.** Add a timeout (e.g. 3s) that forces `aborting → aborted`. Rejected: it hides genuine "Pi refuses to die" cases by giving up too early, and adds a timer per session that complicates shutdown.
- **Throw `ABORT_TARGET_TERMINATED` when abort hits a terminal session.** Rejected: turns harmless retries into errors clients must special-case, and loses the useful "here is the actual terminal status" information.

## Follow-ups

- Update `docs/designs/pier-mcp-interface.md` cancellation section to reference this ADR.
- Add an integration test that aborts a session after it has reached `completed`, asserting the call returns the existing terminal status unchanged.
