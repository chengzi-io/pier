import { serve } from '@hono/node-server';
import { createApp } from './app.js';
import { loadConfigFromEnv } from './config.js';

const port = Number(process.env.PORT ?? 8787);
const config = loadConfigFromEnv();
const app = createApp(config);

serve(
	{
		fetch: app.fetch,
		port,
	},
	(info) => {
		console.log(`pier server listening on http://localhost:${info.port}`);
	},
);
