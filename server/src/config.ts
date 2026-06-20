import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { z } from 'zod';
import {
	type AgentDiscoveryOptions,
	discoverAgentFiles,
} from './config/agent-loader.js';
import {
	type ResolveAgentDefinitionsOptions,
	type ResolvedAgentDefinition,
	resolveAgentDefinitions,
	resolvedAgentDefinitionSchema,
} from './config/agent-resolver.js';

export const pierConfigSchema = z.object({
	agents: z.array(resolvedAgentDefinitionSchema).default([]),
});

export type AgentDefinition = ResolvedAgentDefinition;
export type PierConfig = z.infer<typeof pierConfigSchema>;

export interface LoadPierConfigOptions
	extends Partial<AgentDiscoveryOptions>,
		Partial<ResolveAgentDefinitionsOptions> {}

export interface DynamicAgentResolutionOptions
	extends Pick<
		LoadPierConfigOptions,
		'userHome' | 'piAgentDir' | 'discoverCommunityAgents'
	> {
	cwd: string;
}

export interface GlobalAgentResolutionOptions
	extends Pick<
		LoadPierConfigOptions,
		'userHome' | 'piAgentDir' | 'discoverCommunityAgents'
	> {}

export function loadPierConfig(
	options: LoadPierConfigOptions = {},
): PierConfig {
	const discoveryOptions = resolveDiscoveryOptions(options);
	const discoveredAgents = discoverAgentFiles(discoveryOptions);
	const agents = resolveAgentDefinitions(discoveredAgents, discoveryOptions);
	return pierConfigSchema.parse({ agents });
}

export function loadProjectAgentsForCwd(
	options: DynamicAgentResolutionOptions,
): AgentDefinition[] {
	const projectRoot = detectProjectRootFromCwd(options.cwd);
	const discoveryOptions = resolveDiscoveryOptions({
		projectRoot,
		userHome: options.userHome,
		piAgentDir: options.piAgentDir,
		discoverCommunityAgents: options.discoverCommunityAgents,
		includeGlobalAgents: false,
		includeProjectAgents: true,
	});
	const discoveredAgents = discoverAgentFiles(discoveryOptions);
	return resolveAgentDefinitions(discoveredAgents, discoveryOptions);
}

export function loadGlobalAgents(
	options: GlobalAgentResolutionOptions = {},
): AgentDefinition[] {
	const discoveryOptions = resolveDiscoveryOptions({
		projectRoot: process.cwd(),
		userHome: options.userHome,
		piAgentDir: options.piAgentDir,
		discoverCommunityAgents: options.discoverCommunityAgents,
		includeGlobalAgents: true,
		includeProjectAgents: false,
	});
	const discoveredAgents = discoverAgentFiles(discoveryOptions);
	return resolveAgentDefinitions(discoveredAgents, discoveryOptions);
}

// Kept for compatibility with the current startup path in index.ts.
export function loadConfigFromEnv(
	options: LoadPierConfigOptions = {},
): PierConfig {
	return loadPierConfig({
		includeProjectAgents: false,
		includeGlobalAgents: true,
		...options,
	});
}

export function detectProjectRootFromCwd(cwd: string): string {
	let currentDir = resolve(cwd);

	while (true) {
		if (isProjectRootCandidate(currentDir)) {
			return currentDir;
		}

		const parentDir = dirname(currentDir);
		if (parentDir === currentDir) {
			return resolve(cwd);
		}
		currentDir = parentDir;
	}
}

export function isProjectRootCandidate(directory: string): boolean {
	return (
		existsSync(join(directory, '.pi/agents')) ||
		existsSync(join(directory, '.pi/skills')) ||
		existsSync(join(directory, '.agents/skills')) ||
		existsSync(join(directory, 'pnpm-workspace.yaml')) ||
		existsSync(join(directory, '.git')) ||
		existsSync(join(directory, 'AGENTS.md'))
	);
}

function resolveDiscoveryOptions(
	options: LoadPierConfigOptions,
): AgentDiscoveryOptions & ResolveAgentDefinitionsOptions {
	const projectRoot = options.projectRoot ?? process.cwd();
	const userHome = options.userHome ?? homedir();
	const piAgentDir =
		options.piAgentDir ??
		process.env.PI_CODING_AGENT_DIR ??
		join(userHome, '.pi/agent');

	return {
		projectRoot,
		userHome,
		piAgentDir,
		includeGlobalAgents: options.includeGlobalAgents ?? true,
		includeProjectAgents: options.includeProjectAgents ?? true,
		discoverCommunityAgents:
			options.discoverCommunityAgents ??
			readBooleanEnv(process.env.PIER_DISCOVER_PI_SUBAGENT_AGENTS),
	};
}

function readBooleanEnv(raw: string | undefined): boolean {
	if (!raw) {
		return false;
	}

	switch (raw.trim().toLowerCase()) {
		case '1':
		case 'true':
		case 'yes':
		case 'on':
			return true;
		default:
			return false;
	}
}
