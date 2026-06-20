import type { AgentDefinition } from '../config.js';

export function listAgents(definitions: AgentDefinition[]): AgentDefinition[] {
	return definitions.map((definition) => ({ ...definition }));
}
