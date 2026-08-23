import express, { Router } from 'express';
import type { DeviceManagerPort } from '../device-manager';
import { ConfigStore } from '../config-store';
import { DeviceApiError } from '../proxy-client';
import { logger } from '../logger';

/**
 * Same-origin proxy for the Mirror Home device control API. The device API
 * intentionally has no CORS headers, so the browser UI talks to these
 * companion endpoints instead of the device directly; the companion
 * attaches the bearer token server-side. Nothing here ever logs a token or
 * a Wi-Fi passphrase, and neither is ever placed in a URL.
 */
export function createDeviceProxyRouter(deviceManager: DeviceManagerPort, configStore: ConfigStore): Router {
  const router = Router();
  // Scoped locally (not globally in app.ts) so the media router's raw
  // upload stream is never at risk of being consumed by a body parser
  // mounted ahead of it.
  router.use(express.json({ limit: '256kb' }));

  function handleError(res: import('express').Response, error: unknown): void {
    if (error instanceof DeviceApiError) {
      res.status(error.status >= 400 ? error.status : 502).json({ error: error.message });
      return;
    }
    logger.warn('device proxy error', { message: (error as Error).message });
    res.status(502).json({ error: (error as Error).message });
  }

  router.get('/status', async (_req, res) => {
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.getStatus();
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/pair', async (req, res) => {
    const code = typeof req.body?.code === 'string' ? req.body.code : undefined;
    if (!code) {
      res.status(400).json({ error: 'code is required' });
      return;
    }
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.pair(code);
      if (status === 200 && body && typeof (body as { token?: unknown }).token === 'string') {
        configStore.setToken((body as { token: string }).token);
        res.status(200).json({ paired: true });
        return;
      }
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/pair/revoke', async (_req, res) => {
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.revokePairing();
      configStore.setToken(null);
      res.status(status).json(body);
    } catch (error) {
      // Even if the device call fails (e.g. already unreachable), clear
      // the local token so the UI doesn't get stuck believing it's paired.
      configStore.setToken(null);
      handleError(res, error);
    }
  });

  router.get('/dashboard', async (_req, res) => {
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.getDashboard();
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.put('/dashboard', async (req, res) => {
    const url = typeof req.body?.url === 'string' ? req.body.url : undefined;
    if (url === undefined) {
      res.status(400).json({ error: 'url is required' });
      return;
    }
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.setDashboard(url);
      if (status === 200) configStore.setDashboardUrl(url);
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/wifi/configure', async (req, res) => {
    const ssid = typeof req.body?.ssid === 'string' ? req.body.ssid : undefined;
    const passphrase = typeof req.body?.passphrase === 'string' ? req.body.passphrase : '';
    const hidden = Boolean(req.body?.hidden);
    if (!ssid) {
      res.status(400).json({ error: 'ssid is required' });
      return;
    }
    try {
      const client = deviceManager.getClient();
      // passphrase is forwarded to the device and never written to disk,
      // logged, or echoed back in any response here.
      const { status, body } = await client.configureWifi(ssid, passphrase, hidden);
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/control/brightness', async (req, res) => {
    const value = typeof req.body?.value === 'number' ? req.body.value : undefined;
    if (value === undefined) {
      res.status(400).json({ error: 'value is required' });
      return;
    }
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.setBrightness(value);
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/control/name', async (req, res) => {
    const name = typeof req.body?.name === 'string' ? req.body.name : undefined;
    if (!name) {
      res.status(400).json({ error: 'name is required' });
      return;
    }
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.setName(name);
      if (status === 200) configStore.setDisplayName(name);
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.get('/system', async (_req, res) => {
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.getSystemStatus();
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/system/prepare-kiosk', async (_req, res) => {
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.prepareKiosk();
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/system/home', async (req, res) => {
    const enabled = typeof req.body?.enabled === 'boolean' ? req.body.enabled : undefined;
    if (enabled === undefined) {
      res.status(400).json({ error: 'enabled (boolean) is required' });
      return;
    }
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.setSystemHome(enabled);
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.get('/media/status', async (_req, res) => {
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.getMediaStatus();
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/media/play', async (req, res) => {
    const url = typeof req.body?.url === 'string' ? req.body.url : undefined;
    if (!url) {
      res.status(400).json({ error: 'url is required' });
      return;
    }
    const mimeType = typeof req.body?.mimeType === 'string' ? req.body.mimeType : undefined;
    const title = typeof req.body?.title === 'string' ? req.body.title : undefined;
    const time = typeof req.body?.time === 'number' ? req.body.time : undefined;
    const volume = typeof req.body?.volume === 'number' ? req.body.volume : undefined;
    const speed = typeof req.body?.speed === 'number' ? req.body.speed : undefined;
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.playMedia({ url, mimeType, title, time, volume, speed });
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/media/pause', async (_req, res) => {
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.pauseMedia();
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/media/resume', async (_req, res) => {
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.resumeMedia();
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/media/stop', async (_req, res) => {
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.stopMedia();
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/media/seek', async (req, res) => {
    const time = typeof req.body?.time === 'number' ? req.body.time : undefined;
    if (time === undefined) {
      res.status(400).json({ error: 'time is required' });
      return;
    }
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.seekMedia(time);
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  router.post('/media/volume', async (req, res) => {
    const volume = typeof req.body?.volume === 'number' ? req.body.volume : undefined;
    if (volume === undefined) {
      res.status(400).json({ error: 'volume is required' });
      return;
    }
    try {
      const client = deviceManager.getClient();
      const { status, body } = await client.setMediaVolume(volume);
      res.status(status).json(body);
    } catch (error) {
      handleError(res, error);
    }
  });

  return router;
}
