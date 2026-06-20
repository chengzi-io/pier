import { describe, expect, it, vi } from 'vitest';
import { createApp } from './app.js';
import type { PierConfig } from './config.js';
import * as mcpServerModule from './mcp/server.js';

const config: PierConfig = {
	agents: [
		{
			id: 'review',
			name: 'review',
			description: 'Review code changes',
			source: {
				path: '/tmp/review.md',
				scope: 'project',
			},
			prompt: 'Review the repository.',
			tools: { mode: 'inherit' },
			extensions: { mode: 'inherit' },
			skills: [],
			inheritSkills: true,
			inheritProjectContext: true,
			systemPromptMode: 'replace',
			autoCompaction: true,
			autoRetry: true,
		},
	],
};

describe('createApp', () => {
	it('serves REST session endpoints and rejects non-initialize MCP requests without a transport session', async () => {
		const app = createApp(config, {
			sessionManager: {
				loadGlobalAgents: () => config.agents,
			},
		});

		const agentsResponse = await app.request('/api/agents');
		expect(agentsResponse.status).toBe(200);
		expect(await agentsResponse.json()).toEqual({
			cwd: null,
			items: [
				{
					id: 'review',
					description: 'Review code changes',
					source: {
						path: '/tmp/review.md',
						scope: 'project',
					},
					prompt: 'Review the repository.',
					tools: { mode: 'inherit' },
					extensions: { mode: 'inherit' },
					skills: [],
					inheritSkills: true,
					inheritProjectContext: true,
					systemPromptMode: 'replace',
					autoCompaction: true,
					autoRetry: true,
				},
			],
		});

		const sessionsResponse = await app.request('/api/sessions');
		expect(sessionsResponse.status).toBe(200);
		expect(await sessionsResponse.json()).toEqual({ sessions: [] });

		const badMcpResponse = await app.request('/mcp', {
			method: 'POST',
			headers: {
				Accept: 'application/json, text/event-stream',
				'Content-Type': 'application/json',
			},
			body: JSON.stringify({
				jsonrpc: '2.0',
				id: 'tools',
				method: 'tools/list',
				params: {},
			}),
		});

		expect(badMcpResponse.status).toBe(400);
		expect(await badMcpResponse.json()).toMatchObject({
			error: {
				message: 'Bad Request: No valid session ID provided',
			},
		});
	});

	it('accepts cwd on REST agent listing and echoes the requested project scope', async () => {
		const app = createApp(config, {
			sessionManager: {
				loadGlobalAgents: () => config.agents,
			},
		});

		const response = await app.request('/api/agents?cwd=/workspace/project');

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({
			cwd: '/workspace/project',
			items: [
				{
					id: 'review',
					description: 'Review code changes',
					source: {
						path: '/tmp/review.md',
						scope: 'project',
					},
					prompt: 'Review the repository.',
					tools: { mode: 'inherit' },
					extensions: { mode: 'inherit' },
					skills: [],
					inheritSkills: true,
					inheritProjectContext: true,
					systemPromptMode: 'replace',
					autoCompaction: true,
					autoRetry: true,
				},
			],
		});
	});

	it('initializes MCP over /mcp and exposes stable tools without progress notifications', async () => {
		const app = createApp(config, {
			sessionManager: {
				loadGlobalAgents: () => config.agents,
			},
		});

		const initializeResponse = await app.request('/mcp', {
			method: 'POST',
			headers: {
				Accept: 'application/json, text/event-stream',
				'Content-Type': 'application/json',
			},
			body: JSON.stringify({
				jsonrpc: '2.0',
				id: 'init',
				method: 'initialize',
				params: {
					protocolVersion: '2025-03-26',
					capabilities: {},
					clientInfo: {
						name: 'vitest',
						version: '1.0.0',
					},
				},
			}),
		});

		expect(initializeResponse.status).toBe(200);
		expect(initializeResponse.headers.get('content-type')).toContain(
			'text/event-stream',
		);
		const sessionId = initializeResponse.headers.get('mcp-session-id');
		expect(sessionId).toBeTruthy();

		const initBody = await initializeResponse.text();
		expect(initBody).toContain('"protocolVersion":"2025-03-26"');
		expect(initBody).toContain('"name":"pier"');
		expect(initBody).not.toContain('notifications/progress');

		const toolsResponse = await app.request('/mcp', {
			method: 'POST',
			headers: {
				Accept: 'application/json, text/event-stream',
				'Content-Type': 'application/json',
				'Mcp-Session-Id': sessionId ?? '',
				'Mcp-Protocol-Version': '2025-03-26',
			},
			body: JSON.stringify({
				jsonrpc: '2.0',
				id: 'tools',
				method: 'tools/list',
				params: {},
			}),
		});

		expect(toolsResponse.status).toBe(200);
		const toolsBody = await toolsResponse.text();
		expect(toolsBody).toContain('"name":"list_agents"');
		expect(toolsBody).toContain('"name":"list_models"');
		expect(toolsBody).toContain('"name":"agent"');
		expect(toolsBody).not.toContain('notifications/progress');
	});

	it('list_agents accepts cwd-scoped discovery arguments', async () => {
		const app = createApp(config, {
			sessionManager: {
				loadGlobalAgents: () => config.agents,
			},
		});

		const initializeResponse = await app.request('/mcp', {
			method: 'POST',
			headers: {
				Accept: 'application/json, text/event-stream',
				'Content-Type': 'application/json',
			},
			body: JSON.stringify({
				jsonrpc: '2.0',
				id: 'init',
				method: 'initialize',
				params: {
					protocolVersion: '2025-03-26',
					capabilities: {},
					clientInfo: {
						name: 'vitest',
						version: '1.0.0',
					},
				},
			}),
		});

		const sessionId = initializeResponse.headers.get('mcp-session-id');
		expect(sessionId).toBeTruthy();

		await app.request('/mcp', {
			method: 'POST',
			headers: {
				Accept: 'application/json, text/event-stream',
				'Content-Type': 'application/json',
				'Mcp-Session-Id': sessionId ?? '',
				'Mcp-Protocol-Version': '2025-03-26',
			},
			body: JSON.stringify({
				jsonrpc: '2.0',
				method: 'notifications/initialized',
				params: {},
			}),
		});

		const agentsResponse = await app.request('/mcp', {
			method: 'POST',
			headers: {
				Accept: 'application/json, text/event-stream',
				'Content-Type': 'application/json',
				'Mcp-Session-Id': sessionId ?? '',
				'Mcp-Protocol-Version': '2025-03-26',
			},
			body: JSON.stringify({
				jsonrpc: '2.0',
				id: 'agents',
				method: 'tools/call',
				params: {
					name: 'list_agents',
					arguments: {
						cwd: '/workspace/project',
					},
				},
			}),
		});

		expect(agentsResponse.status).toBe(200);
		const body = await agentsResponse.text();
		expect(body).toContain('"agents"');
		expect(body).not.toContain('notifications/progress');
	});

	it('only forwards terminal session notifications to MCP clients', async () => {
		const notifySpy = vi
			.spyOn(mcpServerModule, 'sendSessionStatusChangedNotification')
			.mockResolvedValue(undefined);
		const app = createApp(config, {
			sessionManager: {
				loadGlobalAgents: () => config.agents,
			},
		});

		const initializeResponse = await app.request('/mcp', {
			method: 'POST',
			headers: {
				Accept: 'application/json, text/event-stream',
				'Content-Type': 'application/json',
			},
			body: JSON.stringify({
				jsonrpc: '2.0',
				id: 'init',
				method: 'initialize',
				params: {
					protocolVersion: '2025-03-26',
					capabilities: {},
					clientInfo: {
						name: 'vitest',
						version: '1.0.0',
					},
				},
			}),
		});

		const sessionId = initializeResponse.headers.get('mcp-session-id');
		expect(sessionId).toBeTruthy();

		await app.request('/mcp', {
			method: 'POST',
			headers: {
				Accept: 'application/json, text/event-stream',
				'Content-Type': 'application/json',
				'Mcp-Session-Id': sessionId ?? '',
				'Mcp-Protocol-Version': '2025-03-26',
			},
			body: JSON.stringify({
				jsonrpc: '2.0',
				method: 'notifications/initialized',
				params: {},
			}),
		});

		expect(notifySpy).not.toHaveBeenCalled();
		notifySpy.mockRestore();
	});
});
