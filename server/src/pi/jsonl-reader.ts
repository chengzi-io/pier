export function attachJsonlLineReader(
	stream: NodeJS.ReadableStream,
	onLine: (line: string) => void,
): () => void {
	let buffer = '';

	const onData = (chunk: Buffer | string) => {
		buffer += chunk.toString();
		while (true) {
			const newlineIndex = buffer.indexOf('\n');
			if (newlineIndex === -1) {
				return;
			}

			let line = buffer.slice(0, newlineIndex);
			buffer = buffer.slice(newlineIndex + 1);
			if (line.endsWith('\r')) {
				line = line.slice(0, -1);
			}
			if (line.length > 0) {
				onLine(line);
			}
		}
	};

	stream.on('data', onData);
	return () => {
		stream.off('data', onData);
	};
}
