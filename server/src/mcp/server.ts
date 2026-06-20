import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { WebStandardStreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js';
import { z } from 'zod';
import { listAgents } from '../agents/registry.js';
import type { AgentDefinition } from '../config.js';
import { resolvedAgentDefinitionSchema } from '../config/agent-resolver.js';
import { thinkingLevelSchema } from '../config/agent-schema.js';
import { PierError, type PierErrorCode, isPierError } from '../errors.js';
import type {
	SessionManager,
	SessionStatusChangedEvent,
	SessionSummary,
} from '../runtime/types.js';

const agentActionSchema = z.enum(['send', 'status', 'abort', 'steer']);

const listedAgentSchema = resolvedAgentDefinitionSchema.omit({
	name: true,
	prompt: true,
});

const listAgentsResultSchema = z.object({
	agents: z.array(listedAgentSchema),
});

const modelSummarySchema = z.object({
	provider: z.string().optional(),
	modelId: z.string().optional(),
	name: z.string().optional(),
	displayName: z.string().optional(),
	reasoning: z.boolean().optional(),
	input: z.array(z.string()).optional(),
	contextWindow: z.number().optional(),
	maxTokens: z.number().optional(),
});

const listModelsResultSchema = z.object({
	models: z.array(modelSummarySchema),
});

const agentResultSchema = z.object({
	sessionId: z.string(),
	agentId: z.string(),
	status: z.enum([
		'starting',
		'running',
		'aborting',
		'completed',
		'error',
		'aborted',
		'stopped',
	]),
	updatedAt: z.string(),
	sessionFile: z.string().optional(),
	model: z.string().optional(),
	thinking: thinkingLevelSchema.optional(),
	lastAssistantTextPreview: z
		.string()
		.optional()
		.describe(
			'Bounded preview of the most recent assistant turn. Not the full transcript; retrieve the full transcript from `sessionFile` or a future pagination tool.',
		),
	error: z.string().optional(),
});

function formatStructuredToolResult<T>(structuredContent: T) {
	return {
		content: [
			{
				type: 'text' as const,
				text: JSON.stringify(structuredContent, null, 2),
			},
		],
		structuredContent,
	};
}

function createToolError(
	message: string,
	code: PierErrorCode = 'VALIDATION_FAILED',
) {
	return {
		content: [{ type: 'text' as const, text: `${code}: ${message}` }],
		isError: true,
		structuredContent: { errorType: code, message },
	};
}

function createStructuredToolError(error: unknown) {
	if (isPierError(error)) {
		const payload = {
			errorType: error.code,
			message: error.message,
		};
		return {
			content: [
				{
					type: 'text' as const,
					text: `${error.code}: ${error.message}`,
				},
			],
			isError: true,
			structuredContent: payload,
		};
	}
	const message = error instanceof Error ? error.message : String(error);
	const code = inferErrorCode(message);
	return {
		content: [{ type: 'text' as const, text: `${code}: ${message}` }],
		isError: true,
		structuredContent: { errorType: code, message },
	};
}

function inferErrorMessageCode(message: string): PierErrorCode | undefined {
	if (/Model ".*" not found/i.test(message)) {
		return 'MODEL_NOT_FOUND';
	}
	if (
		/Extension .* does not exist|Extension ".*" was not found/i.test(message)
	) {
		return 'EXTENSION_NOT_FOUND';
	}
	return undefined;
}

function inferErrorCode(message: string): PierErrorCode {
	return inferErrorMessageCode(message) ?? 'INTERNAL_ERROR';
}

function toAgentResult(session: SessionSummary) {
	return {
		sessionId: session.sessionId,
		agentId: session.agentId,
		status: session.status,
		updatedAt: session.updatedAt,
		sessionFile: session.sessionFile,
		model: session.model,
		thinking: session.thinking,
		lastAssistantTextPreview: session.lastAssistantTextPreview,
		error: session.error,
	};
}

export function createMcpServer(
	agents: AgentDefinition[],
	sessionManager: SessionManager,
): McpServer {
	const server = new McpServer({
		name: 'pier',
		version: '0.0.1',
	});
	server.registerTool(
		'list_agents',
		{
			description:
				'List runnable Pier agents for the current project context. Returns a fresh snapshot of effective agent settings for selection and execution, without prompt text. When cwd is provided, Pier merges global agents with project agents from that cwd, and project agents override global agents with the same id.',
			inputSchema: {
				cwd: z
					.string()
					.optional()
					.describe(
						'Optional project working directory. Omit to list only global agents. Provide cwd to list the merged global + project agents available for that project. If both scopes contain the same agent id, the project agent from cwd takes precedence.',
					),
			},
			outputSchema: listAgentsResultSchema,
			annotations: {
				readOnlyHint: true,
				destructiveHint: false,
				idempotentHint: true,
				openWorldHint: false,
			},
		},
		async ({ cwd }) => {
			const rows = sessionManager.listAgents
				? await sessionManager.listAgents(cwd)
				: listAgents(agents);
			return formatStructuredToolResult({
				agents: rows.map(
					({ name: _name, prompt: _prompt, ...definition }) => definition,
				),
			});
		},
	);

	server.registerTool(
		'list_models',
		{
			description:
				'List currently configured Pi models as a fresh snapshot. Use provider and modelId from this result to construct agent.send model overrides in provider/model format.',
			inputSchema: z.object({}).strict(),
			outputSchema: listModelsResultSchema,
			annotations: {
				readOnlyHint: true,
				destructiveHint: false,
				idempotentHint: true,
				openWorldHint: false,
			},
		},
		async () => {
			const models = (await sessionManager.listModels?.()) ?? [];
			return formatStructuredToolResult({ models });
		},
	);

	server.registerTool(
		'agent',
		{
			description:
				'Manage a Pi-backed agent session. Use send to start a new session or continue an existing one, status to read the latest session snapshot, steer to redirect a running session, and abort to cancel active work.',
			inputSchema: {
				action: agentActionSchema.describe(
					'Session action. send starts a new session when sessionId is omitted, continues a running session when sessionId is active, and resumes a stopped or completed session when sessionId is provided but not running. status reads the latest stored session snapshot. steer redirects a currently running session. abort requests cancellation of the active run while keeping the session available for later status checks and future send calls.',
				),
				agentId: z
					.string()
					.optional()
					.describe(
						'Required for action=send when sessionId is omitted. Must match list_agents[].id from the latest resolved agent snapshot. Ignored when sessionId is provided.',
					),
				task: z
					.string()
					.optional()
					.describe(
						'Required for action=send and action=steer; ignored by status and abort. ' +
							'For action=send, this is the primary user instruction that begins or continues a session turn and is recorded into the session history. ' +
							'For action=steer, this is a redirect signal injected into a currently running turn; the agent tries to honor the new instruction but may still retain prior context, and it is not guaranteed to fully replace the original task.',
					),
				sessionId: z
					.string()
					.optional()
					.describe(
						'Optional for action=send. Omit to create a new session. Provide it to continue a running session or resume a non-running session. Required for status, steer, and abort.',
					),
				cwd: z
					.string()
					.optional()
					.describe(
						'Required for action=send when sessionId is omitted. Used both to resolve project-scoped agents and as the Pi process working directory for the new session. Ignored when sessionId is provided.',
					),
				model: z
					.string()
					.optional()
					.describe(
						'Optional one-session model override for action=send when creating a new session. Format: provider/model, for example agnes-ai/agnes-2.0-flash. ' +
							'An optional `:thinking` suffix (e.g. `agnes-ai/agnes-2.0-flash:high`) is honored as an override and takes precedence over the agent frontmatter `thinking`. Ignored when sessionId is provided.',
					),
			},
			outputSchema: agentResultSchema,
			annotations: {
				destructiveHint: false,
				idempotentHint: false,
				openWorldHint: true,
			},
		},
		async ({ action, agentId, cwd, model, sessionId, task }) => {
			try {
				switch (action) {
					case 'send': {
						if (!task) {
							return createToolError('action=send requires task');
						}
						const session = sessionId
							? await sessionManager.followUp({ sessionId, task })
							: !agentId || !cwd
								? null
								: await sessionManager.start({
										agentId,
										task,
										cwd,
										model,
									});
						if (!session) {
							return createToolError(
								'action=send without sessionId requires agentId and cwd',
							);
						}
						return formatStructuredToolResult(toAgentResult(session));
					}
					case 'status': {
						if (!sessionId) {
							return createToolError('action=status requires sessionId');
						}
						const session = sessionManager.get(sessionId);
						if (!session) {
							return createToolError(`Unknown session: ${sessionId}`);
						}
						return formatStructuredToolResult(toAgentResult(session));
					}
					case 'abort': {
						if (!sessionId) {
							return createToolError('action=abort requires sessionId');
						}
						const session = await sessionManager.abort(sessionId);
						return formatStructuredToolResult(toAgentResult(session));
					}
					case 'steer': {
						if (!sessionId || !task) {
							return createToolError(
								'action=steer requires both sessionId and task',
							);
						}
						const session = await sessionManager.steer({ sessionId, task });
						return formatStructuredToolResult(toAgentResult(session));
					}
				}
			} catch (error) {
				return createStructuredToolError(error);
			}
			return createToolError(`Unsupported action: ${String(action)}`);
		},
	);

	server.server.registerCapabilities({
		tools: { listChanged: false },
	});

	return server;
}

export function createMcpTransport(): WebStandardStreamableHTTPServerTransport {
	return new WebStandardStreamableHTTPServerTransport({
		sessionIdGenerator: () => crypto.randomUUID(),
	});
}

export async function sendSessionStatusChangedNotification(
	server: McpServer,
	event: SessionStatusChangedEvent,
): Promise<void> {
	await server.server.notification({
		method: 'notifications/pier/session_status_changed',
		params: event,
	});
}
