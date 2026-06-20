import { describe, expect, it, vi } from 'vitest';
import { SessionStore } from './session-store.js';

describe('SessionStore', () => {
	it('stores and updates sessions while emitting lifecycle events', () => {
		const store = new SessionStore();
		const listener = vi.fn();
		store.subscribe(listener);

		store.create({
			sessionId: 'session-1',
			agentId: 'review',
			status: 'starting',
			updatedAt: '2026-06-18T00:00:00.000Z',
		});
		store.update('session-1', { status: 'running' });
		store.markTerminal('session-1', 'completed', {
			lastAssistantTextPreview: 'done',
		});

		const session = store.get('session-1');
		expect(session).toMatchObject({
			sessionId: 'session-1',
			agentId: 'review',
			status: 'completed',
			lastAssistantTextPreview: 'done',
		});
		expect(listener).toHaveBeenCalledTimes(3);
		expect(listener).toHaveBeenLastCalledWith(
			expect.objectContaining({
				sessionId: 'session-1',
				status: 'completed',
				lastAssistantTextPreview: 'done',
			}),
		);
	});
});
