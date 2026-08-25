import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { createDevicesRouter } from '../routes/devices-router';
import type { DeviceManagerPort, UsbConnectionState } from '../device-manager';
import type { DeviceClientPort, DeviceApiResult } from '../proxy-client';

/**
 * Regression coverage for code-review finding #1: async errors thrown by
 * DeviceManager.disconnect() must be caught and turned into a proper error
 * response, not left to reject an unhandled promise (which would hang the
 * request and crash the process under Node's unhandled-rejection policy).
 */

function fakeClient(): DeviceClientPort {
  const ok = async (): Promise<DeviceApiResult> => ({ status: 200, body: {} });
  return {
    getStatus: ok,
    pair: ok,
    getDashboard: ok,
    setDashboard: ok,
    configureWifi: ok,
    setBrightness: ok,
    setName: ok,
    revokePairing: ok,
    getSystemStatus: ok,
    prepareKiosk: ok,
    setSystemHome: ok,
    getMediaStatus: ok,
    playMedia: ok,
    pauseMedia: ok,
    resumeMedia: ok,
    stopMedia: ok,
    seekMedia: ok,
    setMediaVolume: ok,
  };
}

async function startServer(manager: DeviceManagerPort): Promise<{ baseUrl: string; close: () => Promise<void> }> {
  const app = express();
  app.use(createDevicesRouter(manager));
  const server = app.listen(0, '127.0.0.1');
  await new Promise<void>((resolve) => server.on('listening', resolve));
  const { port } = server.address() as AddressInfo;
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    close: () =>
      new Promise((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections();
      }),
  };
}

describe('devices-router disconnect error handling (finding #1 regression)', () => {
  test('returns a 500 JSON error instead of hanging when disconnect() rejects', async () => {
    const manager: DeviceManagerPort = {
      listDiscoveredDevices: async () => [],
      connect: async (): Promise<UsbConnectionState> => {
        throw new Error('not exercised in this test');
      },
      connectLan: async () => {
        throw new Error('not exercised');
      },
      disconnect: async () => {
        throw new Error('adb forward removal failed');
      },
      getConnection: () => null,
      getClient: () => fakeClient(),
      ensureUsbReverseTunnel: async () => {
        throw new Error('not exercised');
      },
    };
    const { baseUrl, close } = await startServer(manager);
    try {
      const response = await fetch(`${baseUrl}/devices/disconnect`, { method: 'POST' });
      assert.equal(response.status, 500);
      const body = (await response.json()) as { error: string };
      assert.match(body.error, /adb forward removal failed/);
    } finally {
      await close();
    }
  });

  test('returns 200 with connected:false when disconnect() succeeds', async () => {
    const manager: DeviceManagerPort = {
      listDiscoveredDevices: async () => [],
      connect: async (): Promise<UsbConnectionState> => {
        throw new Error('not exercised in this test');
      },
      connectLan: async () => {
        throw new Error('not exercised');
      },
      disconnect: async () => {},
      getConnection: () => null,
      getClient: () => fakeClient(),
      ensureUsbReverseTunnel: async () => {
        throw new Error('not exercised');
      },
    };
    const { baseUrl, close } = await startServer(manager);
    try {
      const response = await fetch(`${baseUrl}/devices/disconnect`, { method: 'POST' });
      assert.equal(response.status, 200);
      const body = (await response.json()) as { connected: boolean };
      assert.equal(body.connected, false);
    } finally {
      await close();
    }
  });
});

describe('devices-router JSON body handling (finding #2 rescoping regression)', () => {
  test('POST /devices/connect still parses a JSON body once express.json is scoped to this router only', async () => {
    const manager: DeviceManagerPort = {
      listDiscoveredDevices: async () => [],
      connect: async (serial: string): Promise<UsbConnectionState> => ({
        mode: 'usb',
        serial,
        localPort: 18787,
      }),
      connectLan: async () => {
        throw new Error('not exercised');
      },
      disconnect: async () => {},
      getConnection: () => null,
      getClient: () => fakeClient(),
      ensureUsbReverseTunnel: async () => {
        throw new Error('not exercised');
      },
    };
    const { baseUrl, close } = await startServer(manager);
    try {
      const response = await fetch(`${baseUrl}/devices/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ serial: 'ABC123' }),
      });
      assert.equal(response.status, 200);
      const body = (await response.json()) as { serial: string; localPort: number };
      assert.equal(body.serial, 'ABC123');
      assert.equal(body.localPort, 18787);
    } finally {
      await close();
    }
  });
});

describe('devices-router POST /devices/lan/connect', () => {
  test('connects successfully and reports the LAN host, never the token', async () => {
    let receivedArgs: [string, string] | undefined;
    const manager: DeviceManagerPort = {
      listDiscoveredDevices: async () => [],
      connect: async (): Promise<UsbConnectionState> => {
        throw new Error('not exercised');
      },
      connectLan: async (host, token) => {
        receivedArgs = [host, token];
        return { mode: 'lan', host, port: 8787 };
      },
      disconnect: async () => {},
      getConnection: () => null,
      getClient: () => fakeClient(),
      ensureUsbReverseTunnel: async () => {
        throw new Error('not exercised');
      },
    };
    const { baseUrl, close } = await startServer(manager);
    try {
      const response = await fetch(`${baseUrl}/devices/lan/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ipAddress: '192.168.1.20', token: 'lan-token' }),
      });
      assert.equal(response.status, 200);
      const body = (await response.json()) as Record<string, unknown>;
      assert.equal(body.connected, true);
      assert.equal(body.mode, 'lan');
      assert.equal(body.host, '192.168.1.20');
      assert.equal(Object.prototype.hasOwnProperty.call(body, 'token'), false);
      assert.deepEqual(receivedArgs, ['192.168.1.20', 'lan-token']);
    } finally {
      await close();
    }
  });

  test('requires both ipAddress and token', async () => {
    const manager: DeviceManagerPort = {
      listDiscoveredDevices: async () => [],
      connect: async (): Promise<UsbConnectionState> => {
        throw new Error('not exercised');
      },
      connectLan: async () => {
        throw new Error('should not be called');
      },
      disconnect: async () => {},
      getConnection: () => null,
      getClient: () => fakeClient(),
      ensureUsbReverseTunnel: async () => {
        throw new Error('not exercised');
      },
    };
    const { baseUrl, close } = await startServer(manager);
    try {
      const missingToken = await fetch(`${baseUrl}/devices/lan/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ipAddress: '192.168.1.20' }),
      });
      assert.equal(missingToken.status, 400);

      const missingIp = await fetch(`${baseUrl}/devices/lan/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: 'lan-token' }),
      });
      assert.equal(missingIp.status, 400);
    } finally {
      await close();
    }
  });

  test('surfaces the SSRF-guard rejection message with a 400', async () => {
    const manager: DeviceManagerPort = {
      listDiscoveredDevices: async () => [],
      connect: async (): Promise<UsbConnectionState> => {
        throw new Error('not exercised');
      },
      connectLan: async () => {
        throw new Error('Refusing to connect: "8.8.8.8" is not a private (RFC1918) or link-local IPv4 address.');
      },
      disconnect: async () => {},
      getConnection: () => null,
      getClient: () => fakeClient(),
      ensureUsbReverseTunnel: async () => {
        throw new Error('not exercised');
      },
    };
    const { baseUrl, close } = await startServer(manager);
    try {
      const response = await fetch(`${baseUrl}/devices/lan/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ipAddress: '8.8.8.8', token: 'lan-token' }),
      });
      assert.equal(response.status, 400);
      const body = (await response.json()) as { error: string };
      assert.match(body.error, /private.*RFC1918|link-local/i);
    } finally {
      await close();
    }
  });
});
