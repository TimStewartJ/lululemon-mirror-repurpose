import { test, describe, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { AddressInfo } from 'node:net';
import { createApp } from '../app';
import type { CompanionEnv } from '../env';

/**
 * Regression coverage for code-review finding #4: once COMPANION_ACCESS_TOKEN
 * is configured, every /api/device, /api/companion, and /media-files
 * request must present a matching X-Companion-Token header. Loopback-only
 * deployments (no token configured) remain frictionless. Static UI assets
 * stay reachable either way.
 */

async function startApp(env: CompanionEnv): Promise<{ baseUrl: string; close: () => Promise<void> }> {
  const { app } = createApp(env);
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

function baseEnv(overrides: Partial<CompanionEnv>, dataDir: string, mediaRoot: string): CompanionEnv {
  return {
    port: 0,
    host: '127.0.0.1',
    dataDir,
    mediaRoot,
    adbPath: undefined,
    adbForwardPort: 18787,
    deviceMediaPort: 14317,
    deviceApiPort: 8787,
    accessToken: undefined,
    publicUrl: undefined,
    ...overrides,
  };
}

describe('access token middleware wiring in the full app (finding #4 regression)', () => {
  let dataDir: string;
  let mediaRoot: string;

  before(() => {
    dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'companion-app-data-'));
    mediaRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'companion-app-media-'));
  });

  after(() => {
    fs.rmSync(dataDir, { recursive: true, force: true });
    fs.rmSync(mediaRoot, { recursive: true, force: true });
  });

  test('requests succeed without a header when no access token is configured', async () => {
    const { baseUrl, close } = await startApp(baseEnv({}, dataDir, mediaRoot));
    try {
      const response = await fetch(`${baseUrl}/api/companion/media`);
      assert.equal(response.status, 200);
    } finally {
      await close();
    }
  });

  test('rejects /api/companion requests without the header once a token is configured', async () => {
    const { baseUrl, close } = await startApp(baseEnv({ accessToken: 'secret-token' }, dataDir, mediaRoot));
    try {
      const response = await fetch(`${baseUrl}/api/companion/media`);
      assert.equal(response.status, 401);
    } finally {
      await close();
    }
  });

  test('accepts /api/companion requests with the correct X-Companion-Token header', async () => {
    const { baseUrl, close } = await startApp(baseEnv({ accessToken: 'secret-token' }, dataDir, mediaRoot));
    try {
      const response = await fetch(`${baseUrl}/api/companion/media`, {
        headers: { 'X-Companion-Token': 'secret-token' },
      });
      assert.equal(response.status, 200);
    } finally {
      await close();
    }
  });

  test('rejects /api/device requests without the header once a token is configured', async () => {
    const { baseUrl, close } = await startApp(baseEnv({ accessToken: 'secret-token' }, dataDir, mediaRoot));
    try {
      const response = await fetch(`${baseUrl}/api/device/status`);
      assert.equal(response.status, 401);
    } finally {
      await close();
    }
  });

  test('rejects /media-files requests without the header once a token is configured', async () => {
    const { baseUrl, close } = await startApp(baseEnv({ accessToken: 'secret-token' }, dataDir, mediaRoot));
    try {
      const response = await fetch(`${baseUrl}/media-files/anything.mp3`);
      assert.equal(response.status, 401);
    } finally {
      await close();
    }
  });

  test('static UI assets remain reachable without a token even when one is configured', async () => {
    const { baseUrl, close } = await startApp(baseEnv({ accessToken: 'secret-token' }, dataDir, mediaRoot));
    try {
      const response = await fetch(`${baseUrl}/index.html`);
      // 200 if dist/client assets happen to be built alongside this test
      // run, 404 if not -- either way it must never be 401: static assets
      // are not behind the access-token gate.
      assert.notEqual(response.status, 401);
    } finally {
      await close();
    }
  });
});
