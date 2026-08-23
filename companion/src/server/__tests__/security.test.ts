import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import type { Request, Response } from 'express';
import {
  isLoopbackHost,
  assertAccessTokenConfigured,
  createAccessTokenMiddleware,
  MissingAccessTokenError,
} from '../security';

describe('isLoopbackHost', () => {
  test('recognizes 127.0.0.1', () => {
    assert.equal(isLoopbackHost('127.0.0.1'), true);
  });

  test('recognizes other addresses in the 127.0.0.0/8 range', () => {
    assert.equal(isLoopbackHost('127.5.6.7'), true);
  });

  test('recognizes localhost case-insensitively', () => {
    assert.equal(isLoopbackHost('Localhost'), true);
  });

  test('recognizes the IPv6 loopback address', () => {
    assert.equal(isLoopbackHost('::1'), true);
  });

  test('treats 0.0.0.0 as non-loopback (binds every interface)', () => {
    assert.equal(isLoopbackHost('0.0.0.0'), false);
  });

  test('treats a LAN address as non-loopback', () => {
    assert.equal(isLoopbackHost('192.168.1.50'), false);
  });

  test('treats an empty string as non-loopback', () => {
    assert.equal(isLoopbackHost(''), false);
  });
});

describe('assertAccessTokenConfigured', () => {
  test('does not throw for the default loopback host, token or not', () => {
    assert.doesNotThrow(() => assertAccessTokenConfigured({ host: '127.0.0.1', accessToken: undefined }));
    assert.doesNotThrow(() => assertAccessTokenConfigured({ host: '127.0.0.1', accessToken: 'secret' }));
  });

  test('throws MissingAccessTokenError for a non-loopback host with no token', () => {
    assert.throws(
      () => assertAccessTokenConfigured({ host: '0.0.0.0', accessToken: undefined }),
      MissingAccessTokenError,
    );
  });

  test('does not throw for a non-loopback host once a token is configured', () => {
    assert.doesNotThrow(() => assertAccessTokenConfigured({ host: '0.0.0.0', accessToken: 'secret' }));
  });
});

// Minimal fakes: the middleware only calls req.header(...) and
// res.status(...).json(...), so we avoid pulling in a real HTTP server for
// these unit tests.
function fakeRequest(headers: Record<string, string>): Request {
  return {
    header: (name: string) => headers[name.toLowerCase()],
  } as unknown as Request;
}

function fakeResponse(): { res: Response; statusCode: () => number | undefined; body: () => unknown } {
  let statusCode: number | undefined;
  let body: unknown;
  const res = {
    status(code: number) {
      statusCode = code;
      return res;
    },
    json(payload: unknown) {
      body = payload;
      return res;
    },
  } as unknown as Response;
  return { res, statusCode: () => statusCode, body: () => body };
}

describe('createAccessTokenMiddleware', () => {
  test('passes through every request when no token is configured', () => {
    const middleware = createAccessTokenMiddleware(undefined);
    const req = fakeRequest({});
    const { res } = fakeResponse();
    let nextCalled = false;
    middleware(req, res, () => {
      nextCalled = true;
    });
    assert.equal(nextCalled, true);
  });

  test('rejects a request with no header when a token is configured', () => {
    const middleware = createAccessTokenMiddleware('super-secret');
    const req = fakeRequest({});
    const { res, statusCode, body } = fakeResponse();
    let nextCalled = false;
    middleware(req, res, () => {
      nextCalled = true;
    });
    assert.equal(nextCalled, false);
    assert.equal(statusCode(), 401);
    assert.match((body() as { error: string }).error, /Missing or invalid/);
  });

  test('rejects a request with the wrong token', () => {
    const middleware = createAccessTokenMiddleware('super-secret');
    const req = fakeRequest({ 'x-companion-token': 'wrong-token-value' });
    const { res, statusCode } = fakeResponse();
    let nextCalled = false;
    middleware(req, res, () => {
      nextCalled = true;
    });
    assert.equal(nextCalled, false);
    assert.equal(statusCode(), 401);
  });

  test('rejects a token that only differs in length from the configured one', () => {
    const middleware = createAccessTokenMiddleware('super-secret');
    const req = fakeRequest({ 'x-companion-token': 'super-secret-but-longer' });
    const { res, statusCode } = fakeResponse();
    let nextCalled = false;
    middleware(req, res, () => {
      nextCalled = true;
    });
    assert.equal(nextCalled, false);
    assert.equal(statusCode(), 401);
  });

  test('accepts a request with the correct token', () => {
    const middleware = createAccessTokenMiddleware('super-secret');
    const req = fakeRequest({ 'x-companion-token': 'super-secret' });
    const { res, statusCode } = fakeResponse();
    let nextCalled = false;
    middleware(req, res, () => {
      nextCalled = true;
    });
    assert.equal(nextCalled, true);
    assert.equal(statusCode(), undefined);
  });
});
