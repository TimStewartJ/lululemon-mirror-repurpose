import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { createDeviceProxyRouter } from '../routes/device-proxy-router';
import { ConfigStore } from '../config-store';
import type { DeviceManagerPort, UsbConnectionState } from '../device-manager';
import type { DeviceClientPort, DeviceApiResult } from '../proxy-client';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

/**
 * Coverage for the new authenticated GET /api/v1/system and POST
 * /api/v1/system/prepare-kiosk proxy routes.
 */

function fakeManager(client: DeviceClientPort): DeviceManagerPort {
  return {
    listDiscoveredDevices: async () => [],
    connect: async (): Promise<UsbConnectionState> => {
      throw new Error('not exercised');
    },
    connectLan: async () => {
      throw new Error('not exercised');
    },
    disconnect: async () => {},
    getConnection: () => null,
    getClient: () => client,
    ensureUsbReverseTunnel: async () => {
      throw new Error('not exercised');
    },
  };
}

async function startServer(client: DeviceClientPort): Promise<{ baseUrl: string; close: () => Promise<void> }> {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'companion-proxy-test-'));
  const configStore = new ConfigStore(dataDir);
  const app = express();
  app.use(createDeviceProxyRouter(fakeManager(client), configStore));
  const server = app.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.on('listening', resolve));
  const { port } = server.address() as AddressInfo;
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    close: () =>
      new Promise((resolve) => {
        server.close(() => {
          fs.rmSync(dataDir, { recursive: true, force: true });
          resolve();
        });
        server.closeAllConnections();
      }),
  };
}

function clientWith(overrides: Partial<DeviceClientPort>): DeviceClientPort {
  const notExercised = async (): Promise<DeviceApiResult> => {
    throw new Error('not exercised');
  };
  return {
    getStatus: notExercised,
    pair: notExercised,
    getDashboard: notExercised,
    setDashboard: notExercised,
    configureWifi: notExercised,
    setBrightness: notExercised,
    setName: notExercised,
    revokePairing: notExercised,
    getSystemStatus: notExercised,
    prepareKiosk: notExercised,
    setSystemHome: notExercised,
    getMediaStatus: notExercised,
    playMedia: notExercised,
    pauseMedia: notExercised,
    resumeMedia: notExercised,
    stopMedia: notExercised,
    seekMedia: notExercised,
    setMediaVolume: notExercised,
    ...overrides,
  };
}

describe('device-proxy-router /system routes', () => {
  test('GET /system proxies the device system status through', async () => {
    const client = clientWith({
      getSystemStatus: async () => ({ status: 200, body: { connected: true, capabilities: ['kiosk'] } }),
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const response = await fetch(`${baseUrl}/system`);
      assert.equal(response.status, 200);
      const body = (await response.json()) as { connected: boolean; capabilities: string[] };
      assert.equal(body.connected, true);
      assert.deepEqual(body.capabilities, ['kiosk']);
    } finally {
      await close();
    }
  });

  test('POST /system/prepare-kiosk proxies success through', async () => {
    const client = clientWith({
      prepareKiosk: async () => ({ status: 200, body: { prepared: true } }),
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const response = await fetch(`${baseUrl}/system/prepare-kiosk`, { method: 'POST' });
      assert.equal(response.status, 200);
      const body = (await response.json()) as { prepared: boolean };
      assert.equal(body.prepared, true);
    } finally {
      await close();
    }
  });

  test('POST /system/prepare-kiosk forwards a device-reported 503 unavailable status', async () => {
    const client = clientWith({
      prepareKiosk: async () => ({ status: 503, body: { prepared: false } }),
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const response = await fetch(`${baseUrl}/system/prepare-kiosk`, { method: 'POST' });
      assert.equal(response.status, 503);
      const body = (await response.json()) as { prepared: boolean };
      assert.equal(body.prepared, false);
    } finally {
      await close();
    }
  });

  test('GET /system returns 401 mapped from an unauthenticated DeviceApiError when not paired', async () => {
    const { DeviceApiError } = await import('../proxy-client');
    const client = clientWith({
      getSystemStatus: async () => {
        throw new DeviceApiError('Not paired: no bearer token available', 401, null);
      },
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const response = await fetch(`${baseUrl}/system`);
      assert.equal(response.status, 401);
    } finally {
      await close();
    }
  });

  test('POST /system/home requires a boolean enabled field', async () => {
    const client = clientWith({
      setSystemHome: async () => {
        throw new Error('should not be called');
      },
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const missing = await fetch(`${baseUrl}/system/home`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      assert.equal(missing.status, 400);

      const wrongType = await fetch(`${baseUrl}/system/home`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: 'true' }),
      });
      assert.equal(wrongType.status, 400);
    } finally {
      await close();
    }
  });

  test('POST /system/home enables Mirror Home and forwards the parsed body', async () => {
    let receivedEnabled: boolean | undefined;
    const client = clientWith({
      setSystemHome: async (enabled) => {
        receivedEnabled = enabled;
        return { status: 200, body: { changed: true, enabled } };
      },
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const response = await fetch(`${baseUrl}/system/home`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true }),
      });
      assert.equal(response.status, 200);
      const body = (await response.json()) as { changed: boolean; enabled: boolean };
      assert.equal(body.changed, true);
      assert.equal(body.enabled, true);
      assert.equal(receivedEnabled, true);
    } finally {
      await close();
    }
  });

  test('POST /system/home restores the stock launcher when enabled:false', async () => {
    let receivedEnabled: boolean | undefined;
    const client = clientWith({
      setSystemHome: async (enabled) => {
        receivedEnabled = enabled;
        return { status: 200, body: { changed: true, enabled } };
      },
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const response = await fetch(`${baseUrl}/system/home`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: false }),
      });
      assert.equal(response.status, 200);
      const body = (await response.json()) as { changed: boolean; enabled: boolean };
      assert.equal(body.enabled, false);
      assert.equal(receivedEnabled, false);
    } finally {
      await close();
    }
  });

  test('POST /system/home returns 401 mapped from an unauthenticated DeviceApiError when not paired', async () => {
    const { DeviceApiError } = await import('../proxy-client');
    const client = clientWith({
      setSystemHome: async () => {
        throw new DeviceApiError('Not paired: no bearer token available', 401, null);
      },
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const response = await fetch(`${baseUrl}/system/home`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true }),
      });
      assert.equal(response.status, 401);
    } finally {
      await close();
    }
  });
});

describe('device-proxy-router /media routes', () => {
  test('GET /media/status proxies the device media snapshot through', async () => {
    const client = clientWith({
      getMediaStatus: async () => ({
        status: 200,
        body: { state: 'playing', title: 'Clip', positionSeconds: 5, durationSeconds: 60 },
      }),
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const response = await fetch(`${baseUrl}/media/status`);
      assert.equal(response.status, 200);
      const body = (await response.json()) as { state: string; title: string };
      assert.equal(body.state, 'playing');
      assert.equal(body.title, 'Clip');
    } finally {
      await close();
    }
  });

  test('POST /media/play forwards the parsed body fields to DeviceClient.playMedia', async () => {
    let received: unknown;
    const client = clientWith({
      playMedia: async (request) => {
        received = request;
        return { status: 202, body: { accepted: true } };
      },
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const response = await fetch(`${baseUrl}/media/play`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: 'http://127.0.0.1:18787/media-files/song.mp3', volume: 0.5 }),
      });
      assert.equal(response.status, 202);
      assert.deepEqual(received, {
        url: 'http://127.0.0.1:18787/media-files/song.mp3',
        mimeType: undefined,
        title: undefined,
        time: undefined,
        volume: 0.5,
        speed: undefined,
      });
    } finally {
      await close();
    }
  });

  test('POST /media/play rejects a missing url with 400 before calling the device', async () => {
    const client = clientWith({
      playMedia: async () => {
        throw new Error('should not be called');
      },
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const response = await fetch(`${baseUrl}/media/play`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      assert.equal(response.status, 400);
    } finally {
      await close();
    }
  });

  test('POST /media/pause, /media/resume, /media/stop proxy through', async () => {
    const client = clientWith({
      pauseMedia: async () => ({ status: 202, body: { state: 'paused' } }),
      resumeMedia: async () => ({ status: 202, body: { state: 'playing' } }),
      stopMedia: async () => ({ status: 202, body: { state: 'idle' } }),
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const pause = await fetch(`${baseUrl}/media/pause`, { method: 'POST' });
      assert.equal(pause.status, 202);
      assert.equal(((await pause.json()) as { state: string }).state, 'paused');

      const resume = await fetch(`${baseUrl}/media/resume`, { method: 'POST' });
      assert.equal(resume.status, 202);
      assert.equal(((await resume.json()) as { state: string }).state, 'playing');

      const stop = await fetch(`${baseUrl}/media/stop`, { method: 'POST' });
      assert.equal(stop.status, 202);
      assert.equal(((await stop.json()) as { state: string }).state, 'idle');
    } finally {
      await close();
    }
  });

  test('POST /media/seek requires a numeric time and forwards it', async () => {
    let receivedTime: number | undefined;
    const client = clientWith({
      seekMedia: async (time) => {
        receivedTime = time;
        return { status: 202, body: { time } };
      },
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const missing = await fetch(`${baseUrl}/media/seek`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      assert.equal(missing.status, 400);

      const ok = await fetch(`${baseUrl}/media/seek`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ time: 42 }),
      });
      assert.equal(ok.status, 202);
      assert.equal(receivedTime, 42);
    } finally {
      await close();
    }
  });

  test('POST /media/volume requires a numeric volume and forwards it', async () => {
    let receivedVolume: number | undefined;
    const client = clientWith({
      setMediaVolume: async (volume) => {
        receivedVolume = volume;
        return { status: 202, body: { volume } };
      },
    });
    const { baseUrl, close } = await startServer(client);
    try {
      const missing = await fetch(`${baseUrl}/media/volume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      assert.equal(missing.status, 400);

      const ok = await fetch(`${baseUrl}/media/volume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ volume: 0.25 }),
      });
      assert.equal(ok.status, 202);
      assert.equal(receivedVolume, 0.25);
    } finally {
      await close();
    }
  });
});
