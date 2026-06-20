import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import {
	type PierAgentFrontmatter,
	pierAgentFrontmatterSchema,
} from './agent-schema.js';

export type AgentSourceScope = 'community' | 'user' | 'project';

export interface AgentDiscoveryOptions {
	projectRoot: string;
	userHome: string;
	piAgentDir: string;
	includeGlobalAgents?: boolean;
	includeProjectAgents?: boolean;
	discoverCommunityAgents?: boolean;
}

export interface AgentDiscoveryRoot {
	scope: AgentSourceScope;
	rootDir: string;
	priority: number;
}

export interface ParsedFrontmatterDocument {
	data: Record<string, FrontmatterValue>;
	body: string;
}

export type FrontmatterValue = string | boolean | null | string[];

export interface ParsedAgentFile {
	path: string;
	rootDir: string;
	scope: AgentSourceScope;
	priority: number;
	frontmatter: PierAgentFrontmatter;
	body: string;
}

export function discoverAgentFiles(
	options: AgentDiscoveryOptions,
): ParsedAgentFile[] {
	const roots = getAgentDiscoveryRoots(options);

	return roots.flatMap((root) =>
		collectMarkdownFiles(root.rootDir).map((filePath) =>
			parseAgentFile({
				filePath,
				rootDir: root.rootDir,
				scope: root.scope,
				priority: root.priority,
			}),
		),
	);
}

export function getAgentDiscoveryRoots(
	options: AgentDiscoveryOptions,
): AgentDiscoveryRoot[] {
	const roots: AgentDiscoveryRoot[] = [];

	if (options.includeGlobalAgents ?? true) {
		roots.push({
			scope: 'user',
			rootDir: join(options.piAgentDir, 'agents'),
			priority: 1,
		});
	}

	if (options.includeProjectAgents ?? true) {
		roots.push({
			scope: 'project',
			rootDir: join(options.projectRoot, '.pi/agents'),
			priority: 2,
		});
	}

	if (options.discoverCommunityAgents) {
		roots.unshift({
			scope: 'community',
			rootDir: join(options.piAgentDir, 'extensions/subagent/agents'),
			priority: 0,
		});
	}

	return roots;
}

export function parseAgentFile(input: {
	filePath: string;
	rootDir: string;
	scope: AgentSourceScope;
	priority: number;
}): ParsedAgentFile {
	const contents = readFileSync(input.filePath, 'utf8');
	const document = parseMarkdownFrontmatterDocument(contents, input.filePath);
	const frontmatter = pierAgentFrontmatterSchema.parse(document.data);

	return {
		path: input.filePath,
		rootDir: input.rootDir,
		scope: input.scope,
		priority: input.priority,
		frontmatter,
		body: document.body,
	};
}

export function parseMarkdownFrontmatterDocument(
	contents: string,
	sourcePath = '<inline>',
): ParsedFrontmatterDocument {
	if (!contents.startsWith('---\n') && !contents.startsWith('---\r\n')) {
		throw new Error(`Missing frontmatter block in ${sourcePath}`);
	}

	const normalized = contents.replace(/\r\n/g, '\n');
	const closingIndex = normalized.indexOf('\n---\n', 4);

	if (closingIndex === -1) {
		throw new Error(`Unterminated frontmatter block in ${sourcePath}`);
	}

	const frontmatterText = normalized.slice(4, closingIndex);
	const body = normalized.slice(closingIndex + '\n---\n'.length);

	return {
		data: parseFrontmatterObject(frontmatterText, sourcePath),
		body: trimLeadingNewline(body),
	};
}

function collectMarkdownFiles(rootDir: string): string[] {
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

			if (entry.isFile() && entry.name.endsWith('.md')) {
				results.push(entryPath);
			}
		}
	};

	walk(rootDir);
	return results;
}

function parseFrontmatterObject(
	frontmatterText: string,
	sourcePath: string,
): Record<string, FrontmatterValue> {
	const lines = frontmatterText.split('\n');
	const data: Record<string, FrontmatterValue> = {};

	for (let index = 0; index < lines.length; ) {
		const line = lines[index];
		const trimmed = line.trim();

		if (trimmed.length === 0 || trimmed.startsWith('#')) {
			index += 1;
			continue;
		}

		if (/^\s/.test(line)) {
			throw new Error(
				`Unexpected indentation in frontmatter at ${sourcePath}:${index + 1}`,
			);
		}

		const match = line.match(/^([A-Za-z][A-Za-z0-9]*):(.*)$/);
		if (!match) {
			throw new Error(
				`Invalid frontmatter entry in ${sourcePath}:${index + 1}: ${line}`,
			);
		}

		const [, key, remainder] = match;
		const inlineValue = remainder.trim();

		if (inlineValue.length > 0) {
			data[key] = parseScalarValue(inlineValue, sourcePath, index + 1);
			index += 1;
			continue;
		}

		index += 1;
		const items: string[] = [];

		while (index < lines.length) {
			const blockLine = lines[index];
			const blockTrimmed = blockLine.trim();

			if (blockTrimmed.length === 0 || blockTrimmed.startsWith('#')) {
				index += 1;
				continue;
			}

			if (!/^\s/.test(blockLine)) {
				break;
			}

			const itemMatch = blockLine.match(/^\s*-\s+(.*)$/);
			if (!itemMatch) {
				throw new Error(
					`Only list items are supported in frontmatter blocks (${sourcePath}:${
						index + 1
					})`,
				);
			}

			const value = parseScalarValue(
				itemMatch[1].trim(),
				sourcePath,
				index + 1,
			);
			if (typeof value !== 'string') {
				throw new Error(
					`List items must be strings in ${sourcePath}:${index + 1}`,
				);
			}

			items.push(value);
			index += 1;
		}

		data[key] = items.length === 0 ? null : items;
	}

	return data;
}

function parseScalarValue(
	rawValue: string,
	sourcePath: string,
	lineNumber: number,
): FrontmatterValue {
	if (rawValue === 'true') {
		return true;
	}

	if (rawValue === 'false') {
		return false;
	}

	if (rawValue === 'null') {
		return null;
	}

	if (rawValue === '[]') {
		return [];
	}

	if (rawValue.startsWith('[') && rawValue.endsWith(']')) {
		return parseInlineStringArray(
			rawValue.slice(1, -1),
			sourcePath,
			lineNumber,
		);
	}

	if (rawValue.startsWith('"') && rawValue.endsWith('"')) {
		return JSON.parse(rawValue) as string;
	}

	if (rawValue.startsWith("'") && rawValue.endsWith("'")) {
		return rawValue.slice(1, -1);
	}

	return rawValue;
}

function parseInlineStringArray(
	value: string,
	sourcePath: string,
	lineNumber: number,
): string[] {
	if (value.trim().length === 0) {
		return [];
	}

	return value.split(',').map((item) => {
		const parsed = parseScalarValue(item.trim(), sourcePath, lineNumber);
		if (typeof parsed !== 'string') {
			throw new Error(
				`Inline arrays only support strings in ${sourcePath}:${lineNumber}`,
			);
		}
		return parsed;
	});
}

function trimLeadingNewline(value: string): string {
	return value.startsWith('\n') ? value.slice(1) : value;
}
