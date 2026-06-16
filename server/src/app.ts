import { Hono } from 'hono';
import { cors } from 'hono/cors';
import { listAgents } from './agents/registry.js';
import type { PierConfig } from './config.js';
import { createMcpServer, createMcpTransport } from './mcp/server.js';

export function createApp(config: PierConfig) {
	const app = new Hono();

	app.use(
		'*',
		cors({
			origin: '*',
		}),
	);

	app.get('/health', (c) => c.json({ ok: true }));

	app.get('/api/agents', (c) => {
		return c.json({
			agents: config.agents,
			sessions: listAgents(config.agents),
		});
	});

	const mcpServer = createMcpServer(config.agents);
	const transport = createMcpTransport();
	let mcpReady: Promise<void> | null = null;

	app.all('/mcp', async (c) => {
		if (!mcpReady) {
			mcpReady = mcpServer.connect(transport);
		}
		await mcpReady;
		return transport.handleRequest(c.req.raw);
	});

	return app;
}
