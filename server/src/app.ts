import type { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { WebStandardStreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js';
import { isInitializeRequest } from '@modelcontextprotocol/sdk/types.js';
import { Hono } from 'hono';
import { cors } from 'hono/cors';
import type { PierConfig } from './config.js';
import {
	createMcpServer,
	sendSessionStatusChangedNotification,
} from './mcp/server.js';
import {
	PiSessionManager,
	type PiSessionManagerOptions,
} from './runtime/session-manager.js';
import type { SessionStatusChangedEvent } from './runtime/types.js';

export interface CreateAppOptions {
	sessionManager?: PiSessionManagerOptions;
}

export function createApp(config: PierConfig, options: CreateAppOptions = {}) {
	const app = new Hono();
	const sessionManager = new PiSessionManager(config.agents, {
		discoverCommunityAgents: false,
		...options.sessionManager,
	});

	app.use(
		'*',
		cors({
			origin: '*',
			allowHeaders: [
				'Content-Type',
				'Accept',
				'mcp-session-id',
				'mcp-protocol-version',
				'Last-Event-ID',
			],
			exposeHeaders: ['mcp-session-id', 'mcp-protocol-version'],
		}),
	);

	app.get('/health', (c) => c.json({ ok: true }));

	app.get('/api/agents', (c) => {
		const cwd = c.req.query('cwd');
		const items = sessionManager.listAgents(cwd);
		return c.json({
			cwd: cwd ?? null,
			items: items.map(({ name: _name, ...definition }) => definition),
		});
	});

	app.get('/api/sessions', (c) => {
		return c.json({ sessions: sessionManager.list() });
	});

	app.get('/api/sessions/:sessionId', (c) => {
		const session = sessionManager.get(c.req.param('sessionId'));
		if (!session) {
			return c.json({ error: 'Session not found' }, 404);
		}
		return c.json({ session });
	});

	const mcpTransports = new Map<
		string,
		{
			transport: WebStandardStreamableHTTPServerTransport;
			server: McpServer;
		}
	>();

	sessionManager.subscribe((event) => {
		if (!isTerminalEvent(event)) {
			return;
		}
		for (const entry of mcpTransports.values()) {
			void sendSessionStatusChangedNotification(entry.server, event).catch(
				() => undefined,
			);
		}
	});

	app.all('/mcp', async (c) => {
		const request = c.req.raw;
		const transportSessionId = request.headers.get('mcp-session-id');
		if (transportSessionId) {
			const entry = mcpTransports.get(transportSessionId);
			if (!entry) {
				return new Response(
					JSON.stringify({
						jsonrpc: '2.0',
						error: {
							code: -32000,
							message: 'Bad Request: No valid session ID provided',
						},
						id: null,
					}),
					{
						status: 400,
						headers: { 'Content-Type': 'application/json' },
					},
				);
			}
			return entry.transport.handleRequest(request);
		}

		if (request.method === 'GET') {
			return new Response('Invalid or missing session ID', { status: 400 });
		}

		const body = await request
			.clone()
			.json()
			.catch(() => null);
		if (!body || !isInitializeRequest(body)) {
			return new Response(
				JSON.stringify({
					jsonrpc: '2.0',
					error: {
						code: -32000,
						message: 'Bad Request: No valid session ID provided',
					},
					id: null,
				}),
				{
					status: 400,
					headers: { 'Content-Type': 'application/json' },
				},
			);
		}

		const liveTransport = new WebStandardStreamableHTTPServerTransport({
			sessionIdGenerator: () => crypto.randomUUID(),
			onsessioninitialized: (sessionId: string) => {
				mcpTransports.set(sessionId, {
					transport: liveTransport,
					server: liveServer,
				});
			},
			onsessionclosed: (sessionId: string) => {
				mcpTransports.delete(sessionId);
			},
		});
		const liveServer = createMcpServer(config.agents, sessionManager);
		await liveServer.connect(liveTransport);

		return liveTransport.handleRequest(request);
	});

	return app;
}

function isTerminalEvent(event: SessionStatusChangedEvent): boolean {
	return (
		event.status === 'completed' ||
		event.status === 'error' ||
		event.status === 'aborted' ||
		event.status === 'stopped'
	);
}
