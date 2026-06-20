import { describe, expect, it } from 'vitest';
import type { AgentDefinition } from '../config.js';
import { listAgents } from './registry.js';

describe('listAgents', () => {
	it('returns cloned resolved agent definitions', () => {
		const agents: AgentDefinition[] = [
			{
				id: 'review',
				name: 'review',
				description: 'Review code changes',
				source: {
					path: '/tmp/review.md',
					scope: 'project',
				},
				prompt: 'Review the repository.',
				tools: {
					mode: 'inherit',
				},
				extensions: {
					mode: 'inherit',
				},
				skills: [],
				inheritSkills: true,
				inheritProjectContext: true,
				systemPromptMode: 'replace',
				autoCompaction: true,
				autoRetry: true,
			},
		];

		expect(listAgents(agents)).toEqual(agents);
		expect(listAgents(agents)[0]).not.toBe(agents[0]);
	});
});
