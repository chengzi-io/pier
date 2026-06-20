import {
	mkdirSync,
	mkdtempSync,
	realpathSync,
	rmSync,
	writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import {
	detectProjectRootFromCwd,
	loadConfigFromEnv,
	loadPierConfig,
	loadProjectAgentsForCwd,
	pierConfigSchema,
} from './config.js';
import {
	type ResolvedAgentDefinition,
	normalizeModelAndThinking,
} from './config/agent-resolver.js';
import { buildPiRpcArgv } from './pi/argv.js';

const tempDirs: string[] = [];
const originalCwd = process.cwd();

afterEach(() => {
	process.chdir(originalCwd);
	for (const directory of tempDirs.splice(0)) {
		rmSync(directory, { recursive: true, force: true });
	}
});

describe('pierConfigSchema', () => {
	it('accepts empty agents', () => {
		expect(pierConfigSchema.parse({})).toEqual({ agents: [] });
	});
});

describe('loadPierConfig', () => {
	it('loads project agents, filters disabled entries, and resolves runtime config', () => {
		const fixture = createFixture();
		writeAgent(
			fixture.projectRoot,
			'.pi/agents/review.md',
			`---
name: review
description: Project review agent
model: anthropic/claude-sonnet-4-20250514:high
tools:
  - read
  - bash
extensions:
skills:
  - browser-testing
inheritSkills: false
inheritProjectContext: false
systemPromptMode: append
autoCompaction: false
---
Review the current changes carefully.
`,
		);
		writeAgent(
			fixture.projectRoot,
			'.pi/agents/disabled.md',
			`---
name: disabled
description: Disabled agent
enabled: false
---
Should not load.
`,
		);
		writeSkill(
			fixture.projectRoot,
			'.agents/skills/browser-testing/SKILL.md',
			`---
name: browser-testing
---
# Browser testing
`,
		);

		const config = loadPierConfig(fixture.options);

		expect(config.agents).toHaveLength(1);
		expect(config.agents[0]).toMatchObject({
			id: 'review',
			name: 'review',
			description: 'Project review agent',
			source: {
				scope: 'project',
			},
			model: 'anthropic/claude-sonnet-4-20250514',
			thinking: 'high',
			tools: {
				mode: 'allowlist',
				values: ['read', 'bash'],
			},
			extensions: {
				mode: 'none',
			},
			inheritSkills: false,
			inheritProjectContext: false,
			systemPromptMode: 'append',
			autoCompaction: false,
			autoRetry: true,
			prompt: 'Review the current changes carefully.',
		});
		expect(config.agents[0]?.skills).toEqual([
			{
				name: 'browser-testing',
				path: join(
					fixture.projectRoot,
					'.agents/skills/browser-testing/SKILL.md',
				),
				scope: 'project',
			},
		]);
	});

	it('prefers project agents over user agents with the same name', () => {
		const fixture = createFixture();
		writeAgent(
			fixture.userHome,
			'.pi/agent/agents/review.md',
			`---
name: review
description: User review agent
---
Use the user version.
`,
		);
		writeAgent(
			fixture.projectRoot,
			'.pi/agents/review.md',
			`---
name: review
description: Project review agent
---
Use the project version.
`,
		);

		const config = loadPierConfig(fixture.options);

		expect(config.agents).toHaveLength(1);
		expect(config.agents[0]?.description).toBe('Project review agent');
		expect(config.agents[0]?.source.path).toBe(
			join(fixture.projectRoot, '.pi/agents/review.md'),
		);
	});

	it('includes optional community agents only when enabled', () => {
		const fixture = createFixture();
		writeAgent(
			fixture.userHome,
			'.pi/agent/extensions/subagent/agents/scout.md',
			`---
name: scout
description: Community scout agent
---
Scout around.
`,
		);

		expect(loadPierConfig(fixture.options).agents).toHaveLength(0);
		expect(
			loadPierConfig({
				...fixture.options,
				discoverCommunityAgents: true,
			}).agents,
		).toHaveLength(1);
	});

	it('uses project skill definitions to override user skill definitions', () => {
		const fixture = createFixture();
		writeSkill(
			fixture.userHome,
			'.pi/agent/skills/browser-testing/SKILL.md',
			`---
name: browser-testing
---
user
`,
		);
		writeSkill(
			fixture.projectRoot,
			'.agents/skills/browser-testing/SKILL.md',
			`---
name: browser-testing
---
project
`,
		);
		writeAgent(
			fixture.projectRoot,
			'.pi/agents/review.md',
			`---
name: review
description: Review agent
skills:
  - browser-testing
---
Prompt.
`,
		);

		const config = loadPierConfig(fixture.options);

		expect(config.agents[0]?.skills[0]?.path).toBe(
			join(fixture.projectRoot, '.agents/skills/browser-testing/SKILL.md'),
		);
	});
});

describe('dynamic project agent loading', () => {
	it('loads only global agents by default from env startup', () => {
		const fixture = createFixture();
		writeAgent(
			fixture.userHome,
			'.pi/agent/agents/review.md',
			`---
name: review
description: Global review agent
---
Use the global version.
`,
		);
		writeAgent(
			fixture.projectRoot,
			'.pi/agents/project-review.md',
			`---
name: project-review
description: Project review agent
---
Use the project version.
`,
		);

		const config = loadConfigFromEnv({
			projectRoot: fixture.projectRoot,
			userHome: fixture.userHome,
			piAgentDir: join(fixture.userHome, '.pi/agent'),
		});

		expect(config.agents).toHaveLength(1);
		expect(config.agents[0]?.id).toBe('review');
		expect(config.agents[0]?.description).toBe('Global review agent');
	});

	it('loads project agents dynamically from cwd and prefers them over global agents', () => {
		const fixture = createFixture();
		writeAgent(
			fixture.userHome,
			'.pi/agent/agents/review.md',
			`---
name: review
description: Global review agent
---
Use the global version.
`,
		);
		writeAgent(
			fixture.projectRoot,
			'.pi/agents/review.md',
			`---
name: review
description: Project review agent
---
Use the project version.
`,
		);

		const agents = loadProjectAgentsForCwd({
			cwd: join(fixture.projectRoot, 'apps/web/src'),
			userHome: fixture.userHome,
			piAgentDir: join(fixture.userHome, '.pi/agent'),
		});

		expect(agents).toHaveLength(1);
		expect(agents[0]?.description).toBe('Project review agent');
		expect(realpathSync(agents[0]?.source.path ?? '')).toBe(
			realpathSync(join(fixture.projectRoot, '.pi/agents/review.md')),
		);
	});

	it('detects the target project root from cwd instead of the pier process cwd', () => {
		const fixture = createFixture();
		mkdirSync(join(fixture.projectRoot, 'apps/web/src'), { recursive: true });
		writeFileSync(
			join(fixture.projectRoot, 'pnpm-workspace.yaml'),
			'packages:\n  - apps/*\n',
		);

		expect(
			realpathSync(
				detectProjectRootFromCwd(join(fixture.projectRoot, 'apps/web/src')),
			),
		).toBe(realpathSync(fixture.projectRoot));
	});
});

describe('normalizeModelAndThinking', () => {
	it('accepts embedded thinking suffixes when explicit thinking is absent', () => {
		expect(
			normalizeModelAndThinking('anthropic/claude-sonnet-4:medium', undefined),
		).toEqual({
			model: 'anthropic/claude-sonnet-4',
			thinking: 'medium',
		});
	});

	it('rejects conflicting model suffix and explicit thinking values', () => {
		expect(() =>
			normalizeModelAndThinking('anthropic/claude-sonnet-4:high', 'low'),
		).toThrow(/conflicts/);
	});

	it('lets override thinking suffix win over agent thinking when override flag is set', () => {
		expect(
			normalizeModelAndThinking('agnes-ai/agnes-2.0-flash:high', 'off', {
				override: true,
			}),
		).toEqual({
			model: 'agnes-ai/agnes-2.0-flash',
			thinking: 'high',
		});
	});

	it('keeps agent thinking untouched when override model has no suffix', () => {
		expect(
			normalizeModelAndThinking('agnes-ai/agnes-2.0-flash', 'off', {
				override: true,
			}),
		).toEqual({
			model: 'agnes-ai/agnes-2.0-flash',
			thinking: 'off',
		});
	});
});

describe('buildPiRpcArgv', () => {
	it('maps resolved runtime config to deterministic Pi argv', () => {
		const agent: ResolvedAgentDefinition = {
			id: 'review',
			name: 'review',
			description: 'Review agent',
			source: {
				path: '/tmp/review.md',
				scope: 'project',
			},
			prompt: 'Review the repository.',
			model: 'anthropic/claude-sonnet-4',
			thinking: 'high',
			tools: {
				mode: 'allowlist',
				values: ['read', 'bash'],
			},
			extensions: {
				mode: 'allowlist',
				values: ['pi-mcp-adapter', '/tmp/ext.ts'],
			},
			skills: [
				{
					name: 'browser-testing',
					path: '/skills/browser-testing/SKILL.md',
					scope: 'project',
				},
			],
			inheritSkills: false,
			inheritProjectContext: false,
			systemPromptMode: 'append',
			autoCompaction: true,
			autoRetry: true,
		};

		expect(
			buildPiRpcArgv(agent, {
				cwd: '/workspace',
				name: 'Review Session',
				sessionDir: '/sessions',
			}),
		).toEqual([
			'--mode',
			'rpc',
			'--model',
			'anthropic/claude-sonnet-4',
			'--thinking',
			'high',
			'--name',
			'Review Session',
			'--session-dir',
			'/sessions',
			'--tools',
			'read,bash',
			'--no-extensions',
			'-e',
			'pi-mcp-adapter',
			'-e',
			'/tmp/ext.ts',
			'--no-skills',
			'--skill',
			'/skills/browser-testing/SKILL.md',
			'--no-context-files',
			'--append-system-prompt',
			'Review the repository.',
		]);
	});

	it('uses explicit empty semantics for tools and extensions', () => {
		const agent: ResolvedAgentDefinition = {
			id: 'shell',
			name: 'shell',
			description: 'Shell agent',
			source: {
				path: '/tmp/shell.md',
				scope: 'project',
			},
			prompt: null,
			model: undefined,
			thinking: undefined,
			tools: { mode: 'none' },
			extensions: { mode: 'none' },
			skills: [],
			inheritSkills: true,
			inheritProjectContext: true,
			systemPromptMode: 'replace',
			autoCompaction: true,
			autoRetry: false,
		};

		expect(buildPiRpcArgv(agent)).toEqual([
			'--mode',
			'rpc',
			'--no-tools',
			'--no-extensions',
		]);
	});
});

function createFixture() {
	const root = mkdtempSync(join(tmpdir(), 'pier-config-test-'));
	const projectRoot = join(root, 'project');
	const userHome = join(root, 'home');
	const piAgentDir = join(userHome, '.pi/agent');

	mkdirSync(projectRoot, { recursive: true });
	mkdirSync(userHome, { recursive: true });
	tempDirs.push(root);

	return {
		projectRoot,
		userHome,
		piAgentDir,
		options: {
			projectRoot,
			userHome,
			piAgentDir,
			discoverCommunityAgents: false,
		},
	};
}

function writeAgent(
	root: string,
	relativePath: string,
	contents: string,
): void {
	writeFile(root, relativePath, contents);
}

function writeSkill(
	root: string,
	relativePath: string,
	contents: string,
): void {
	writeFile(root, relativePath, contents);
}

function writeFile(root: string, relativePath: string, contents: string): void {
	const filePath = join(root, relativePath);
	mkdirSync(dirname(filePath), { recursive: true });
	writeFileSync(filePath, contents, 'utf8');
}
