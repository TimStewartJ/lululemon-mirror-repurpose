import path from 'node:path';
import express, { type Express } from 'express';
import { CompanionEnv } from './env';
import { ConfigStore } from './config-store';
import { DeviceManager } from './device-manager';
import { MediaLibrary } from './media/media-library';
import { createDevicesRouter } from './routes/devices-router';
import { createDeviceProxyRouter } from './routes/device-proxy-router';
import { createMediaRouter, createMediaFilesRouter } from './routes/media-router';
import { createAccessTokenMiddleware } from './security';
import { logger } from './logger';

export interface AppContext {
  app: Express;
  deviceManager: DeviceManager;
  configStore: ConfigStore;
  mediaLibrary: MediaLibrary;
}

export function createApp(env: CompanionEnv): AppContext {
  const app = express();
  app.disable('x-powered-by');
  // Note: no global body parser here. Body parsing is scoped per-router
  // (devices-router, device-proxy-router) so the media router's raw
  // upload stream is never at risk of being consumed by a parser mounted
  // ahead of it. See routes/media-router.ts for details.

  const configStore = new ConfigStore(env.dataDir);
  const deviceManager = new DeviceManager(
    configStore,
    env.adbForwardPort,
    env.deviceApiPort,
    env.adbPath,
    env.deviceMediaPort,
  );
  const mediaLibrary = new MediaLibrary(env.mediaRoot);

  // Guards every companion/device/media route. A no-op passthrough when no
  // COMPANION_ACCESS_TOKEN is configured (the default loopback-only setup);
  // enforced once the companion is bound to a LAN-reachable host (see
  // security.ts / assertAccessTokenConfigured, called at startup in
  // index.ts before the server ever starts listening).
  const accessTokenMiddleware = createAccessTokenMiddleware(env.accessToken);
  app.use('/api/device', accessTokenMiddleware);
  app.use('/api/companion', accessTokenMiddleware);
  app.use('/media-files', accessTokenMiddleware);

  app.use('/api/companion', createDevicesRouter(deviceManager));
  app.use('/api/device', createDeviceProxyRouter(deviceManager, configStore));
  app.use('/api/companion', createMediaRouter(mediaLibrary, deviceManager, {
    companionPort: env.port,
    publicUrl: env.publicUrl,
    accessToken: env.accessToken,
  }));
  app.use('/', createMediaFilesRouter(mediaLibrary));

  const clientDir = path.join(__dirname, '..', 'client');
  app.use(express.static(clientDir));

  app.use((req, res) => {
    res.status(404).json({ error: 'Not found', path: req.path });
  });

  // Express error handler signature requires 4 args even when `next` is unused.
  app.use((error: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    logger.error('unhandled request error', { message: (error as Error)?.message });
    res.status(500).json({ error: 'Internal companion error' });
  });

  return { app, deviceManager, configStore, mediaLibrary };
}
