import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { z } from 'zod';
import type { PiModelSummary } from './rpc-types.js';

const configuredModelSchema = z
	.object({
		id: z.string().min(1),
		name: z.string().optional(),
		displayName: z.string().optional(),
		reasoning: z.boolean().optional(),
		input: z.array(z.string()).optional(),
		contextWindow: z.number().optional(),
		maxTokens: z.number().optional(),
	})
	.passthrough();

const configuredProviderSchema = z
	.object({
		models: z.array(configuredModelSchema).default([]),
	})
	.passthrough();

const modelsConfigSchema = z
	.object({
		providers: z.record(z.string(), configuredProviderSchema).default({}),
	})
	.passthrough();

export function listConfiguredPiModels(
	options: {
		piAgentDir?: string;
		userHome?: string;
	} = {},
): PiModelSummary[] {
	const userHome = options.userHome ?? homedir();
	const piAgentDir =
		options.piAgentDir ??
		process.env.PI_CODING_AGENT_DIR ??
		join(userHome, '.pi/agent');
	const modelsPath = join(piAgentDir, 'models.json');

	if (!existsSync(modelsPath)) {
		return [];
	}

	const parsed = modelsConfigSchema.parse(
		JSON.parse(readFileSync(modelsPath, 'utf8')),
	);

	return Object.entries(parsed.providers)
		.flatMap(([providerName, provider]) =>
			provider.models.map((model) => ({
				provider: providerName,
				modelId: model.id,
				name: model.name,
				displayName: model.displayName,
				reasoning: model.reasoning,
				input: model.input,
				contextWindow: model.contextWindow,
				maxTokens: model.maxTokens,
			})),
		)
		.sort((left, right) => {
			const providerCompare = (left.provider ?? '').localeCompare(
				right.provider ?? '',
			);
			if (providerCompare !== 0) {
				return providerCompare;
			}
			return (left.modelId ?? '').localeCompare(right.modelId ?? '');
		});
}
