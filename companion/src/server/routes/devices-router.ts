import express, { Router } from 'express';
import type { DeviceManagerPort } from '../device-manager';
import { logger } from '../logger';

/**
 * Routes for discovering and connecting to the Mirror over USB via adb.
 * These never touch the pairing token; that lives in device-proxy-router.
 */
export function createDevicesRouter(deviceManager: DeviceManagerPort): Router {
  const router = Router();
  // Scoped locally (not globally in app.ts) so the media router's raw
  // upload stream is never at risk of being consumed by a body parser
  // mounted ahead of it.
  router.use(express.json({ limit: '256kb' }));

  router.get('/devices', async (_req, res) => {
    try {
      const devices = await deviceManager.listDiscoveredDevices();
      res.json({ devices });
    } catch (error) {
      logger.warn('device listing failed', { message: (error as Error).message });
      res.status(502).json({ error: (error as Error).message });
    }
  });

  router.post('/devices/connect', async (req, res) => {
    const serial = typeof req.body?.serial === 'string' ? req.body.serial : undefined;
    if (!serial) {
      res.status(400).json({ error: 'serial is required' });
      return;
    }
    try {
      const connection = await deviceManager.connect(serial);
      res.json({ connected: true, serial: connection.serial, localPort: connection.localPort });
    } catch (error) {
      res.status(400).json({ error: (error as Error).message });
    }
  });

  router.post('/devices/disconnect', async (_req, res) => {
    try {
      await deviceManager.disconnect();
      res.json({ connected: false });
    } catch (error) {
      logger.warn('device disconnect failed', { message: (error as Error).message });
      res.status(500).json({ error: (error as Error).message });
    }
  });

  // Connects to a Mirror that is already on the household LAN using its
  // private IPv4 address and an existing pairing token (for example, one
  // issued through the on-glass QR + pairing-code flow). The host is
  // validated (RFC1918/link-local IPv4 only) inside
  // deviceManager.connectLan(); this route never sees or forwards a Wi-Fi
  // passphrase, and never logs the token.
  router.post('/devices/lan/connect', async (req, res) => {
    const ipAddress = typeof req.body?.ipAddress === 'string' ? req.body.ipAddress : undefined;
    const token = typeof req.body?.token === 'string' ? req.body.token : undefined;
    if (!ipAddress || !token) {
      res.status(400).json({ error: 'ipAddress and token are required' });
      return;
    }
    try {
      const connection = await deviceManager.connectLan(ipAddress, token);
      res.json({ connected: true, mode: connection.mode, host: connection.host });
    } catch (error) {
      res.status(400).json({ error: (error as Error).message });
    }
  });

  router.get('/devices/connection', (_req, res) => {
    const connection = deviceManager.getConnection();
    res.json({ connection });
  });

  return router;
}
