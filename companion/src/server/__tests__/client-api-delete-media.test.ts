import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

// api.ts is browser-only source (native ESM, compiled by tsconfig.client.json
// into dist/client/api.js). It has no DOM dependency beyond global `fetch`
// and `window.sessionStorage` (guarded by a try/catch in token.ts, so it is
// harmless to import from plain Node), so we exercise the *compiled* module
// directly via a dynamic import rather than duplicating its fetch-wrapping
// logic in a server-side helper just to make it unit-testable.
// dist/client/package.json (written by scripts/copy-assets.mjs) marks that
// directory as "type": "module" so Node's loader parses it as ESM.
const moduleUrl = pathToFileURL(path.join(__dirname, '..', '..', 'client', 'api.js')).href;

interface CompanionApiModule {
  companionApi: {
    deleteMedia(name: string): Promise<void>;
  };
}

async function loadModule(): Promise<CompanionApiModule> {
  // TypeScript, when compiling to CommonJS, rewrites a literal `import(...)`
  // expression into `require(...)`, which cannot load a real ES module (and
  // require() doesn't accept file:// URLs anyway). Wrapping the import in a
  // `new Function(...)` body hides it from TypeScript's downlevel
  // transform, so this performs a genuine dynamic import at runtime.
  const dynamicImport = new Function('specifier', 'return import(specifier);') as (
    specifier: string,
  ) => Promise<CompanionApiModule>;
  return dynamicImport(moduleUrl);
}

function fakeFetch(response: Pick<Response, 'ok' | 'status'> & { json?: () => Promise<unknown> }): typeof fetch {
  return (async () =>
    ({
      ok: response.ok,
      status: response.status,
      json: response.json ?? (async () => ({})),
    }) as unknown as Response) as typeof fetch;
}

describe('companionApi.deleteMedia', () => {
  test('resolves without throwing on a 2xx (204 No Content) response', async () => {
    const { companionApi } = await loadModule();
    const originalFetch = globalThis.fetch;
    let calledMethod: string | undefined;
    globalThis.fetch = (async (_input: unknown, init?: RequestInit) => {
      calledMethod = init?.method;
      return { ok: true, status: 204, json: async () => ({}) } as unknown as Response;
    }) as typeof fetch;
    try {
      await assert.doesNotReject(() => companionApi.deleteMedia('clip.mp4'));
      assert.equal(calledMethod, 'DELETE');
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test('throws with the server-provided error message on a non-2xx response', async () => {
    const { companionApi } = await loadModule();
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fakeFetch({ ok: false, status: 404, json: async () => ({ error: 'Media file not found' }) });
    try {
      await assert.rejects(() => companionApi.deleteMedia('missing.mp4'), /Media file not found/);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test('falls back to a generic status-coded message when the error body has no message', async () => {
    const { companionApi } = await loadModule();
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fakeFetch({ ok: false, status: 500, json: async () => ({}) });
    try {
      await assert.rejects(() => companionApi.deleteMedia('clip.mp4'), /Delete failed \(500\)/);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  test('falls back to a generic message when the error body is not valid JSON', async () => {
    const { companionApi } = await loadModule();
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fakeFetch({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error('response body is not JSON');
      },
    });
    try {
      await assert.rejects(() => companionApi.deleteMedia('clip.mp4'), /Delete failed \(500\)/);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
