import { test, describe, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import fsPromises from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { createMediaRouter, createMediaFilesRouter, type MediaRouterOptions } from '../routes/media-router';
import { MediaLibrary, type MediaLibraryPort, type MediaEntry } from '../media/media-library';
import type { DeviceManagerPort, UsbConnectionState } from '../device-manager';

/**
 * Integration-aligned coverage for code-review findings #1, #2, and #3.
 * These mount the routers the same way app.ts does in production: the
 * media router never gets a JSON body parser ahead of it, so uploads
 * always see the raw, unconsumed request stream.
 */

/** A fake DeviceManagerPort reporting "no device connected"; these tests
 * don't exercise the /media/:name/play route, so every operation throws if
 * ever called. */
function fakeDeviceManager(): DeviceManagerPort {
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
    getClient: () => {
      throw new Error('not exercised');
    },
    ensureUsbReverseTunnel: async () => {
      throw new Error('not exercised');
    },
  };
}

async function startServer(
  mediaLibrary: MediaLibraryPort,
  deviceManager: DeviceManagerPort = fakeDeviceManager(),
  routerOptions: Partial<MediaRouterOptions> = {},
): Promise<{ baseUrl: string; close: () => Promise<void> }> {
  const app = express();
  app.use(createMediaRouter(mediaLibrary, deviceManager, { companionPort: 4317, ...routerOptions }));
  app.use('/', createMediaFilesRouter(mediaLibrary));
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

describe('media-router GET /media error handling (finding #1 regression)', () => {
  test('returns a 500 JSON error instead of hanging when list() rejects', async () => {
    const brokenLibrary: MediaLibraryPort = {
      list: async () => {
        throw new Error('disk read failed');
      },
      resolve: () => {
        throw new Error('not exercised');
      },
      save: async () => {
        throw new Error('not exercised');
      },
      remove: async () => {
        throw new Error('not exercised');
      },
      serve: async () => {
        throw new Error('not exercised');
      },
    };
    const { baseUrl, close } = await startServer(brokenLibrary);
    try {
      const response = await fetch(`${baseUrl}/media`);
      assert.equal(response.status, 500);
    } finally {
      await close();
    }
  });

  test('returns the entry list on success (sanity check)', async () => {
    const entries: MediaEntry[] = [{ name: 'song.mp3', sizeBytes: 10, modifiedAt: new Date().toISOString() }];
    const library: MediaLibraryPort = {
      list: async () => entries,
      resolve: () => {
        throw new Error('not exercised');
      },
      save: async () => {
        throw new Error('not exercised');
      },
      remove: async () => {
        throw new Error('not exercised');
      },
      serve: async () => {
        throw new Error('not exercised');
      },
    };
    const { baseUrl, close } = await startServer(library);
    try {
      const response = await fetch(`${baseUrl}/media`);
      assert.equal(response.status, 200);
      const body = (await response.json()) as { entries: MediaEntry[] };
      assert.deepEqual(body.entries, entries);
    } finally {
      await close();
    }
  });
});

describe('media-router upload content-type handling (finding #2 regression)', () => {
  let root: string;

  before(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'companion-media-test-'));
  });

  after(() => {
    if (root) fs.rmSync(root, { recursive: true, force: true });
  });

  test('preserves raw bytes exactly when Content-Type is application/json', async () => {
    const library = new MediaLibrary(root);
    const { baseUrl, close } = await startServer(library);
    try {
      // A JSON-looking payload uploaded as a "file". If express.json() were
      // mounted ahead of this router, it would consume this body into
      // req.body and the handler would receive nothing to stream, silently
      // producing a 0-byte file.
      const payload = Buffer.from(JSON.stringify({ hello: 'world', n: 42, nested: { a: [1, 2, 3] } }), 'utf8');
      const response = await fetch(`${baseUrl}/media/payload.json`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
      });
      assert.equal(response.status, 201);

      const onDisk = await fsPromises.readFile(path.join(root, 'payload.json'));
      assert.equal(Buffer.compare(onDisk, payload), 0);
    } finally {
      await close();
    }
  });

  test('preserves raw bytes exactly for arbitrary binary content with no content type', async () => {
    const library = new MediaLibrary(root);
    const { baseUrl, close } = await startServer(library);
    try {
      const payload = crypto.randomBytes(4096);
      const response = await fetch(`${baseUrl}/media/binary.bin`, {
        method: 'PUT',
        body: payload,
      });
      assert.equal(response.status, 201);

      const onDisk = await fsPromises.readFile(path.join(root, 'binary.bin'));
      assert.equal(Buffer.compare(onDisk, payload), 0);
    } finally {
      await close();
    }
  });

  test('rejects multipart/form-data uploads with 415 instead of silently mishandling them', async () => {
    const library = new MediaLibrary(root);
    const { baseUrl, close } = await startServer(library);
    try {
      const response = await fetch(`${baseUrl}/media/upload.bin`, {
        method: 'PUT',
        headers: { 'Content-Type': 'multipart/form-data; boundary=----boundary' },
        body: '------boundary--',
      });
      assert.equal(response.status, 415);
      const exists = fs.existsSync(path.join(root, 'upload.bin'));
      assert.equal(exists, false);
    } finally {
      await close();
    }
  });
});

describe('media-router POST /media/:name/play', () => {
  let root: string;

  before(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'companion-media-play-test-'));
  });

  after(() => {
    if (root) fs.rmSync(root, { recursive: true, force: true });
  });

  function fakeClient(overrides: Partial<import('../proxy-client').DeviceClientPort> = {}) {
    const notExercised = async (): Promise<import('../proxy-client').DeviceApiResult> => {
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

  test('returns 404 when the named media file does not exist', async () => {
    const library = new MediaLibrary(root);
    const { baseUrl, close } = await startServer(library);
    try {
      const response = await fetch(`${baseUrl}/media/missing.mp3/play`, { method: 'POST' });
      assert.equal(response.status, 404);
    } finally {
      await close();
    }
  });

  test('returns 400 for a path-traversal name instead of resolving anything', async () => {
    const library = new MediaLibrary(root);
    const { baseUrl, close } = await startServer(library);
    try {
      const response = await fetch(`${baseUrl}/media/${encodeURIComponent('../secret.txt')}/play`, {
        method: 'POST',
      });
      assert.equal(response.status, 400);
    } finally {
      await close();
    }
  });

  test('returns 409 when no device is connected', async () => {
    await fsPromises.writeFile(path.join(root, 'song.mp3'), 'audio-bytes');
    const library = new MediaLibrary(root);
    const { baseUrl, close } = await startServer(library, fakeDeviceManager());
    try {
      const response = await fetch(`${baseUrl}/media/song.mp3/play`, { method: 'POST' });
      assert.equal(response.status, 409);
    } finally {
      await close();
    }
  });

  test('USB mode builds a 127.0.0.1:<devicePort> URL via the reverse tunnel and calls playMedia', async () => {
    await fsPromises.writeFile(path.join(root, 'song.mp3'), 'audio-bytes');
    const library = new MediaLibrary(root);
    let receivedUrl: string | undefined;
    let receivedHeaders: Record<string, string> | undefined;
    const client = fakeClient({
      playMedia: async (request) => {
        receivedUrl = request.url;
        receivedHeaders = request.headers;
        return { status: 202, body: { accepted: true } };
      },
    });
    const deviceManager: DeviceManagerPort = {
      listDiscoveredDevices: async () => [],
      connect: async (): Promise<UsbConnectionState> => {
        throw new Error('not exercised');
      },
      connectLan: async () => {
        throw new Error('not exercised');
      },
      disconnect: async () => {},
      getConnection: () => ({ mode: 'usb', serial: 'ABC123', localPort: 18787, reverseDevicePort: null }),
      getClient: () => client,
      ensureUsbReverseTunnel: async () => 40123,
    };
    const { baseUrl, close } = await startServer(library, deviceManager);
    try {
      const response = await fetch(`${baseUrl}/media/song.mp3/play`, { method: 'POST' });
      assert.equal(response.status, 202);
      assert.equal(receivedUrl, 'http://127.0.0.1:40123/media-files/song.mp3');
      assert.equal(receivedHeaders, undefined);
    } finally {
      await close();
    }
  });

  test('USB mode omits the companion access token even when one is configured (always a 127.0.0.1 reverse-tunnel URL)', async () => {
    await fsPromises.writeFile(path.join(root, 'song.mp3'), 'audio-bytes');
    const library = new MediaLibrary(root);
    let receivedHeaders: Record<string, string> | undefined;
    const client = fakeClient({
      playMedia: async (request) => {
        receivedHeaders = request.headers;
        return { status: 202, body: { accepted: true } };
      },
    });
    const deviceManager: DeviceManagerPort = {
      listDiscoveredDevices: async () => [],
      connect: async (): Promise<UsbConnectionState> => {
        throw new Error('not exercised');
      },
      connectLan: async () => {
        throw new Error('not exercised');
      },
      disconnect: async () => {},
      getConnection: () => ({ mode: 'usb', serial: 'ABC123', localPort: 18787, reverseDevicePort: null }),
      getClient: () => client,
      ensureUsbReverseTunnel: async () => 40123,
    };
    const { baseUrl, close } = await startServer(library, deviceManager, { accessToken: 'super-secret-token' });
    try {
      const response = await fetch(`${baseUrl}/media/song.mp3/play`, { method: 'POST' });
      assert.equal(response.status, 202);
      assert.equal(receivedHeaders, undefined);
    } finally {
      await close();
    }
  });

  test('LAN mode uses the COMPANION_PUBLIC_URL override, never a browser Host header', async () => {
    await fsPromises.writeFile(path.join(root, 'song.mp3'), 'audio-bytes');
    const library = new MediaLibrary(root);
    let receivedUrl: string | undefined;
    let receivedHeaders: Record<string, string> | undefined;
    const client = fakeClient({
      playMedia: async (request) => {
        receivedUrl = request.url;
        receivedHeaders = request.headers;
        return { status: 202, body: { accepted: true } };
      },
    });
    const deviceManager: DeviceManagerPort = {
      listDiscoveredDevices: async () => [],
      connect: async (): Promise<UsbConnectionState> => {
        throw new Error('not exercised');
      },
      connectLan: async () => {
        throw new Error('not exercised');
      },
      disconnect: async () => {},
      getConnection: () => ({ mode: 'lan', host: '192.168.1.20', port: 8787 }),
      getClient: () => client,
      ensureUsbReverseTunnel: async () => {
        throw new Error('not exercised in LAN mode');
      },
    };
    const app = express();
    app.use(
      createMediaRouter(library, deviceManager, {
        companionPort: 4317,
        publicUrl: 'http://203.0.113.5:4317',
      }),
    );
    const server = app.listen(0, '127.0.0.1');
    await new Promise<void>((resolve) => server.on('listening', resolve));
    const { port } = server.address() as AddressInfo;
    try {
      // Sending a spoofed Host header must have no effect on the URL used.
      const response = await fetch(`http://127.0.0.1:${port}/media/song.mp3/play`, {
        method: 'POST',
        headers: { Host: 'attacker.example' },
      });
      assert.equal(response.status, 202);
      assert.equal(receivedUrl, 'http://203.0.113.5:4317/media-files/song.mp3');
      // No COMPANION_ACCESS_TOKEN was configured for this router instance,
      // so there is nothing to forward: headers must stay unset.
      assert.equal(receivedHeaders, undefined);
    } finally {
      await new Promise<void>((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections();
      });
    }
  });

  test('LAN mode forwards the configured companion access token as an X-Companion-Token header for the device to replay', async () => {
    await fsPromises.writeFile(path.join(root, 'song.mp3'), 'audio-bytes');
    const library = new MediaLibrary(root);
    let receivedUrl: string | undefined;
    let receivedHeaders: Record<string, string> | undefined;
    const client = fakeClient({
      playMedia: async (request) => {
        receivedUrl = request.url;
        receivedHeaders = request.headers;
        return { status: 202, body: { accepted: true } };
      },
    });
    const deviceManager: DeviceManagerPort = {
      listDiscoveredDevices: async () => [],
      connect: async (): Promise<UsbConnectionState> => {
        throw new Error('not exercised');
      },
      connectLan: async () => {
        throw new Error('not exercised');
      },
      disconnect: async () => {},
      getConnection: () => ({ mode: 'lan', host: '192.168.1.20', port: 8787 }),
      getClient: () => client,
      ensureUsbReverseTunnel: async () => {
        throw new Error('not exercised in LAN mode');
      },
    };
    const { baseUrl, close } = await startServer(library, deviceManager, {
      publicUrl: 'http://203.0.113.5:4317',
      accessToken: 'super-secret-token',
    });
    try {
      const response = await fetch(`${baseUrl}/media/song.mp3/play`, { method: 'POST' });
      assert.equal(response.status, 202);
      assert.equal(receivedUrl, 'http://203.0.113.5:4317/media-files/song.mp3');
      assert.deepEqual(receivedHeaders, { 'X-Companion-Token': 'super-secret-token' });
      // The credential must never be appended to the media URL itself.
      assert.equal(receivedUrl?.includes('super-secret-token'), false);
    } finally {
      await close();
    }
  });
});

describe('media path traversal / percent-encoding (finding #3 integration regression)', () => {
  let root: string;

  before(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), 'companion-media-traversal-test-'));
  });

  after(() => {
    if (root) fs.rmSync(root, { recursive: true, force: true });
  });

  test('rejects a percent-encoded traversal sequence over real HTTP', async () => {
    const library = new MediaLibrary(root);
    const { baseUrl, close } = await startServer(library);
    try {
      // fetch's URL parser will send this through untouched; Express
      // decodes the wildcard param once (giving "../../../etc/passwd"),
      // which resolveMediaPath must reject.
      const response = await fetch(`${baseUrl}/media-files/..%2f..%2f..%2fetc%2fpasswd`);
      assert.equal(response.status, 400);
    } finally {
      await close();
    }
  });

  test('round-trips a legitimate filename containing a literal percent character', async () => {
    const library = new MediaLibrary(root);
    const { baseUrl, close } = await startServer(library);
    try {
      const payload = Buffer.from('fifty percent audio track', 'utf8');
      const filename = '50%.mp3';

      const putResponse = await fetch(`${baseUrl}/media/${encodeURIComponent(filename)}`, {
        method: 'PUT',
        body: payload,
      });
      assert.equal(putResponse.status, 201);

      const getResponse = await fetch(`${baseUrl}/media-files/${encodeURIComponent(filename)}`);
      assert.equal(getResponse.status, 200);
      const body = Buffer.from(await getResponse.arrayBuffer());
      assert.equal(Buffer.compare(body, payload), 0);
    } finally {
      await close();
    }
  });
});
