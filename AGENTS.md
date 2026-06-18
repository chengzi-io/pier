A MCP server that reads Pi Agent definitions and exposes them as trackable agents via Streamable HTTP — letting any MCP client dispatch tasks to configured agents with full visibility into their runtime status.

## Skills

- **mcp-builder** - Build the MCP server transport layer, tool definitions, and protocol handling
- **hono** - Build the HTTP layer (MCP endpoint + REST API + dashboard routing)
- **typescript-advanced-types** - Type-safe agent config parsing, Pi SDK wrapper typing
- **monorepo-management** - Manage pnpm workspace split (server/, web/)
- **frontend-design** - Preact dashboard UI and agent session visualization
- **clean-code** - Code style and maintainability during development

## Tech Stack

- TypeScript strict mode
- pnpm (workspace)
- vite
- Biome

### FE

- Preact
- Lucide
- zod

### BE

- Hono

### MCP - use Streamable HTTP

- @modelcontextprotocol/sdk 

### Pi

Use [Pi Agent](https://github.com/earendil-works/pi) [RPC mode](https://pi.dev/docs/latest/rpc)

prerequisites:
- `pnpm add -g --ignore-scripts @earendil-works/pi-coding-agent`(Priority)
-  or `npm i -g @earendil-works/pi-coding-agent`

### Git Hooks

- simple-git-hooks + lint-staged（pre-commit auto format + lint）

### Test

- vitest
- playwright

## Git

- Follow conventional commits
- Never push without user acpect
- Do not add  `Co-Authored-By`  to the commit message.
- worktrees store at `${PROJECT_ROOT}/.worktrees`

## ADRS

@ADRS.md

## FE Design Specification

Before you start developing the front-end, you MUST read and follow the [Design Specification](DESIGN.md)

## Store At Tmp Dir（）

- All tmp files store at `${PROJECT_ROOT}/.tmp`.
- NEVER commit
