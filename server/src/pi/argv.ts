import {
	type ResolvedAgentDefinition,
	normalizeModelAndThinking,
} from '../config/agent-resolver.js';
import type { ThinkingLevel } from '../config/agent-schema.js';

export interface PiRpcLaunchOverrides {
	cwd?: string;
	model?: string;
	name?: string;
	provider?: string;
	session?: string;
	sessionDir?: string;
	thinking?: ThinkingLevel;
	extensionPaths?: string[];
}

export function buildPiRpcArgv(
	agent: ResolvedAgentDefinition,
	overrides: PiRpcLaunchOverrides = {},
): string[] {
	const args = ['--mode', 'rpc'];
	const isOverride = Boolean(overrides.model);
	const modelAndThinking = normalizeModelAndThinking(
		overrides.model ?? agent.model,
		overrides.thinking ?? agent.thinking,
		{ override: isOverride },
	);

	pushOptionalFlag(args, '--provider', overrides.provider);
	pushOptionalFlag(args, '--model', modelAndThinking.model);
	pushOptionalFlag(args, '--thinking', modelAndThinking.thinking);
	pushOptionalFlag(args, '--name', overrides.name);
	pushOptionalFlag(args, '--session', overrides.session);
	pushOptionalFlag(args, '--session-dir', overrides.sessionDir);

	switch (agent.tools.mode) {
		case 'none':
			args.push('--no-tools');
			break;
		case 'allowlist':
			args.push('--tools', agent.tools.values.join(','));
			break;
		case 'inherit':
			break;
	}

	switch (agent.extensions.mode) {
		case 'none':
			args.push('--no-extensions');
			break;
		case 'allowlist': {
			args.push('--no-extensions');
			const paths = overrides.extensionPaths ?? agent.extensions.values;
			for (const extension of paths) {
				args.push('-e', extension);
			}
			break;
		}
		case 'inherit':
			break;
	}

	if (!agent.inheritSkills) {
		args.push('--no-skills');
	}

	for (const skill of agent.skills) {
		args.push('--skill', skill.path);
	}

	if (!agent.inheritProjectContext) {
		args.push('--no-context-files');
	}

	if (agent.prompt) {
		args.push(
			agent.systemPromptMode === 'append'
				? '--append-system-prompt'
				: '--system-prompt',
			agent.prompt,
		);
	}

	return args;
}

function pushOptionalFlag(
	args: string[],
	flag: string,
	value: string | undefined,
): void {
	if (!value) {
		return;
	}

	args.push(flag, value);
}
