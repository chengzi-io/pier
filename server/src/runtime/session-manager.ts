import { mkdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join, resolve } from 'node:path';
import { listAgents } from '../agents/registry.js';
import {
	type AgentDefinition,
	loadGlobalAgents,
	loadProjectAgentsForCwd,
} from '../config.js';
import { normalizeModelAndThinking } from '../config/agent-resolver.js';
import { PierError } from '../errors.js';
import { buildPiRpcArgv } from '../pi/argv.js';
import { resolveExtensionPath } from '../pi/extensions.js';
import { listConfiguredPiModels } from '../pi/models.js';
import {
	type CreatePiRpcClientOptions,
	type PiRpcClient,
	createPiRpcClient,
} from '../pi/rpc-client.js';
import type {
	PiRpcCommand,
	PiRpcEvent,
	PiRpcResponse,
} from '../pi/rpc-types.js';
import { SessionStore } from './session-store.js';
import type {
	ContinueSessionInput,
	ModelSummary,
	SessionManager,
	SessionStatus,
	SessionStatusChangedEvent,
	SessionSummary,
	StartSessionInput,
} from './types.js';

interface SessionRuntime extends PiRpcClient {
	bindSessionId(sessionId: string): void;
}

type SessionRuntimeFactory = (options: CreatePiRpcClientOptions) => PiRpcClient;

export interface PiSessionManagerOptions {
	createRuntime?: SessionRuntimeFactory;
	loadGlobalAgents?: () => AgentDefinition[];
	piCommand?: string;
	piEnv?: NodeJS.ProcessEnv;
	userHome?: string;
	piAgentDir?: string;
	discoverCommunityAgents?: boolean;
}

export class PiSessionManager implements SessionManager {
	#sessionAgentsById = new Map<string, AgentDefinition>();
	#store = new SessionStore();
	#runtimes = new Map<string, SessionRuntime>();
	#createRuntime: SessionRuntimeFactory;
	#loadGlobalAgents: () => AgentDefinition[];
	#piCommand: string;
	#piEnv: NodeJS.ProcessEnv;
	#userHome?: string;
	#piAgentDir?: string;
	#discoverCommunityAgents: boolean;

	constructor(
		agents: AgentDefinition[],
		options: PiSessionManagerOptions = {},
	) {
		this.#createRuntime = options.createRuntime ?? createPiRpcClient;
		this.#loadGlobalAgents =
			options.loadGlobalAgents ??
			(() =>
				loadGlobalAgents({
					userHome: this.#userHome,
					piAgentDir: this.#piAgentDir,
					discoverCommunityAgents: this.#discoverCommunityAgents,
				}));
		this.#piCommand = options.piCommand ?? 'pi';
		this.#piEnv = {
			...process.env,
			PI_OFFLINE: '1',
			...options.piEnv,
		};
		this.#userHome = options.userHome;
		this.#piAgentDir = options.piAgentDir;
		this.#discoverCommunityAgents = options.discoverCommunityAgents ?? false;
	}

	subscribe(listener: (event: SessionStatusChangedEvent) => void): () => void {
		return this.#store.subscribe(listener);
	}

	list(): SessionSummary[] {
		return this.#store.list();
	}

	get(sessionId: string): SessionSummary | undefined {
		return this.#store.get(sessionId);
	}

	listModels(): ModelSummary[] {
		return listConfiguredPiModels();
	}

	listAgents(cwd?: string): AgentDefinition[] {
		return listAgents(this.#resolveAgentsForList(cwd));
	}

	async start(input: StartSessionInput): Promise<SessionSummary> {
		const agent = this.#requireAgentForStart(input.agentId, input.cwd);
		let boundSessionId: string | undefined;

		const runtime = this.#spawnRuntime({
			agent,
			cwd: input.cwd,
			model: input.model,
			onBindSessionId: (sessionId) => {
				boundSessionId = sessionId;
			},
		});

		try {
			const session = await this.#initializeSessionRuntime({
				runtime,
				agent,
				agentId: input.agentId,
				task: input.task,
				modelOverride: input.model,
			});
			boundSessionId = session.sessionId;
			this.#sessionAgentsById.set(session.sessionId, agent);
			return session;
		} catch (error) {
			await runtime.stop().catch(() => undefined);
			if (boundSessionId) {
				this.#runtimes.delete(boundSessionId);
				this.#sessionAgentsById.delete(boundSessionId);
			}
			throw normalizeError(error);
		}
	}

	async abort(sessionId: string): Promise<SessionSummary> {
		const current = this.#requireSession(sessionId);
		if (isTerminalStatus(current.status)) {
			return current;
		}
		const runtime = this.#runtimes.get(sessionId);
		if (!runtime) {
			throw createInactiveSessionError(sessionId);
		}

		this.#store.update(sessionId, {
			status: 'aborting',
			lastAssistantTextPreview: runtime.lastAssistantText,
			error: undefined,
		});
		await this.#sendAndEnsureSuccess(runtime, { type: 'abort' });
		return this.#store.get(sessionId) ?? current;
	}

	async followUp(input: ContinueSessionInput): Promise<SessionSummary> {
		const current = this.#requireSession(input.sessionId);
		const runtime = this.#runtimes.get(input.sessionId);

		if (runtime && current.status === 'running') {
			await this.#sendAndEnsureSuccess(runtime, {
				type: 'follow_up',
				message: input.task,
			});
			return current;
		}

		return await this.#resumeCompletedSession(current, input.task);
	}

	async steer(input: ContinueSessionInput): Promise<SessionSummary> {
		const runtime = this.#runtimes.get(input.sessionId);
		const current = this.#requireSession(input.sessionId);
		if (!runtime || current.status !== 'running') {
			throw createSessionNotRunningError(input.sessionId);
		}

		await this.#sendAndEnsureSuccess(runtime, {
			type: 'steer',
			message: input.task,
		});
		return (
			this.#store.update(input.sessionId, {
				status: 'running',
			}) ?? current
		);
	}

	async shutdown(): Promise<void> {
		await Promise.all(
			[...this.#runtimes.entries()].map(async ([sessionId, runtime]) => {
				this.#store.markTerminal(sessionId, 'stopped', {
					lastAssistantTextPreview: runtime.lastAssistantText,
				});
				await runtime.stop().catch(() => undefined);
			}),
		);
		this.#runtimes.clear();
		this.#sessionAgentsById.clear();
	}

	#requireAgentForStart(agentId: string, cwd: string): AgentDefinition {
		const agent = this.#resolveAgentForCwd(agentId, cwd);
		if (!agent) {
			throw createUnknownAgentError(agentId);
		}
		return agent;
	}

	#requireSessionAgent(sessionId: string, agentId: string): AgentDefinition {
		const agent =
			this.#sessionAgentsById.get(sessionId) ??
			this.#getGlobalAgentsById().get(agentId);
		if (!agent) {
			throw createUnknownAgentError(agentId);
		}
		return agent;
	}

	#requireSession(sessionId: string): SessionSummary {
		const current = this.#store.get(sessionId);
		if (!current) {
			throw createUnknownSessionError(sessionId);
		}
		return current;
	}

	#spawnRuntime(input: {
		agent: AgentDefinition;
		cwd?: string;
		model?: string;
		session?: string;
		onBindSessionId?: (sessionId: string) => void;
	}): SessionRuntime {
		let boundSessionId: string | undefined;

		const extensionPaths =
			input.agent.extensions.mode === 'allowlist'
				? input.agent.extensions.values.map((name) =>
						resolveExtensionPath(name, {
							piAgentDir: this.#requirePiAgentDir(),
							cwd: input.cwd,
						}),
					)
				: undefined;

		const runtimeClient = this.#createRuntime({
			command: this.#piCommand,
			args: [
				...buildPiRpcArgv(input.agent, {
					cwd: input.cwd,
					model: input.model,
					name: input.agent.name,
					session: input.session,
					sessionDir: this.#resolveSessionDir(input.cwd),
					extensionPaths,
				}),
				'--approve',
			],
			cwd: input.cwd,
			env: this.#piEnv,
			onEvent: (event) => {
				if (boundSessionId) {
					this.#handlePiEvent(boundSessionId, event);
				}
			},
			onExit: (error) => {
				if (!boundSessionId) {
					return;
				}
				input.onBindSessionId?.(boundSessionId);
				const current = this.#store.get(boundSessionId);
				if (!current || isTerminalStatus(current.status)) {
					this.#runtimes.delete(boundSessionId);
					return;
				}
				const terminalStatus: SessionStatus =
					current.status === 'aborting' ? 'aborted' : 'error';
				const patch =
					terminalStatus === 'aborted'
						? {
								lastAssistantTextPreview: runtimeClient.lastAssistantText,
								error: undefined,
							}
						: {
								error: error.message,
								lastAssistantTextPreview: runtimeClient.lastAssistantText,
							};
				this.#store.markTerminal(boundSessionId, terminalStatus, patch);
				this.#runtimes.delete(boundSessionId);
			},
		});

		return Object.assign(runtimeClient, {
			bindSessionId(sessionId: string) {
				boundSessionId = sessionId;
				input.onBindSessionId?.(sessionId);
			},
		});
	}

	async #initializeSessionRuntime(input: {
		runtime: SessionRuntime;
		agent: AgentDefinition;
		agentId: string;
		task: string;
		expectedSessionId?: string;
		modelOverride?: string;
	}): Promise<SessionSummary> {
		const effectiveModel = normalizeModelAndThinking(
			input.modelOverride ?? input.agent.model,
			input.agent.thinking,
			{ override: Boolean(input.modelOverride) },
		);
		await this.#sendAndEnsureSuccess(input.runtime, {
			type: 'set_auto_compaction',
			enabled: input.agent.autoCompaction,
		});
		await this.#sendAndEnsureSuccess(input.runtime, {
			type: 'set_auto_retry',
			enabled: input.agent.autoRetry,
		});

		const stateResponse = await input.runtime.send({ type: 'get_state' });
		if (!stateResponse.success) {
			throw new Error(stateResponse.error);
		}

		const sessionId = String(stateResponse.data?.sessionId ?? '');
		if (!sessionId) {
			throw new Error('Pi did not return a sessionId');
		}

		if (input.expectedSessionId && input.expectedSessionId !== sessionId) {
			throw new Error(
				`Resumed session mismatch: expected ${input.expectedSessionId}, got ${sessionId}`,
			);
		}

		input.runtime.bindSessionId(sessionId);
		this.#runtimes.set(sessionId, input.runtime);

		const existing = this.#store.get(sessionId);
		const persisted = existing
			? this.#store.update(sessionId, {
					status: 'starting',
					sessionFile:
						typeof stateResponse.data?.sessionFile === 'string'
							? stateResponse.data.sessionFile
							: existing.sessionFile,
					model: effectiveModel.model,
					thinking: effectiveModel.thinking,
					error: undefined,
					pid: input.runtime.process.pid,
				})
			: this.#store.create({
					sessionId,
					agentId: input.agentId,
					status: 'starting',
					updatedAt: new Date().toISOString(),
					sessionFile:
						typeof stateResponse.data?.sessionFile === 'string'
							? stateResponse.data.sessionFile
							: undefined,
					model: effectiveModel.model,
					thinking: effectiveModel.thinking,
					pid: input.runtime.process.pid,
				});

		if (!persisted) {
			throw new Error(`Failed to persist session ${sessionId}`);
		}

		const promptResponse = await input.runtime.send({
			type: 'prompt',
			message: input.task,
		});
		if (!promptResponse.success) {
			throw new Error(promptResponse.error);
		}

		const session = this.#store.update(sessionId, {
			status: 'running',
			error: undefined,
			pid: input.runtime.process.pid,
		});
		if (!session) {
			throw new Error(`Failed to persist session ${sessionId}`);
		}
		return session;
	}

	async #resumeCompletedSession(
		current: SessionSummary,
		task: string,
	): Promise<SessionSummary> {
		if (current.status === 'starting' || current.status === 'aborting') {
			throw createInactiveSessionError(current.sessionId);
		}

		const agent = this.#requireSessionAgent(current.sessionId, current.agentId);
		const resumeTarget = current.sessionFile ?? current.sessionId;
		const runtime = this.#spawnRuntime({
			agent,
			session: resumeTarget,
			onBindSessionId: () => undefined,
		});

		try {
			return await this.#initializeSessionRuntime({
				runtime,
				agent,
				agentId: current.agentId,
				task,
				expectedSessionId: current.sessionId,
			});
		} catch (error) {
			await runtime.stop().catch(() => undefined);
			this.#runtimes.delete(current.sessionId);
			throw normalizeError(error);
		}
	}

	#resolveAgentsForList(cwd?: string): AgentDefinition[] {
		const globalAgentsById = this.#getGlobalAgentsById();
		if (!cwd) {
			return [...globalAgentsById.values()].sort((left, right) =>
				left.id.localeCompare(right.id),
			);
		}

		const projectAgents = loadProjectAgentsForCwd({
			cwd,
			userHome: this.#userHome,
			piAgentDir: this.#piAgentDir,
			discoverCommunityAgents: this.#discoverCommunityAgents,
		});
		const merged = new Map(globalAgentsById);
		for (const agent of projectAgents) {
			merged.set(agent.id, agent);
		}
		return [...merged.values()].sort((left, right) =>
			left.id.localeCompare(right.id),
		);
	}

	#resolveAgentForCwd(
		agentId: string,
		cwd: string,
	): AgentDefinition | undefined {
		return this.#resolveAgentsForList(cwd).find(
			(agent) => agent.id === agentId,
		);
	}

	#getGlobalAgentsById(): Map<string, AgentDefinition> {
		return new Map(this.#loadGlobalAgents().map((agent) => [agent.id, agent]));
	}

	#handlePiEvent(sessionId: string, event: PiRpcEvent): void {
		if (event.type === 'agent_start') {
			return;
		}

		if (event.type === 'agent_end') {
			const runtime = this.#runtimes.get(sessionId);
			const current = this.#store.get(sessionId);
			this.#store.markTerminal(
				sessionId,
				current?.status === 'aborting' ? 'aborted' : 'completed',
				{
					lastAssistantTextPreview: runtime?.lastAssistantText,
					error: undefined,
				},
			);
			return;
		}

		if (event.type === 'message_end') {
			return;
		}

		if (event.type === 'extension_error') {
			this.#store.markTerminal(sessionId, 'error', {
				error:
					typeof event.error === 'string' ? event.error : 'Pi extension error',
			});
		}
	}

	async #sendAndEnsureSuccess(
		runtime: SessionRuntime,
		command: PiRpcCommand,
	): Promise<void> {
		const response = await runtime.send(command);
		if (!response.success) {
			throw new Error(response.error);
		}
	}

	#requirePiAgentDir(): string {
		if (this.#piAgentDir) {
			return this.#piAgentDir;
		}
		const configured = process.env.PI_CODING_AGENT_DIR?.trim();
		if (!configured) {
			return join(homedir(), '.pi', 'agent');
		}
		if (configured === '~') {
			return homedir();
		}
		if (configured.startsWith('~/')) {
			return join(homedir(), configured.slice(2));
		}
		return resolve(configured);
	}

	#resolveSessionDir(cwd?: string): string {
		const pierHome = process.env.PIER_HOME?.trim() || join(homedir(), '.pier');
		const sessionsRoot = join(pierHome, 'sessions');
		if (!cwd) {
			mkdirSync(sessionsRoot, { recursive: true });
			return sessionsRoot;
		}
		const absolute = isAbsolute(cwd) ? cwd : resolve(cwd);
		const encoded = `--${absolute.slice(1).replaceAll('/', '-')}--`;
		const projectSessionDir = join(sessionsRoot, encoded);
		mkdirSync(projectSessionDir, { recursive: true });
		return projectSessionDir;
	}
}

export function isTerminalStatus(status: SessionStatus): boolean {
	return (
		status === 'completed' ||
		status === 'error' ||
		status === 'aborted' ||
		status === 'stopped'
	);
}

function normalizeError(error: unknown): Error {
	return error instanceof Error ? error : new Error(String(error));
}

function createUnknownAgentError(agentId: string): PierError {
	return new PierError(
		'UNKNOWN_AGENT',
		`Agent "${agentId}" was not found in the resolved agent list. Call list_agents with the matching cwd to see available ids.`,
	);
}

function createUnknownSessionError(sessionId: string): PierError {
	return new PierError(
		'UNKNOWN_SESSION',
		`Session "${sessionId}" was not found. It may have been restarted, or the id is wrong.`,
	);
}

function createInactiveSessionError(sessionId: string): PierError {
	return new PierError(
		'SESSION_NOT_RESUMABLE',
		`Session ${sessionId} is not ready for a new turn yet (still starting or aborting). Wait for status to reach a terminal state, then retry.`,
	);
}

function createSessionNotRunningError(sessionId: string): PierError {
	return new PierError(
		'SESSION_NOT_RUNNING',
		`Session ${sessionId} is not running. action=steer only applies to a currently running session. Use action=send with the same sessionId to continue or resume it instead.`,
	);
}
