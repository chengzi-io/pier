import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { describe, expect, it, vi } from 'vitest';
import type { AgentDefinition } from '../config.js';
import type {
	ModelSummary,
	SessionManager,
	SessionStatusChangedEvent,
	SessionSummary,
} from '../runtime/types.js';
import { createMcpServer } from './server.js';

const agent: AgentDefinition = {
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
};

const session: SessionSummary = {
	sessionId: 'session-123',
	agentId: 'review',
	status: 'running',
	updatedAt: '2026-06-18T00:00:00.000Z',
	sessionFile: '/tmp/session-123.jsonl',
	model: 'agnes-ai/agnes-2.0-flash',
	thinking: 'off',
};

describe('createMcpServer', () => {
	it('lists stable workflow tools and returns configured models', async () => {
		const listAgentsForCwd = vi.fn((cwd?: string) =>
			cwd
				? ([
						{
							id: 'project-review',
							name: 'project-review',
							description: 'Project agent',
							source: {
								path: '/tmp/project-review.md',
								scope: 'project',
							},
							prompt: 'Project prompt',
							model: 'agnes-ai/agnes-2.0-flash',
							thinking: 'off',
							tools: { mode: 'inherit' },
							extensions: { mode: 'inherit' },
							skills: [],
							inheritSkills: true,
							inheritProjectContext: true,
							systemPromptMode: 'replace',
							autoCompaction: true,
							autoRetry: true,
						},
					] satisfies AgentDefinition[])
				: [agent],
		);
		const manager = createSessionManagerStub({
			listModels: async () =>
				[
					{
						provider: 'agnes-ai',
						modelId: 'agnes-2.0-flash',
					},
				] satisfies ModelSummary[],
			listAgents: listAgentsForCwd,
		});
		const server = createMcpServer([agent], manager);
		const client = await connectTestClient(server);

		const tools = await client.listTools();
		const toolNames = tools.tools.map((tool: { name: string }) => tool.name);
		expect(toolNames).toEqual(['list_agents', 'list_models', 'agent']);
		const listAgentsTool = tools.tools.find(
			(tool: { name: string }) => tool.name === 'list_agents',
		);
		const listModelsTool = tools.tools.find(
			(tool: { name: string }) => tool.name === 'list_models',
		);
		expect(listAgentsTool?.outputSchema).toBeDefined();
		expect(listModelsTool?.outputSchema).toBeDefined();

		const modelsResult = await client.callTool({
			name: 'list_models',
			arguments: {},
		});

		expect(modelsResult.structuredContent).toEqual({
			models: [
				{
					provider: 'agnes-ai',
					modelId: 'agnes-2.0-flash',
				},
			],
		});

		const agentsResult = await client.callTool({
			name: 'list_agents',
			arguments: {
				cwd: '/workspace/project',
			},
		});
		expect(listAgentsForCwd).toHaveBeenCalledWith('/workspace/project');
		expect(agentsResult.structuredContent).toEqual({
			agents: [
				{
					id: 'project-review',
					description: 'Project agent',
					source: {
						path: '/tmp/project-review.md',
						scope: 'project',
					},
					model: 'agnes-ai/agnes-2.0-flash',
					thinking: 'off',
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

		await client.close();
		await server.close();
	});

	it('surfaces agent.send result and errors through the workflow tool', async () => {
		const start = vi.fn(async () => session);
		const server = createMcpServer(
			[agent],
			createSessionManagerStub({
				start,
				get: () => session,
			}),
		);
		const client = await connectTestClient(server);

		const ok = await client.callTool({
			name: 'agent',
			arguments: {
				action: 'send',
				agentId: 'review',
				task: 'Review this diff',
				cwd: '/workspace/project',
			},
		});
		expect(start).toHaveBeenCalledWith({
			agentId: 'review',
			task: 'Review this diff',
			cwd: '/workspace/project',
			model: undefined,
		});
		expect(ok.structuredContent).toMatchObject({
			sessionId: 'session-123',
			status: 'running',
			model: 'agnes-ai/agnes-2.0-flash',
			thinking: 'off',
		});

		const bad = await client.callTool({
			name: 'agent',
			arguments: {
				action: 'send',
				agentId: 'review',
			},
		});
		expect(bad.isError).toBe(true);
		expect(bad.structuredContent).toMatchObject({
			errorType: 'VALIDATION_FAILED',
		});
		const badContent = bad.content as Array<{ type: string; text?: string }>;
		expect(badContent[0]).toMatchObject({
			type: 'text',
			text: 'VALIDATION_FAILED: action=send requires task',
		});

		await client.close();
		await server.close();
	});
});

function createSessionManagerStub(
	overrides: Partial<SessionManager> = {},
): SessionManager {
	return {
		start: overrides.start ?? (async () => session),
		get: overrides.get ?? (() => session),
		list: overrides.list ?? (() => [session]),
		listModels: overrides.listModels ?? (() => []),
		listAgents: overrides.listAgents ?? (() => [agent]),
		abort: overrides.abort ?? (async () => session),
		followUp: overrides.followUp ?? (async () => session),
		steer: overrides.steer ?? (async () => session),
		subscribe:
			overrides.subscribe ??
			((_listener: (event: SessionStatusChangedEvent) => void) => () =>
				undefined),
	};
}

async function connectTestClient(server: ReturnType<typeof createMcpServer>) {
	const [clientTransport, serverTransport] =
		InMemoryTransport.createLinkedPair();
	const client = new Client({
		name: 'test-client',
		version: '1.0.0',
	});
	await Promise.all([
		server.connect(serverTransport),
		client.connect(clientTransport),
	]);
	return client;
}
