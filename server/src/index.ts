import { serve } from '@hono/node-server';
import { createApp } from './app.js';
import { loadConfigFromEnv } from './config.js';

const port = Number(process.env.PORT ?? 8787);
const hostname = process.env.HOST ?? '127.0.0.1';
const config = loadConfigFromEnv();
const app = createApp(config);

serve(
	{
		fetch: app.fetch,
		port,
		hostname,
	},
	(info) => {
		console.log(`pier server listening on http://${hostname}:${info.port}`);
	},
);
