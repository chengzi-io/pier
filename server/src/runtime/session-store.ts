import type {
	SessionStatus,
	SessionStatusChangedEvent,
	SessionSummary,
} from './types.js';

export interface MutableSessionRecord extends SessionSummary {
	pid?: number;
}

export class SessionStore {
	#sessions = new Map<string, MutableSessionRecord>();
	#listeners = new Set<(event: SessionStatusChangedEvent) => void>();

	create(record: MutableSessionRecord): MutableSessionRecord {
		this.#sessions.set(record.sessionId, { ...record });
		this.#emit(record);
		return { ...record };
	}

	update(
		sessionId: string,
		patch: Partial<Omit<MutableSessionRecord, 'sessionId' | 'agentId'>>,
	): MutableSessionRecord | undefined {
		const current = this.#sessions.get(sessionId);
		if (!current) {
			return undefined;
		}
		const next: MutableSessionRecord = {
			...current,
			...patch,
			updatedAt:
				typeof patch.updatedAt === 'string'
					? patch.updatedAt
					: new Date().toISOString(),
		};
		this.#sessions.set(sessionId, next);
		this.#emit(next);
		return { ...next };
	}

	get(sessionId: string): MutableSessionRecord | undefined {
		const current = this.#sessions.get(sessionId);
		return current ? { ...current } : undefined;
	}

	list(): MutableSessionRecord[] {
		return [...this.#sessions.values()].map((session) => ({ ...session }));
	}

	subscribe(listener: (event: SessionStatusChangedEvent) => void): () => void {
		this.#listeners.add(listener);
		return () => {
			this.#listeners.delete(listener);
		};
	}

	markTerminal(
		sessionId: string,
		status: Extract<
			SessionStatus,
			'completed' | 'error' | 'aborted' | 'stopped'
		>,
		patch?: Partial<
			Omit<MutableSessionRecord, 'sessionId' | 'agentId' | 'status'>
		>,
	): MutableSessionRecord | undefined {
		return this.update(sessionId, { ...patch, status });
	}

	#emit(session: MutableSessionRecord): void {
		const event: SessionStatusChangedEvent = {
			sessionId: session.sessionId,
			agentId: session.agentId,
			status: session.status,
			updatedAt: session.updatedAt,
			sessionFile: session.sessionFile,
			lastAssistantTextPreview: session.lastAssistantTextPreview,
			error: session.error,
		};
		for (const listener of this.#listeners) {
			listener(event);
		}
	}
}
