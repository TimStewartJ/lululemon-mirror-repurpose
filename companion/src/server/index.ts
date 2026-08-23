import { loadEnv } from './env';
import { createApp } from './app';
import { assertAccessTokenConfigured, MissingAccessTokenError } from './security';
import { logger } from './logger';

const env = loadEnv();

try {
  assertAccessTokenConfigured(env);
} catch (error) {
  if (error instanceof MissingAccessTokenError) {
    logger.error(error.message);
    process.exit(1);
  }
  throw error;
}

const { app } = createApp(env);

app.listen(env.port, env.host, () => {
  logger.info(`companion listening on http://${env.host}:${env.port}`);
});
