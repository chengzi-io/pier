import { z } from 'zod';

export const agentDefinitionSchema = z.object({
	id: z.string().min(1),
	name: z.string().min(1),
	description: z.string().optional(),
});

export const pierConfigSchema = z.object({
	agents: z.array(agentDefinitionSchema).default([]),
});

export type AgentDefinition = z.infer<typeof agentDefinitionSchema>;
export type PierConfig = z.infer<typeof pierConfigSchema>;

export function loadConfigFromEnv(): PierConfig {
	const raw = process.env.PIER_AGENTS_JSON;
	if (!raw) {
		return { agents: [] };
	}
	return pierConfigSchema.parse(JSON.parse(raw));
}
