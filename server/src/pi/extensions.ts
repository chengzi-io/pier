import { existsSync, readFileSync, statSync } from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import { PierError } from '../errors.js';

export interface ResolveExtensionPathOptions {
	piAgentDir: string;
	cwd?: string;
}

export class ExtensionNotFoundError extends PierError {
	constructor(
		public readonly extensionName: string,
		message: string,
	) {
		super('EXTENSION_NOT_FOUND', message);
		this.name = 'ExtensionNotFoundError';
	}
}

export function resolveExtensionPath(
	extension: string,
	options: ResolveExtensionPathOptions,
): string {
	const trimmed = extension.trim();
	if (!trimmed) {
		throw new ExtensionNotFoundError(extension, 'Empty extension reference');
	}

	if (isAbsolute(trimmed)) {
		assertExtensionFile(trimmed, extension);
		return trimmed;
	}

	if (trimmed.startsWith('./') || trimmed.startsWith('../')) {
		const base = options.cwd ?? process.cwd();
		const resolved = resolve(base, trimmed);
		assertExtensionFile(resolved, extension);
		return resolved;
	}

	const fromNpm = resolveNpmExtension(trimmed, options.piAgentDir);
	if (fromNpm) {
		return fromNpm;
	}

	const fromDir = resolveDirExtension(trimmed, options.piAgentDir);
	if (fromDir) {
		return fromDir;
	}

	throw new ExtensionNotFoundError(
		trimmed,
		`Extension "${trimmed}" was not found. Looked for:\n  ${join(
			options.piAgentDir,
			'npm/node_modules',
			trimmed,
			'index.ts',
		)}\n  ${join(options.piAgentDir, 'extensions', `${trimmed}.ts`)}\n  ${join(
			options.piAgentDir,
			'extensions',
			trimmed,
		)}`,
	);
}

function resolveNpmExtension(
	name: string,
	piAgentDir: string,
): string | undefined {
	const packageDir = join(piAgentDir, 'npm/node_modules', name);
	if (!existsSync(packageDir)) {
		return undefined;
	}
	const pkgJsonPath = join(packageDir, 'package.json');
	if (existsSync(pkgJsonPath)) {
		try {
			const pkg = JSON.parse(readFileSync(pkgJsonPath, 'utf8')) as {
				main?: string;
				module?: string;
				exports?: unknown;
			};
			let candidate: string | undefined;
			if (typeof pkg.module === 'string') {
				candidate = pkg.module;
			} else if (typeof pkg.main === 'string') {
				candidate = pkg.main;
			} else if (typeof pkg.exports === 'string') {
				candidate = pkg.exports;
			} else if (
				pkg.exports &&
				typeof pkg.exports === 'object' &&
				'.' in pkg.exports
			) {
				const dotExport = (pkg.exports as Record<string, unknown>)['.'];
				if (typeof dotExport === 'string') {
					candidate = dotExport;
				} else if (
					dotExport &&
					typeof dotExport === 'object' &&
					typeof (dotExport as { default?: unknown }).default === 'string'
				) {
					candidate = (dotExport as { default: string }).default;
				}
			}
			if (candidate) {
				const entry = join(packageDir, candidate);
				if (existsSync(entry)) {
					return entry;
				}
			}
		} catch {
			// fall through to default index.ts
		}
	}

	const defaultEntry = join(packageDir, 'index.ts');
	return existsSync(defaultEntry) ? defaultEntry : undefined;
}

function resolveDirExtension(
	name: string,
	piAgentDir: string,
): string | undefined {
	const fileCandidate = join(piAgentDir, 'extensions', `${name}.ts`);
	if (existsSync(fileCandidate)) {
		return fileCandidate;
	}
	const dirCandidate = join(piAgentDir, 'extensions', name);
	if (existsSync(dirCandidate)) {
		try {
			if (statSync(dirCandidate).isDirectory()) {
				const indexTs = join(dirCandidate, 'index.ts');
				if (existsSync(indexTs)) {
					return indexTs;
				}
			}
		} catch {
			return undefined;
		}
	}
	return undefined;
}

function assertExtensionFile(resolved: string, original: string): void {
	if (!existsSync(resolved)) {
		throw new ExtensionNotFoundError(
			original,
			`Extension path does not exist: ${resolved}`,
		);
	}
}
