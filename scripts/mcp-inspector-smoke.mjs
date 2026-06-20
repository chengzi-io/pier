import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(__dirname, '..');
const runId = new Date()
	.toISOString()
	.replaceAll(':', '-')
	.replaceAll('.', '-');
const runDir = join(projectRoot, '.tmp', 'mcp-inspector-smoke', runId);
const workspaceDir = join(runDir, 'workspace');
const reportPath = join(runDir, 'report.md');
const port = Number(process.env.PIER_SMOKE_PORT ?? 18787);
const host = process.env.PIER_SMOKE_HOST ?? '127.0.0.1';
const baseUrl = `http://${host}:${port}`;
const mcpUrl = `${baseUrl}/mcp`;
const inspectorCliCommand = 'pnpm';
const inspectorCliBaseArgs = ['exec', 'mcp-inspector', '--cli', '--'];

mkdirSync(join(workspaceDir, '.pi', 'agents'), { recursive: true });
writeFileSync(
	join(workspaceDir, '.pi', 'agents', 'smoke-review.md'),
	`---
name: smoke-review
description: Minimal agent for MCP Inspector smoke testing
model: agnes-ai/agnes-2.0-flash
thinking: off
tools:
extensions:
inheritSkills: false
inheritProjectContext: false
systemPromptMode: replace
autoCompaction: true
autoRetry: false
---
Reply with exactly \`OK\`.
`,
	'utf8',
);
writeFileSync(
	join(workspaceDir, '.pi', 'agents', 'slow-review.md'),
	`---
name: slow-review
description: Deliberately slow agent for steer and abort smoke testing
model: agnes-ai/agnes-2.0-flash
thinking: off
tools:
extensions:
inheritSkills: false
inheritProjectContext: false
systemPromptMode: replace
autoCompaction: true
autoRetry: false
---
Before replying, wait around 8 seconds. Then reply with exactly \`SLOW_OK\`.
If the latest instruction changes the target output, follow that latest instruction instead.
`,
	'utf8',
);
writeFileSync(join(workspaceDir, 'README.md'), '# Smoke workspace\n', 'utf8');

const run = (command, args, options = {}) => {
	const { env: extraEnv, ...restOptions } = options;
	const result = spawnSync(command, args, {
		cwd: projectRoot,
		encoding: 'utf8',
		env: { ...process.env, ...extraEnv },
		...restOptions,
	});
	if (result.status !== 0) {
		throw new Error(
			[
				`Command failed: ${command} ${args.join(' ')}`,
				result.stdout?.trim(),
				result.stderr?.trim(),
			]
				.filter(Boolean)
				.join('\n'),
		);
	}
	return result.stdout.trim();
};

const tryRun = (command, args, options = {}) => {
	const { env: extraEnv, ...restOptions } = options;
	const result = spawnSync(command, args, {
		cwd: projectRoot,
		encoding: 'utf8',
		env: { ...process.env, ...extraEnv },
		...restOptions,
	});

	return {
		status: result.status ?? 1,
		signal: result.signal ?? '',
		stdout: result.stdout?.trim() ?? '',
		stderr: result.stderr?.trim() ?? '',
		error: result.error ? String(result.error) : '',
	};
};

const waitForHealth = async () => {
	const timeoutMs = 15000;
	const startedAt = Date.now();
	while (Date.now() - startedAt < timeoutMs) {
		try {
			const response = await fetch(`${baseUrl}/health`);
			if (response.ok) {
				return;
			}
		} catch {}
		await new Promise((resolvePromise) => setTimeout(resolvePromise, 500));
	}
	throw new Error(`Timed out waiting for ${baseUrl}/health`);
};

const parseCliJson = (stdout, label) => {
	try {
		return JSON.parse(stdout);
	} catch (error) {
		throw new Error(
			`${label} did not return JSON.\n${stdout}\n${
				error instanceof Error ? error.message : String(error)
			}`,
		);
	}
};

const inspectorCli = (args) =>
	tryRun(inspectorCliCommand, [...inspectorCliBaseArgs, ...args], {
		env: {
			MCP_AUTO_OPEN_ENABLED: 'false',
		},
	});

const callRemote = async (args, label) => {
	let lastFailure = '';

	for (let attempt = 1; attempt <= 5; attempt += 1) {
		const result = inspectorCli([mcpUrl, '--transport', 'http', ...args]);
		if (result.status === 0) {
			return parseCliJson(result.stdout, label);
		}

		lastFailure = [
			`${label} attempt ${attempt} failed`,
			result.signal ? `signal=${result.signal}` : '',
			result.error,
			result.stdout,
			result.stderr,
		]
			.filter(Boolean)
			.join('\n');

		await new Promise((resolvePromise) => setTimeout(resolvePromise, 500));
	}

	throw new Error(lastFailure || `${label} failed without output`);
};

const extractStructuredContent = (result) =>
	result.result?.structuredContent ?? result.structuredContent;

const pollStatusUntilTerminal = async (sessionId, labelPrefix) => {
	const statusSteps = [];
	let finalStatus;
	for (let attempt = 1; attempt <= 20; attempt += 1) {
		const status = await callRemote(
			[
				'--method',
				'tools/call',
				'--tool-name',
				'agent',
				'--tool-arg',
				'action=status',
				'--tool-arg',
				`sessionId=${sessionId}`,
			],
			`${labelPrefix}.status#${attempt}`,
		);
		statusSteps.push(status);
		const structured = extractStructuredContent(status);
		if (
			structured?.status &&
			!['starting', 'running'].includes(structured.status)
		) {
			finalStatus = structured;
			break;
		}
		await new Promise((resolvePromise) => setTimeout(resolvePromise, 1000));
	}

	if (!finalStatus) {
		throw new Error(
			`${labelPrefix} did not reach a terminal state within 20 polls`,
		);
	}

	return { statusSteps, finalStatus };
};

run('pnpm', ['--filter', '@pier/server', 'build']);

const serverLogHandle = spawn('node', ['server/dist/index.js'], {
	cwd: projectRoot,
	env: {
		...process.env,
		PORT: String(port),
		HOST: host,
	},
	stdio: ['ignore', 'pipe', 'pipe'],
});

let cleaned = false;
const cleanup = () => {
	if (cleaned) {
		return;
	}
	cleaned = true;
	serverLogHandle.kill('SIGTERM');
};

process.on('exit', cleanup);
process.on('SIGINT', () => {
	cleanup();
	process.exit(130);
});
process.on('SIGTERM', () => {
	cleanup();
	process.exit(143);
});

let serverLog = '';
serverLogHandle.stdout.on('data', (chunk) => {
	serverLog += chunk.toString();
});
serverLogHandle.stderr.on('data', (chunk) => {
	serverLog += chunk.toString();
});

try {
	await waitForHealth();

	const tools = await callRemote(['--method', 'tools/list'], 'tools/list');
	const models = await callRemote(
		['--method', 'tools/call', '--tool-name', 'list_models'],
		'list_models',
	);
	const agents = await callRemote(
		[
			'--method',
			'tools/call',
			'--tool-name',
			'list_agents',
			'--tool-arg',
			`cwd=${workspaceDir}`,
		],
		'list_agents',
	);
	const start = await callRemote(
		[
			'--method',
			'tools/call',
			'--tool-name',
			'agent',
			'--tool-arg',
			'action=start',
			'--tool-arg',
			'agentId=smoke-review',
			'--tool-arg',
			'task=Reply with exactly OK.',
			'--tool-arg',
			`cwd=${workspaceDir}`,
		],
		'agent.start',
	);

	const startStructured = extractStructuredContent(start);
	const piSessionId = startStructured?.sessionId;
	if (!piSessionId) {
		throw new Error(
			`agent.start did not return sessionId.\n${JSON.stringify(start, null, 2)}`,
		);
	}
	if (startStructured?.model !== 'agnes-ai/agnes-2.0-flash') {
		throw new Error(
			`agent.start did not return effective model.\n${JSON.stringify(start, null, 2)}`,
		);
	}
	if (startStructured?.thinking !== 'off') {
		throw new Error(
			`agent.start did not return effective thinking.\n${JSON.stringify(start, null, 2)}`,
		);
	}

	const initialRun = await pollStatusUntilTerminal(
		piSessionId,
		'agent.initial',
	);

	const followUp = await callRemote(
		[
			'--method',
			'tools/call',
			'--tool-name',
			'agent',
			'--tool-arg',
			'action=follow_up',
			'--tool-arg',
			`sessionId=${piSessionId}`,
			'--tool-arg',
			'task=Reply with exactly FOLLOW_UP_OK.',
		],
		'agent.follow_up',
	);
	const followUpStructured = extractStructuredContent(followUp);
	if (followUpStructured?.status !== 'running') {
		throw new Error(
			`follow_up did not resume the session.\n${JSON.stringify(followUp, null, 2)}`,
		);
	}
	const followUpRun = await pollStatusUntilTerminal(
		piSessionId,
		'agent.follow_up',
	);

	const slowStart = await callRemote(
		[
			'--method',
			'tools/call',
			'--tool-name',
			'agent',
			'--tool-arg',
			'action=start',
			'--tool-arg',
			'agentId=slow-review',
			'--tool-arg',
			'task=Wait first, then reply with exactly SLOW_OK.',
			'--tool-arg',
			`cwd=${workspaceDir}`,
		],
		'agent.slow_start',
	);
	const slowStartStructured = extractStructuredContent(slowStart);
	const slowSessionId = slowStartStructured?.sessionId;
	if (!slowSessionId) {
		throw new Error(
			`slow agent start did not return sessionId.\n${JSON.stringify(slowStart, null, 2)}`,
		);
	}

	await new Promise((resolvePromise) => setTimeout(resolvePromise, 1000));
	const steer = await callRemote(
		[
			'--method',
			'tools/call',
			'--tool-name',
			'agent',
			'--tool-arg',
			'action=steer',
			'--tool-arg',
			`sessionId=${slowSessionId}`,
			'--tool-arg',
			'task=When you do reply, reply with exactly STEER_OK.',
		],
		'agent.steer',
	);
	const steerStructured = extractStructuredContent(steer);
	if (steerStructured?.status !== 'running') {
		throw new Error(
			`steer did not keep the session running.\n${JSON.stringify(steer, null, 2)}`,
		);
	}
	const steerRun = await pollStatusUntilTerminal(slowSessionId, 'agent.steer');

	const abortStart = await callRemote(
		[
			'--method',
			'tools/call',
			'--tool-name',
			'agent',
			'--tool-arg',
			'action=start',
			'--tool-arg',
			'agentId=slow-review',
			'--tool-arg',
			'task=Wait first, then reply with exactly SHOULD_ABORT.',
			'--tool-arg',
			`cwd=${workspaceDir}`,
		],
		'agent.abort_start',
	);
	const abortStartStructured = extractStructuredContent(abortStart);
	const abortSessionId = abortStartStructured?.sessionId;
	if (!abortSessionId) {
		throw new Error(
			`abort target start did not return sessionId.\n${JSON.stringify(abortStart, null, 2)}`,
		);
	}

	await new Promise((resolvePromise) => setTimeout(resolvePromise, 1000));
	const abort = await callRemote(
		[
			'--method',
			'tools/call',
			'--tool-name',
			'agent',
			'--tool-arg',
			'action=abort',
			'--tool-arg',
			`sessionId=${abortSessionId}`,
		],
		'agent.abort',
	);
	const abortStructured = extractStructuredContent(abort);
	if (abortStructured?.status !== 'aborted') {
		throw new Error(
			`abort did not return aborted.\n${JSON.stringify(abort, null, 2)}`,
		);
	}

	const abortStatus = await callRemote(
		[
			'--method',
			'tools/call',
			'--tool-name',
			'agent',
			'--tool-arg',
			'action=status',
			'--tool-arg',
			`sessionId=${abortSessionId}`,
		],
		'agent.abort.status',
	);
	const abortFinal = extractStructuredContent(abortStatus);
	if (abortFinal?.status !== 'aborted') {
		throw new Error(
			`aborted session status did not persist.\n${JSON.stringify(abortStatus, null, 2)}`,
		);
	}

	const finalStatus = followUpRun.finalStatus;
	const statusSteps = [
		...initialRun.statusSteps,
		...followUpRun.statusSteps,
		steer,
		...steerRun.statusSteps,
		abort,
		abortStatus,
	];

	const reportLines = [
		'# MCP Inspector Smoke Report',
		'',
		`- Date: \`${new Date().toISOString()}\``,
		`- Base URL: \`${baseUrl}\``,
		`- MCP URL: \`${mcpUrl}\``,
		`- Workspace: \`${workspaceDir}\``,
		`- Pi session: \`${piSessionId}\``,
		`- Steer session: \`${slowSessionId}\``,
		`- Abort session: \`${abortSessionId}\``,
		`- Final status: \`${finalStatus.status}\``,
		`- Result: \`${finalStatus.result ?? ''}\``,
		'',
		'## tools/list',
		'',
		'```json',
		JSON.stringify(tools, null, 2),
		'```',
		'',
		'## list_models',
		'',
		'```json',
		JSON.stringify(models, null, 2),
		'```',
		'',
		'## list_agents',
		'',
		'```json',
		JSON.stringify(agents, null, 2),
		'```',
		'',
		'## agent.start',
		'',
		'```json',
		JSON.stringify(start, null, 2),
		'```',
		'',
		'## agent.follow_up',
		'',
		'```json',
		JSON.stringify(followUp, null, 2),
		'```',
		'',
		'## agent.steer',
		'',
		'```json',
		JSON.stringify(steer, null, 2),
		'```',
		'',
		'## agent.abort',
		'',
		'```json',
		JSON.stringify(abort, null, 2),
		'```',
		'',
		'## agent.status flows',
		'',
		'```json',
		JSON.stringify(statusSteps, null, 2),
		'```',
		'',
		'## Server Log',
		'',
		'```text',
		serverLog.trim(),
		'```',
		'',
	];

	writeFileSync(reportPath, reportLines.join('\n'), 'utf8');
	console.log(
		JSON.stringify(
			{
				ok: true,
				reportPath,
				finalStatus: finalStatus.status,
				piSessionId,
			},
			null,
			2,
		),
	);
} catch (error) {
	writeFileSync(
		reportPath,
		[
			'# MCP Inspector Smoke Report',
			'',
			'## Failure',
			'',
			'```text',
			error instanceof Error ? (error.stack ?? error.message) : String(error),
			'```',
			'',
			'## Server Log',
			'',
			'```text',
			serverLog.trim(),
			'```',
			'',
		].join('\n'),
		'utf8',
	);
	throw error;
} finally {
	cleanup();
}
