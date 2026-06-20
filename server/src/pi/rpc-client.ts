import { spawn } from 'node:child_process';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { attachJsonlLineReader } from './jsonl-reader.js';
import { serializeJsonLine } from './jsonl-writer.js';
import {
	type PiRpcCommand,
	type PiRpcEvent,
	type PiRpcResponse,
	isPiRpcResponse,
} from './rpc-types.js';

export interface PiRpcClient {
	process: ChildProcessWithoutNullStreams;
	send(command: PiRpcCommand): Promise<PiRpcResponse>;
	stop(): Promise<void>;
	readonly lastAssistantText: string | undefined;
}

export interface CreatePiRpcClientOptions {
	args: string[];
	cwd?: string;
	command?: string;
	env?: NodeJS.ProcessEnv;
	onEvent: (event: PiRpcEvent) => void;
	onExit: (error: Error) => void;
}

export function createPiRpcClient(
	options: CreatePiRpcClientOptions,
): PiRpcClient {
	const child = spawn(options.command ?? 'pi', options.args, {
		cwd: options.cwd,
		env: options.env ?? process.env,
		stdio: ['pipe', 'pipe', 'pipe'],
	});

	let stderr = '';
	child.stderr.on('data', (chunk) => {
		stderr += chunk.toString();
	});

	const pending = new Map<
		string,
		{
			resolve: (value: PiRpcResponse) => void;
			reject: (error: Error) => void;
		}
	>();
	let requestId = 0;
	let closed = false;
	let lastAssistantText: string | undefined;

	const stopReadingStdout = attachJsonlLineReader(child.stdout, (line) => {
		let parsed: PiRpcResponse | PiRpcEvent;
		try {
			parsed = JSON.parse(line) as PiRpcResponse | PiRpcEvent;
		} catch (error) {
			rejectAll(
				new Error(
					`Pi process emitted invalid JSONL: ${
						error instanceof Error ? error.message : String(error)
					}`,
				),
			);
			return;
		}

		if (isPiRpcResponse(parsed)) {
			if (parsed.id) {
				const pendingRequest = pending.get(parsed.id);
				if (pendingRequest) {
					pending.delete(parsed.id);
					pendingRequest.resolve(parsed);
				}
			}
			return;
		}

		if (parsed.type === 'message_end') {
			const maybeMessage = parsed.message as
				| { role?: string; content?: Array<{ type?: string; text?: string }> }
				| undefined;
			if (maybeMessage?.role === 'assistant') {
				const text = maybeMessage.content
					?.filter(
						(part) => part.type === 'text' && typeof part.text === 'string',
					)
					.map((part) => part.text ?? '')
					.join('');
				lastAssistantText = summarizeText(text);
			}
		}

		options.onEvent(parsed);
	});

	const rejectAll = (error: Error) => {
		if (closed) {
			return;
		}
		closed = true;
		options.onExit(error);
		for (const request of pending.values()) {
			request.reject(error);
		}
		pending.clear();
	};

	child.once('error', (error) => {
		rejectAll(
			new Error(
				`Pi process error: ${error.message}${stderr ? `\n${stderr}` : ''}`,
			),
		);
	});

	child.once('exit', (code, signal) => {
		const suffix = stderr ? `\n${stderr}` : '';
		rejectAll(
			new Error(
				`Pi process exited with ${signal ? `signal ${signal}` : `code ${code ?? 'unknown'}`}${suffix}`,
			),
		);
	});

	const send = async (command: PiRpcCommand): Promise<PiRpcResponse> => {
		if (closed || child.killed || child.exitCode !== null) {
			throw new Error('Pi session process is not running');
		}

		const id = `rpc-${++requestId}`;
		const payload = { ...command, id };
		return await new Promise<PiRpcResponse>((resolve, reject) => {
			pending.set(id, { resolve, reject });
			child.stdin.write(serializeJsonLine(payload), (error) => {
				if (error) {
					pending.delete(id);
					reject(error instanceof Error ? error : new Error(String(error)));
				}
			});
		});
	};

	const stop = async (): Promise<void> => {
		stopReadingStdout();
		if (child.exitCode !== null || child.killed) {
			return;
		}

		child.kill('SIGTERM');
		await new Promise<void>((resolve) => {
			const timeout = setTimeout(() => {
				child.kill('SIGKILL');
				resolve();
			}, 1000);
			child.once('exit', () => {
				clearTimeout(timeout);
				resolve();
			});
		});
	};

	return {
		process: child,
		send,
		stop,
		get lastAssistantText() {
			return lastAssistantText;
		},
	};
}

function summarizeText(value: string | undefined): string | undefined {
	if (!value) {
		return undefined;
	}

	const trimmed = value.trim();
	if (!trimmed) {
		return undefined;
	}

	return trimmed.length > 240 ? `${trimmed.slice(0, 237)}...` : trimmed;
}
