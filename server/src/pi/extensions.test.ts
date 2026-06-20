import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ExtensionNotFoundError, resolveExtensionPath } from './extensions.js';

function writeFile(path: string, content: string): void {
	mkdirSync(dirname(path), { recursive: true });
	writeFileSync(path, content);
}

describe('resolveExtensionPath', () => {
	it('returns absolute path as-is when the file exists', () => {
		const root = mkdtempSync(join(tmpdir(), 'pier-ext-'));
		const file = join(root, 'custom.ts');
		writeFile(file, '// ok');

		expect(resolveExtensionPath(file, { piAgentDir: root })).toBe(file);
	});

	it('resolves relative path against cwd', () => {
		const root = mkdtempSync(join(tmpdir(), 'pier-ext-'));
		const file = join(root, 'rel.ts');
		writeFile(file, '// ok');

		expect(
			resolveExtensionPath('./rel.ts', {
				piAgentDir: join(tmpdir(), 'nowhere'),
				cwd: root,
			}),
		).toBe(file);
	});

	it('resolves npm package via index.ts when package.json has no main', () => {
		const root = mkdtempSync(join(tmpdir(), 'pier-ext-'));
		const pkgDir = join(root, 'npm/node_modules', 'pi-mcp-adapter');
		writeFile(join(pkgDir, 'package.json'), '{"name":"pi-mcp-adapter"}');
		writeFile(join(pkgDir, 'index.ts'), '// entry');

		const resolved = resolveExtensionPath('pi-mcp-adapter', {
			piAgentDir: root,
		});
		expect(resolved).toBe(join(pkgDir, 'index.ts'));
	});

	it('resolves npm package via package.json module field when present', () => {
		const root = mkdtempSync(join(tmpdir(), 'pier-ext-'));
		const pkgDir = join(root, 'npm/node_modules', 'with-main');
		writeFile(
			join(pkgDir, 'package.json'),
			'{"name":"with-main","main":"./dist/entry.ts"}',
		);
		writeFile(join(pkgDir, 'dist', 'entry.ts'), '// entry');

		const resolved = resolveExtensionPath('with-main', {
			piAgentDir: root,
		});
		expect(resolved).toBe(join(pkgDir, 'dist/entry.ts'));
	});

	it('resolves loose .ts file under extensions/', () => {
		const root = mkdtempSync(join(tmpdir(), 'pier-ext-'));
		writeFile(join(root, 'extensions', 'kooky.ts'), '// ok');

		expect(resolveExtensionPath('kooky', { piAgentDir: root })).toBe(
			join(root, 'extensions/kooky.ts'),
		);
	});

	it('throws ExtensionNotFoundError with code when nothing matches', () => {
		const root = mkdtempSync(join(tmpdir(), 'pier-ext-'));

		try {
			resolveExtensionPath('nope', { piAgentDir: root });
			throw new Error('should have thrown');
		} catch (error) {
			expect(error).toBeInstanceOf(ExtensionNotFoundError);
			expect((error as ExtensionNotFoundError).code).toBe(
				'EXTENSION_NOT_FOUND',
			);
			expect((error as ExtensionNotFoundError).extensionName).toBe('nope');
		}
	});

	it('rejects absolute path that does not exist', () => {
		const root = mkdtempSync(join(tmpdir(), 'pier-ext-'));
		const missing = join(root, 'gone.ts');

		try {
			resolveExtensionPath(missing, { piAgentDir: root });
			throw new Error('should have thrown');
		} catch (error) {
			expect(error).toBeInstanceOf(ExtensionNotFoundError);
		}
	});
});
