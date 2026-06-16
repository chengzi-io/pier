import { describe, expect, it } from 'vitest';
import { pierConfigSchema } from './config.js';

describe('pierConfigSchema', () => {
	it('accepts empty agents', () => {
		expect(pierConfigSchema.parse({})).toEqual({ agents: [] });
	});
});
