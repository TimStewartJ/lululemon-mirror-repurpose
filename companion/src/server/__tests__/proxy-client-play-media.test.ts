import { test, describe, mock } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { DeviceClient } from '../proxy-client';

interface CapturedRequest {
  method: string;
  url: string;
  headers: http.IncomingHttpHeaders;
  body: string;
}

async function startFakeDeviceServer(): Promise<{
  port: number;
  requests: CapturedRequest[];
  close: () => Promise<void>;
}> {
  const requests: CapturedRequest[] = [];
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => {
      requests.push({
        method: req.method ?? '',
        url: req.url ?? '',
        headers: req.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      });
      res.writeHead(202, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ accepted: true }));
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', () => resolve()));
  const { port } = server.address() as AddressInfo;
  return {
    port,
    requests,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
}

// Regression coverage: LAN media playback must forward the companion's own
// access token to the device so the device can attach it (as
// X-Companion-Token) when it fetches the hosted media URL back from this
// companion's protected /media-files route. That credential must travel
// exclusively inside the JSON request body's `headers` field of the
// device-facing POST /api/v1/media/play call -- never appended to the
// media URL, never sent as a literal HTTP header on the companion->device
// request itself (that channel is authenticated with the device's own
// separate pairing bearer token), and never written to a log line.
describe('DeviceClient.playMedia credential handling', () => {
  test('forwards headers only inside the JSON body, keeping it out of the URL, device-facing HTTP headers, and logs', async () => {
    const { port, requests, close } = await startFakeDeviceServer();
    const logs: string[] = [];
    const logMock = mock.method(console, 'log', (message?: unknown) => {
      logs.push(String(message));
    });
    try {
      const client = new DeviceClient({ port, token: 'device-bearer-secret' });
      const result = await client.playMedia({
        url: 'http://192.168.1.20:4317/media-files/song.mp3',
        mimeType: 'audio/mpeg',
        headers: { 'X-Companion-Token': 'lan-companion-token-xyz' },
      });

      assert.equal(result.status, 202);
      assert.equal(requests.length, 1);
      const captured = requests[0];
      assert.ok(captured);

      // Exact path, no query string -- the credential must never leak into
      // the device API request URL.
      assert.equal(captured.method, 'POST');
      assert.equal(captured.url, '/api/v1/media/play');
      assert.equal(captured.url.includes('lan-companion-token-xyz'), false);

      // The companion->device HTTP request itself is authenticated with the
      // device's own pairing bearer token, never with the companion's
      // access token as a literal header.
      assert.equal(captured.headers.authorization, 'Bearer device-bearer-secret');
      assert.equal(captured.headers['x-companion-token'], undefined);

      // The JSON body is the only place the companion access token appears,
      // nested under `headers` for the device to replay on its own fetch.
      const parsedBody = JSON.parse(captured.body) as {
        url: string;
        headers?: Record<string, string>;
      };
      assert.equal(parsedBody.url, 'http://192.168.1.20:4317/media-files/song.mp3');
      assert.deepEqual(parsedBody.headers, { 'X-Companion-Token': 'lan-companion-token-xyz' });
      assert.equal(parsedBody.url.includes('lan-companion-token-xyz'), false);

      for (const line of logs) {
        assert.equal(line.includes('lan-companion-token-xyz'), false, `log line leaked the token: ${line}`);
      }
    } finally {
      logMock.mock.restore();
      await close();
    }
  });

  test('omits the headers field entirely (not an empty object) when no headers are supplied', async () => {
    const { port, requests, close } = await startFakeDeviceServer();
    try {
      const client = new DeviceClient({ port, token: 'device-bearer-secret' });
      await client.playMedia({ url: 'http://127.0.0.1:40123/media-files/song.mp3' });

      assert.equal(requests.length, 1);
      const captured = requests[0];
      assert.ok(captured);
      const parsedBody = JSON.parse(captured.body) as Record<string, unknown>;
      assert.equal('headers' in parsedBody, false);
    } finally {
      await close();
    }
  });
});
