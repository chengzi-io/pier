import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AgentDefinition } from '../config.js';
import type {
	CreatePiRpcClientOptions,
	PiRpcClient,
} from '../pi/rpc-client.js';
import type {
	PiRpcCommand,
	PiRpcEvent,
	PiRpcResponse,
} from '../pi/rpc-types.js';
import { PiSessionManager } from './session-manager.js';

const reviewAgent: AgentDefinition = {
	id: 'review',
	name: 'review',
	description: 'Review code changes',
	source: {
		path: '/tmp/review.md',
		scope: 'project',
	},
	prompt: 'Review the repository.',
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
};

describe('PiSessionManager', () => {
	let agentEndListener: ((event: PiRpcEvent) => void) | undefined;

	beforeEach(() => {
		agentEndListener = undefined;
	});

	it('returns success only after Pi exposes a real sessionId and accepts the first prompt', async () => {
		const responses: PiRpcResponse[] = [
			success('set_auto_compaction'),
			success('set_auto_retry'),
			success('get_state', {
				sessionId: 'session-123',
				sessionFile: '/tmp/session-123.jsonl',
			}),
			success('prompt'),
		];
		const sent: PiRpcCommand[] = [];
		const manager = new PiSessionManager([reviewAgent], {
			createRuntime: createRuntimeFactory({ responses, sent }),
			loadGlobalAgents: () => [reviewAgent],
		});

		const session = await manager.start({
			agentId: 'review',
			task: 'Review the latest diff',
			cwd: '/workspace/project',
		});

		expect(session).toMatchObject({
			sessionId: 'session-123',
			agentId: 'review',
			status: 'running',
			sessionFile: '/tmp/session-123.jsonl',
			model: 'agnes-ai/agnes-2.0-flash',
			thinking: 'off',
		});
		expect(sent.map((command) => command.type)).toEqual([
			'set_auto_compaction',
			'set_auto_retry',
			'get_state',
			'prompt',
		]);
		expect(sent[3]).toMatchObject({
			type: 'prompt',
			message: 'Review the latest diff',
		});
	});

	it('fails start when Pi does not return a sessionId', async () => {
		const sent: PiRpcCommand[] = [];
		const stop = vi.fn(async () => undefined);
		const manager = new PiSessionManager([reviewAgent], {
			createRuntime: createRuntimeFactory({
				responses: [
					success('set_auto_compaction'),
					success('set_auto_retry'),
					success('get_state', {}),
				],
				sent,
				stop,
			}),
			loadGlobalAgents: () => [reviewAgent],
		});

		await expect(
			manager.start({
				agentId: 'review',
				task: 'Review the latest diff',
				cwd: '/workspace/project',
			}),
		).rejects.toThrow('Pi did not return a sessionId');
		expect(stop).toHaveBeenCalled();
		expect(sent.map((command) => command.type)).toEqual([
			'set_auto_compaction',
			'set_auto_retry',
			'get_state',
		]);
	});

	it('resumes a completed session with follow_up by reopening the Pi session', async () => {
		const initialCommands: PiRpcCommand[] = [];
		const resumedCommands: PiRpcCommand[] = [];
		let runtimeCount = 0;
		const manager = new PiSessionManager([reviewAgent], {
			createRuntime: (options) => {
				agentEndListener = options.onEvent;
				runtimeCount += 1;
				if (runtimeCount === 1) {
					return createRuntime({
						options,
						responses: [
							success('set_auto_compaction'),
							success('set_auto_retry'),
							success('get_state', {
								sessionId: 'session-123',
								sessionFile: '/tmp/session-123.jsonl',
							}),
							success('prompt'),
						],
						sent: initialCommands,
					});
				}

				return createRuntime({
					options,
					responses: [
						success('set_auto_compaction'),
						success('set_auto_retry'),
						success('get_state', {
							sessionId: 'session-123',
							sessionFile: '/tmp/session-123.jsonl',
						}),
						success('prompt'),
					],
					sent: resumedCommands,
				});
			},
			loadGlobalAgents: () => [reviewAgent],
		});

		await manager.start({
			agentId: 'review',
			task: 'Initial review',
			cwd: '/workspace/project',
		});

		agentEndListener?.({ type: 'agent_end' });

		const resumed = await manager.followUp({
			sessionId: 'session-123',
			task: 'Check tests too',
		});

		expect(resumed).toMatchObject({
			sessionId: 'session-123',
			status: 'running',
			model: 'agnes-ai/agnes-2.0-flash',
			thinking: 'off',
		});
		expect(resumedCommands.map((command) => command.type)).toEqual([
			'set_auto_compaction',
			'set_auto_retry',
			'get_state',
			'prompt',
		]);
		expect(resumedCommands[3]).toMatchObject({
			type: 'prompt',
			message: 'Check tests too',
		});
	});

	it('marks a session aborting immediately and aborted after Pi ends the run', async () => {
		const sent: PiRpcCommand[] = [];
		const stop = vi.fn(async () => undefined);
		const manager = new PiSessionManager([reviewAgent], {
			createRuntime: (options) => {
				agentEndListener = options.onEvent;
				return createRuntime({
					options,
					responses: [
						success('set_auto_compaction'),
						success('set_auto_retry'),
						success('get_state', {
							sessionId: 'session-123',
							sessionFile: '/tmp/session-123.jsonl',
						}),
						success('prompt'),
						success('abort'),
					],
					sent,
					stop,
				});
			},
			loadGlobalAgents: () => [reviewAgent],
		});

		await manager.start({
			agentId: 'review',
			task: 'Initial review',
			cwd: '/workspace/project',
		});

		const aborting = await manager.abort('session-123');
		expect(aborting).toMatchObject({
			sessionId: 'session-123',
			status: 'aborting',
		});
		expect(stop).not.toHaveBeenCalled();

		agentEndListener?.({ type: 'agent_end' });

		expect(manager.get('session-123')).toMatchObject({
			sessionId: 'session-123',
			status: 'aborted',
		});
	});

	it('moves aborting to aborted when Pi exits without emitting agent_end', async () => {
		let exitListener: ((error: Error) => void) | undefined;
		const sent: PiRpcCommand[] = [];
		const stop = vi.fn(async () => undefined);
		const manager = new PiSessionManager([reviewAgent], {
			createRuntime: (options) => {
				exitListener = options.onExit;
				return createRuntime({
					options,
					responses: [
						success('set_auto_compaction'),
						success('set_auto_retry'),
						success('get_state', {
							sessionId: 'session-123',
							sessionFile: '/tmp/session-123.jsonl',
						}),
						success('prompt'),
						success('abort'),
					],
					sent,
					stop,
				});
			},
			loadGlobalAgents: () => [reviewAgent],
		});

		await manager.start({
			agentId: 'review',
			task: 'Initial review',
			cwd: '/workspace/project',
		});

		const aborting = await manager.abort('session-123');
		expect(aborting.status).toBe('aborting');

		exitListener?.(new Error('Pi process exited with code 0'));

		expect(manager.get('session-123')).toMatchObject({
			sessionId: 'session-123',
			status: 'aborted',
			lastAssistantTextPreview: 'assistant preview',
		});
	});
});

function createRuntimeFactory(input: {
	responses: PiRpcResponse[];
	sent: PiRpcCommand[];
	stop?: ReturnType<typeof vi.fn>;
}): (options: CreatePiRpcClientOptions) => PiRpcClient {
	return (options) =>
		createRuntime({
			options,
			responses: input.responses,
			sent: input.sent,
			stop: input.stop,
		});
}

function createRuntime(input: {
	options: CreatePiRpcClientOptions;
	responses: PiRpcResponse[];
	sent: PiRpcCommand[];
	stop?: ReturnType<typeof vi.fn>;
}): PiRpcClient {
	let index = 0;

	return {
		process: { pid: 4242 } as PiRpcClient['process'],
		get lastAssistantText() {
			return 'assistant preview';
		},
		send: vi.fn(async (command: PiRpcCommand) => {
			input.sent.push(command);
			const response = input.responses[index];
			index += 1;
			if (!response) {
				throw new Error(`No fake response for command ${command.type}`);
			}
			return response;
		}),
		stop: input.stop ?? vi.fn(async () => undefined),
	};
}

function success(
	command: string,
	data?: Record<string, unknown>,
): PiRpcResponse {
	return {
		type: 'response',
		command,
		success: true,
		data,
	};
}
