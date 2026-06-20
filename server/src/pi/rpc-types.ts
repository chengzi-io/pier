export type PiRpcCommand =
	| { id?: string; type: 'prompt'; message: string }
	| { id?: string; type: 'get_state' }
	| { id?: string; type: 'get_available_models' }
	| { id?: string; type: 'set_auto_compaction'; enabled: boolean }
	| { id?: string; type: 'set_auto_retry'; enabled: boolean }
	| { id?: string; type: 'follow_up'; message: string }
	| { id?: string; type: 'steer'; message: string }
	| { id?: string; type: 'abort' };

export interface PiRpcSuccessResponse {
	id?: string;
	type: 'response';
	command: string;
	success: true;
	data?: Record<string, unknown>;
}

export interface PiRpcErrorResponse {
	id?: string;
	type: 'response';
	command: string;
	success: false;
	error: string;
}

export type PiRpcResponse = PiRpcSuccessResponse | PiRpcErrorResponse;

export interface PiRpcEvent extends Record<string, unknown> {
	type: string;
}

export function isPiRpcResponse(
	value: PiRpcResponse | PiRpcEvent,
): value is PiRpcResponse {
	return value.type === 'response';
}

export interface PiModelSummary extends Record<string, unknown> {
	provider?: string;
	modelId?: string;
	name?: string;
	displayName?: string;
}
