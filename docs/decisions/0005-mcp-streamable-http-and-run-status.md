# ADR-0005 — MCP Streamable HTTP and session status delivery

- **Date**: 2026-06-18
- **Status**: accepted
- **Related**: ADR-0003, ADR-0004, ../designs/pier-mcp-interface.md

## Context

Pier exposes Pi-backed agents through MCP. The initial MCP design mixed Streamable HTTP terminology with the deprecated HTTP+SSE dual-connection pattern, and it treated progress notifications as the final-result delivery channel.

That design risks poor compatibility with current MCP clients and can lose terminal results when SSE streams disconnect.

## Decision

Pier adopts MCP Streamable HTTP on a single `/mcp` endpoint. Client JSON-RPC messages use `POST /mcp`; server streaming for an active request happens on that request's Streamable HTTP response stream. Pier may also expose Streamable HTTP's optional `GET /mcp` server-to-client SSE listen stream for best-effort lifecycle notifications. This is not the deprecated HTTP+SSE dual-transport model.

Pier exposes stable workflow tools for agent discovery and session lifecycle management. `agent.start` returns a Pi `sessionId` only after Pier has successfully started Pi, completed RPC initialization, obtained `sessionId` through `get_state`, and received `success: true` for the first `prompt` command.

Follow-up and steering actions are part of MVP and are addressed by `sessionId`.

Progress notifications are advisory and only valid while the originating MCP request is active. A `progressToken` is not a session subscription token. MVP does not require Pier to emit progress notifications.

Pier also emits best-effort lifecycle notifications for terminal or user-visible status changes. Status queries remain the reliable source of truth.

## Consequences

- **+** Aligns with the current MCP transport specification and SDK support.
- **+** Avoids requiring clients to maintain a deprecated standalone SSE connection while still allowing the spec-defined optional `GET /mcp` listen stream.
- **+** Ensures successful `agent.start` responses always include a real, queryable Pi `sessionId`.
- **+** Makes final result delivery retryable after network disconnects or client restarts.
- **+** Keeps `tools/list` stable, improving compatibility with clients that cache tool definitions.
- **-** The "one agent equals one MCP tool" experience is deferred.
- **-** Clients must perform at least one additional status call for long-running results.
- **-** `agent.start` now waits for Pi startup, `get_state`, and prompt acceptance before returning.
- **-** Fire-and-forget lifecycle updates require best-effort lifecycle notifications plus polling, not progress tokens.
- **-** Clients should not expect percentage-like progress for agent execution in MVP.

## Alternatives

- **Deprecated HTTP+SSE dual transport** — rejected because current MCP Streamable HTTP replaces it and client support is inconsistent.
- **Final result only in `notifications/progress`** — rejected because progress notifications are tied to active requests and are not a reliable replay/query mechanism.
- **Dynamic MCP tool per agent** — deferred because `tools/list` caching makes runtime changes hard to observe without `notifications/tools/list_changed` and broad client validation.
- **MCP `tasks/` extension** — deferred because major client support is not yet consistent enough for MVP.
- **Separate Pier `runId` in MCP** — rejected because clients need the Pi session id for status, abort, resume, follow-up, steering, and session history.

## Follow-ups

- Update `docs/designs/pier-mcp-interface.md` to remove deprecated dual-connection semantics.
- Implement `agent.status` as the reliable terminal-result path keyed by `sessionId`.
- Implement `agent.follow_up` and `agent.steer` in MVP.
- If implemented later, keep progress notifications small, advisory, and scoped to active MCP requests.
- Emit small lifecycle notifications for completed/error/aborted/stopped state changes.
