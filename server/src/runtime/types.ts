import type { AgentDefinition } from '../config.js';
import type { ThinkingLevel } from '../config/agent-schema.js';

export type SessionStatus =
	| 'starting'
	| 'running'
	| 'aborting'
	| 'completed'
	| 'error'
	| 'aborted'
	| 'stopped';

export interface SessionSummary extends Record<string, unknown> {
	sessionId: string;
	agentId: string;
	status: SessionStatus;
	updatedAt: string;
	sessionFile?: string;
	model?: string;
	thinking?: ThinkingLevel;
	lastAssistantTextPreview?: string;
	error?: string;
}

export interface SessionStatusChangedEvent extends Record<string, unknown> {
	sessionId: string;
	agentId: string;
	status: SessionStatus;
	updatedAt: string;
	sessionFile?: string;
	model?: string;
	thinking?: ThinkingLevel;
	lastAssistantTextPreview?: string;
	error?: string;
}

export interface StartSessionInput {
	agentId: string;
	task: string;
	cwd: string;
	model?: string;
}

export interface ContinueSessionInput {
	sessionId: string;
	task: string;
}

export interface ModelSummary extends Record<string, unknown> {
	provider?: string;
	modelId?: string;
	name?: string;
	displayName?: string;
}

export interface SessionManager {
	start(input: StartSessionInput): Promise<SessionSummary>;
	get(sessionId: string): SessionSummary | undefined;
	list(): SessionSummary[];
	listModels?(): Promise<ModelSummary[]> | ModelSummary[];
	listAgents?(cwd?: string): Promise<AgentDefinition[]> | AgentDefinition[];
	abort(sessionId: string): Promise<SessionSummary>;
	followUp(input: ContinueSessionInput): Promise<SessionSummary>;
	steer(input: ContinueSessionInput): Promise<SessionSummary>;
	subscribe(listener: (event: SessionStatusChangedEvent) => void): () => void;
}
