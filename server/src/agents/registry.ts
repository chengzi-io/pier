import type { AgentDefinition } from '../config.js';

export type AgentRuntimeStatus = 'idle' | 'running' | 'error';

export interface AgentSession {
	agentId: string;
	status: AgentRuntimeStatus;
	updatedAt: string;
	lastMessage?: string;
}

const sessions = new Map<string, AgentSession>();

export function listAgents(definitions: AgentDefinition[]): AgentSession[] {
	return definitions.map((def) => {
		const existing = sessions.get(def.id);
		return (
			existing ?? {
				agentId: def.id,
				status: 'idle',
				updatedAt: new Date().toISOString(),
			}
		);
	});
}

export function upsertSession(session: AgentSession): void {
	sessions.set(session.agentId, session);
}
