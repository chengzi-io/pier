import { z } from 'zod';

export const thinkingLevelSchema = z.enum([
	'off',
	'minimal',
	'low',
	'medium',
	'high',
	'xhigh',
]);

export const systemPromptModeSchema = z.enum(['replace', 'append']);

export const nullableStringArraySchema = z
	.array(z.string().trim().min(1))
	.nullable()
	.optional();

export const pierAgentFrontmatterSchema = z.object({
	name: z
		.string()
		.trim()
		.min(1)
		.max(64)
		.regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/, {
			message:
				'Agent name must use lowercase letters, numbers, and single hyphens.',
		}),
	description: z.string().trim().min(1).max(1024),
	enabled: z.boolean().default(true),
	model: z.string().trim().min(1).optional(),
	thinking: thinkingLevelSchema.optional(),
	tools: nullableStringArraySchema,
	extensions: nullableStringArraySchema,
	skills: z.array(z.string().trim().min(1)).default([]),
	inheritSkills: z.boolean().default(true),
	inheritProjectContext: z.boolean().default(true),
	systemPromptMode: systemPromptModeSchema.default('replace'),
	autoCompaction: z.boolean().default(true),
	autoRetry: z.boolean().default(true),
});

export const skillFrontmatterSchema = z.object({
	name: z.string().trim().min(1),
});

export type ThinkingLevel = z.infer<typeof thinkingLevelSchema>;
export type SystemPromptMode = z.infer<typeof systemPromptModeSchema>;
export type PierAgentFrontmatter = z.infer<typeof pierAgentFrontmatterSchema>;
export type SkillFrontmatter = z.infer<typeof skillFrontmatterSchema>;
