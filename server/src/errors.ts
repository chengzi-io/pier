export type PierErrorCode =
	| 'UNKNOWN_AGENT'
	| 'UNKNOWN_SESSION'
	| 'SESSION_NOT_RUNNING'
	| 'SESSION_NOT_RESUMABLE'
	| 'MODEL_NOT_FOUND'
	| 'EXTENSION_NOT_FOUND'
	| 'VALIDATION_FAILED'
	| 'INTERNAL_ERROR';

export class PierError extends Error {
	readonly code: PierErrorCode;
	constructor(code: PierErrorCode, message: string) {
		super(message);
		this.name = 'PierError';
		this.code = code;
	}
}

export function isPierError(value: unknown): value is PierError {
	return value instanceof PierError;
}
