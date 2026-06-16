# pier

Your agent fleet, one pier.

MCP server that reads Pi Agent definitions and exposes them as trackable agents over Streamable HTTP, with a Preact dashboard for runtime visibility.

## Requirements

- Node.js 20+
- [pnpm](https://pnpm.io/) 9+

## Setup

```bash
pnpm install
```

Optional agent config (JSON):

```bash
export PIER_AGENTS_JSON='{"agents":[{"id":"default","name":"Default"}]}'
```

## Scripts

| Command | Description |
|---------|-------------|
| `pnpm dev` | Server + web in parallel |
| `pnpm dev:server` | Hono API + MCP on port 8787 |
| `pnpm dev:web` | Vite dashboard |
| `pnpm build` | Build all packages |
| `pnpm lint` | Biome check |
| `pnpm typecheck` | TypeScript across workspace |
| `pnpm test` | Vitest in each package |

## Layout

- `server/` — Hono, MCP Streamable HTTP, Pi integration (in progress)
- `web/` — Preact dashboard (see [DESIGN.md](./DESIGN.md) before UI work)
- `docs/decisions/` — ADRs

See [AGENTS.md](./AGENTS.md) for agent and stack conventions.