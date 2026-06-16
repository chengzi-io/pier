import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { WebStandardStreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js';
import { z } from 'zod';
import { listAgents, upsertSession } from '../agents/registry.js';
import type { AgentDefinition } from '../config.js';

export function createMcpServer(agents: AgentDefinition[]): McpServer {
	const server = new McpServer({
		name: 'pier',
		version: '0.0.1',
	});

	server.registerTool(
		'list_agents',
		{
			description: 'List configured Pi agents and their runtime status',
		},
		async () => {
			const rows = listAgents(agents);
			return {
				content: [
					{
						type: 'text',
						text: JSON.stringify(rows, null, 2),
					},
				],
			};
		},
	);

	server.registerTool(
		'dispatch_task',
		{
			description:
				'Dispatch a task to a configured agent (stub until Pi wiring is complete)',
			inputSchema: {
				agentId: z.string().describe('Target agent id'),
				task: z.string().describe('Task prompt'),
			},
		},
		async ({ agentId, task }) => {
			const known = agents.some((a) => a.id === agentId);
			if (!known) {
				return {
					content: [{ type: 'text', text: `Unknown agent: ${agentId}` }],
					isError: true,
				};
			}
			upsertSession({
				agentId,
				status: 'running',
				updatedAt: new Date().toISOString(),
				lastMessage: task.slice(0, 200),
			});
			return {
				content: [
					{
						type: 'text',
						text: JSON.stringify({ accepted: true, agentId, task }),
					},
				],
			};
		},
	);

	return server;
}

export function createMcpTransport(): WebStandardStreamableHTTPServerTransport {
	return new WebStandardStreamableHTTPServerTransport({
		sessionIdGenerator: () => crypto.randomUUID(),
	});
}
