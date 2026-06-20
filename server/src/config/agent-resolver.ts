import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import {
	type AgentDiscoveryOptions,
	type AgentSourceScope,
	type ParsedAgentFile,
	parseMarkdownFrontmatterDocument,
} from './agent-loader.js';
import {
	type SystemPromptMode,
	type ThinkingLevel,
	systemPromptModeSchema,
	thinkingLevelSchema,
} from './agent-schema.js';

export interface ResolveAgentDefinitionsOptions
	extends Pick<
		AgentDiscoveryOptions,
		'projectRoot' | 'userHome' | 'piAgentDir'
	> {}

export const toolSelectionSchema = z.discriminatedUnion('mode', [
	z.object({
		mode: z
			.literal('inherit')
			.describe(
				'Use the Pi default tool set (built-in + extension tools as discovered by the Pi runtime).',
			),
	}),
	z.object({
		mode: z
			.literal('none')
			.describe('Agent has no tools available; prompt-only turn.'),
	}),
	z.object({
		mode: z
			.literal('allowlist')
			.describe(
				'Only the listed tool names are available. Built-in tool names plus `mcp:<server>` references are supported when the MCP adapter extension is loaded.',
			),
		values: z.array(z.string().min(1)).min(1),
	}),
]);

export const extensionSelectionSchema = z.discriminatedUnion('mode', [
	z.object({
		mode: z
			.literal('inherit')
			.describe(
				'Load every extension discovered by the Pi runtime (from settings.json packages and the extensions/ directory).',
			),
	}),
	z.object({
		mode: z.literal('none').describe('Load no extensions for this agent.'),
	}),
	z.object({
		mode: z
			.literal('allowlist')
			.describe(
				'Load only the listed extensions. Names are resolved to entry paths: npm package name, loose `.ts` filename under `<piAgentDir>/extensions/`, or an absolute/relative path.',
			),
		values: z.array(z.string().min(1)).min(1),
	}),
]);

export const resolvedSkillReferenceSchema = z.object({
	name: z.string().min(1),
	path: z.string().min(1),
	scope: z.enum(['project', 'user']),
});

export const resolvedAgentDefinitionSchema = z.object({
	id: z.string().min(1),
	name: z.string().min(1),
	description: z.string().min(1),
	source: z.object({
		path: z.string().min(1),
		scope: z.enum(['community', 'user', 'project']),
	}),
	prompt: z.string().nullable(),
	model: z.string().min(1).optional(),
	thinking: thinkingLevelSchema.optional(),
	tools: toolSelectionSchema,
	extensions: extensionSelectionSchema,
	skills: z.array(resolvedSkillReferenceSchema),
	inheritSkills: z.boolean(),
	inheritProjectContext: z.boolean(),
	systemPromptMode: systemPromptModeSchema,
	autoCompaction: z.boolean(),
	autoRetry: z.boolean(),
});

export type ToolSelection = z.infer<typeof toolSelectionSchema>;
export type ExtensionSelection = z.infer<typeof extensionSelectionSchema>;
export type ResolvedSkillReference = z.infer<
	typeof resolvedSkillReferenceSchema
>;
export type ResolvedAgentDefinition = z.infer<
	typeof resolvedAgentDefinitionSchema
>;

export function resolveAgentDefinitions(
	discoveredAgents: ParsedAgentFile[],
	options: ResolveAgentDefinitionsOptions,
): ResolvedAgentDefinition[] {
	const dedupedAgents = dedupeAgentsByName(discoveredAgents);

	return dedupedAgents
		.filter((agent) => agent.frontmatter.enabled)
		.map((agent) => resolveAgentDefinition(agent, options));
}

export function resolveAgentDefinition(
	agent: ParsedAgentFile,
	options: ResolveAgentDefinitionsOptions,
): ResolvedAgentDefinition {
	const prompt = normalizePrompt(agent.body);
	const { model, thinking } = normalizeModelAndThinking(
		agent.frontmatter.model,
		agent.frontmatter.thinking,
	);
	const skills = resolveSkillReferences(
		uniqueStrings(agent.frontmatter.skills),
		agent.frontmatter.inheritSkills,
		options,
	);

	return resolvedAgentDefinitionSchema.parse({
		id: agent.frontmatter.name,
		name: agent.frontmatter.name,
		description: agent.frontmatter.description,
		source: {
			path: agent.path,
			scope: agent.scope,
		},
		prompt,
		model,
		thinking,
		tools: resolveListSelection(agent.frontmatter.tools),
		extensions: resolveListSelection(agent.frontmatter.extensions),
		skills,
		inheritSkills: agent.frontmatter.inheritSkills,
		inheritProjectContext: agent.frontmatter.inheritProjectContext,
		systemPromptMode: agent.frontmatter.systemPromptMode,
		autoCompaction: agent.frontmatter.autoCompaction,
		autoRetry: agent.frontmatter.autoRetry,
	});
}

export function normalizeModelAndThinking(
	model: string | undefined,
	thinking: ThinkingLevel | undefined,
	options: { override?: boolean } = {},
): {
	model: string | undefined;
	thinking: ThinkingLevel | undefined;
} {
	const trimmedModel = model?.trim() || undefined;
	const extracted = extractThinkingSuffix(trimmedModel);

	if (!extracted) {
		return {
			model: trimmedModel,
			thinking,
		};
	}

	if (options.override) {
		return {
			model: extracted.model,
			thinking: extracted.thinking,
		};
	}

	if (thinking && thinking !== extracted.thinking) {
		throw new Error(
			`Model thinking suffix "${extracted.thinking}" conflicts with explicit thinking "${thinking}"`,
		);
	}

	return {
		model: extracted.model,
		thinking: thinking ?? extracted.thinking,
	};
}

function dedupeAgentsByName(
	discoveredAgents: ParsedAgentFile[],
): ParsedAgentFile[] {
	const sortedAgents = [...discoveredAgents].sort((left, right) => {
		if (left.priority !== right.priority) {
			return left.priority - right.priority;
		}

		return left.path.localeCompare(right.path);
	});

	const seenByScope = new Map<string, string>();
	const selectedByName = new Map<string, ParsedAgentFile>();

	for (const agent of sortedAgents) {
		const duplicateKey = `${agent.scope}:${agent.frontmatter.name}`;
		const existingPath = seenByScope.get(duplicateKey);
		if (existingPath) {
			throw new Error(
				`Duplicate agent "${agent.frontmatter.name}" found in ${agent.scope} scope:\n${existingPath}\n${agent.path}`,
			);
		}
		seenByScope.set(duplicateKey, agent.path);
		selectedByName.set(agent.frontmatter.name, agent);
	}

	return [...selectedByName.values()].sort((left, right) =>
		left.frontmatter.name.localeCompare(right.frontmatter.name),
	);
}

function resolveSkillReferences(
	skillNames: string[],
	inheritSkills: boolean,
	options: ResolveAgentDefinitionsOptions,
): ResolvedSkillReference[] {
	if (skillNames.length === 0) {
		return [];
	}

	const catalog = buildSkillCatalog(options, inheritSkills);

	return skillNames.map((name) => {
		const skill = catalog.get(name);
		if (!skill) {
			throw new Error(`Unable to resolve skill "${name}"`);
		}
		return skill;
	});
}

function buildSkillCatalog(
	options: ResolveAgentDefinitionsOptions,
	inheritSkills: boolean,
): Map<string, ResolvedSkillReference> {
	const roots = getSkillDiscoveryRoots(options, inheritSkills);
	const catalog = new Map<string, ResolvedSkillReference>();

	for (const root of roots) {
		for (const skillPath of collectSkillFiles(root.rootDir)) {
			const skill = parseSkillReference(skillPath, root.scope);
			catalog.set(skill.name, skill);
		}
	}

	return catalog;
}

function getSkillDiscoveryRoots(
	options: ResolveAgentDefinitionsOptions,
	inheritSkills: boolean,
): Array<{ rootDir: string; scope: 'project' | 'user' }> {
	const roots: Array<{ rootDir: string; scope: 'project' | 'user' }> = [
		{ rootDir: join(options.piAgentDir, 'skills'), scope: 'user' },
		{ rootDir: join(options.userHome, '.agents/skills'), scope: 'user' },
		{ rootDir: join(options.userHome, '.pi/skills'), scope: 'user' },
		{ rootDir: join(options.projectRoot, '.pi/skills'), scope: 'project' },
		{ rootDir: join(options.projectRoot, '.agents/skills'), scope: 'project' },
	];

	return inheritSkills
		? roots
		: roots.filter((root) => root.scope === 'project');
}

function collectSkillFiles(rootDir: string): string[] {
	if (!existsSync(rootDir)) {
		return [];
	}

	const results: string[] = [];
	const walk = (currentDir: string) => {
		const entries = readdirSync(currentDir, { withFileTypes: true }).sort(
			(left, right) => left.name.localeCompare(right.name),
		);

		for (const entry of entries) {
			const entryPath = join(currentDir, entry.name);
			if (entry.isDirectory()) {
				walk(entryPath);
				continue;
			}

			if (entry.isFile() && entry.name === 'SKILL.md') {
				results.push(entryPath);
			}
		}
	};

	walk(rootDir);
	return results;
}

function parseSkillReference(
	skillPath: string,
	scope: 'project' | 'user',
): ResolvedSkillReference {
	const contents = readFileSync(skillPath, 'utf8');
	const document = parseMarkdownFrontmatterDocument(contents, skillPath);
	const rawName = document.data.name;
	const name =
		typeof rawName === 'string' && rawName.trim().length > 0
			? rawName.trim()
			: skillPath.split('/').slice(-2, -1)[0];

	return resolvedSkillReferenceSchema.parse({
		name,
		path: skillPath,
		scope,
	});
}

function resolveListSelection(
	value: string[] | null | undefined,
): ToolSelection | ExtensionSelection {
	if (value === undefined) {
		return { mode: 'inherit' };
	}

	const uniqueValues = uniqueStrings(value ?? []);
	if (uniqueValues.length === 0) {
		return { mode: 'none' };
	}

	return {
		mode: 'allowlist',
		values: uniqueValues,
	};
}

function normalizePrompt(body: string): string | null {
	const normalized = body.trim();
	return normalized.length > 0 ? normalized : null;
}

function extractThinkingSuffix(
	model: string | undefined,
): { model: string; thinking: ThinkingLevel } | null {
	if (!model) {
		return null;
	}

	const separatorIndex = model.lastIndexOf(':');
	if (separatorIndex === -1) {
		return null;
	}

	const candidate = model.slice(separatorIndex + 1);
	const parsed = thinkingLevelSchema.safeParse(candidate);
	if (!parsed.success) {
		return null;
	}

	return {
		model: model.slice(0, separatorIndex),
		thinking: parsed.data,
	};
}

function uniqueStrings(values: string[]): string[] {
	const seen = new Set<string>();
	const results: string[] = [];

	for (const value of values) {
		if (seen.has(value)) {
			continue;
		}
		seen.add(value);
		results.push(value);
	}

	return results;
}
