import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

// ble-protocol.ts is browser-only source (native ESM, compiled by
// tsconfig.client.json into dist/client/ble-protocol.js). It has no
// navigator.bluetooth or DOM dependency -- only TextEncoder/TextDecoder --
// so we exercise the *compiled* module directly via a dynamic import
// rather than duplicating the chunking/framing logic in server-side code
// just to make it unit-testable. dist/client/package.json (written by
// scripts/copy-assets.mjs) marks that directory as "type": "module" so
// Node's loader parses it as ESM.
const moduleUrl = pathToFileURL(
  path.join(__dirname, '..', '..', 'client', 'ble-protocol.js'),
).href;

// Hand-written shape instead of `typeof import('../../client/...')`, since
// the server program's rootDir (src/server) must not pull in client
// sources just for type information.
interface ProvisionRequest {
  code: string;
  ssid: string;
  passphrase: string;
  hidden: boolean;
}

interface ProvisionResponse {
  ok: boolean;
  token?: string;
  message?: string;
  ipAddress?: string | null;
  apiPort?: number;
  error?: string;
}

interface ResponseAssembler {
  push(chunk: Uint8Array): boolean;
  isComplete(): boolean;
  takeResponse(): ProvisionResponse;
}

interface BleProtocolModule {
  MAX_REQUEST_CHUNK_BYTES: number;
  chunkBytes(bytes: Uint8Array, maxBytes: number): Uint8Array[];
  encodeProvisionRequest(request: ProvisionRequest): Uint8Array[];
  createResponseAssembler(): ResponseAssembler;
}

async function loadModule(): Promise<BleProtocolModule> {
  // TypeScript, when compiling to CommonJS, rewrites a literal `import(...)`
  // expression into `require(...)`, which cannot load a real ES module (and
  // require() doesn't accept file:// URLs anyway). Wrapping the import in a
  // `new Function(...)` body hides it from TypeScript's downlevel
  // transform, so this performs a genuine dynamic import at runtime.
  const dynamicImport = new Function('specifier', 'return import(specifier);') as (
    specifier: string,
  ) => Promise<BleProtocolModule>;
  return dynamicImport(moduleUrl);
}

describe('chunkBytes', () => {
  test('splits bytes into chunks no larger than maxBytes', async () => {
    const { chunkBytes } = await loadModule();
    const bytes = new Uint8Array(45).map((_, i) => i);
    const chunks = chunkBytes(bytes, 18);
    assert.equal(chunks.length, 3);
    assert.equal(chunks[0]?.length, 18);
    assert.equal(chunks[1]?.length, 18);
    assert.equal(chunks[2]?.length, 9);
    const reassembled = Uint8Array.from(chunks.flatMap((c) => Array.from(c)));
    assert.deepEqual(reassembled, bytes);
  });

  test('returns a single empty chunk for empty input', async () => {
    const { chunkBytes } = await loadModule();
    const chunks = chunkBytes(new Uint8Array(0), 18);
    assert.equal(chunks.length, 1);
    assert.equal(chunks[0]?.length, 0);
  });

  test('rejects a non-positive maxBytes', async () => {
    const { chunkBytes } = await loadModule();
    assert.throws(() => chunkBytes(new Uint8Array([1]), 0));
  });
});

describe('encodeProvisionRequest', () => {
  test('produces newline-terminated JSON split into <=18-byte chunks', async () => {
    const { encodeProvisionRequest, MAX_REQUEST_CHUNK_BYTES } = await loadModule();
    const chunks = encodeProvisionRequest({
      code: '123456',
      ssid: 'MyHomeNetworkWithALongName',
      passphrase: 'super-secret-passphrase',
      hidden: false,
    });
    assert.ok(chunks.length > 1, 'expected the request to require multiple chunks');
    for (const chunk of chunks) {
      assert.ok(chunk.length <= MAX_REQUEST_CHUNK_BYTES);
    }

    const decoder = new TextDecoder();
    const totalBytes = chunks.reduce((sum, c) => sum + c.length, 0);
    const combined = new Uint8Array(totalBytes);
    let offset = 0;
    for (const chunk of chunks) {
      combined.set(chunk, offset);
      offset += chunk.length;
    }
    const text = decoder.decode(combined);
    assert.ok(text.endsWith('\n'));
    const parsed = JSON.parse(text.trimEnd());
    assert.deepEqual(parsed, {
      type: 'provision',
      code: '123456',
      ssid: 'MyHomeNetworkWithALongName',
      passphrase: 'super-secret-passphrase',
      hidden: false,
    });
  });

  test('splits a multi-byte UTF-8 sequence safely across chunk boundaries', async () => {
    const { encodeProvisionRequest } = await loadModule();
    // A non-ASCII SSID forces multi-byte UTF-8 sequences near a chunk
    // boundary; the device buffers raw bytes so a split sequence must
    // still decode correctly once reassembled.
    const chunks = encodeProvisionRequest({
      code: '000000',
      ssid: 'Café-Réseau-日本語-ssid',
      passphrase: 'pw',
      hidden: true,
    });
    const decoder = new TextDecoder();
    const totalBytes = chunks.reduce((sum, c) => sum + c.length, 0);
    const combined = new Uint8Array(totalBytes);
    let offset = 0;
    for (const chunk of chunks) {
      combined.set(chunk, offset);
      offset += chunk.length;
    }
    const parsed = JSON.parse(decoder.decode(combined).trimEnd());
    assert.equal(parsed.ssid, 'Café-Réseau-日本語-ssid');
  });
});

describe('createResponseAssembler', () => {
  test('assembles a response delivered across multiple notification chunks', async () => {
    const { createResponseAssembler } = await loadModule();
    const encoder = new TextEncoder();
    const assembler = createResponseAssembler();
    const full = encoder.encode(
      '{"ok":true,"token":"abc123","ipAddress":"192.168.1.20","apiPort":8787}\n',
    );

    let done = false;
    for (let i = 0; i < full.length; i += 10) {
      done = assembler.push(full.slice(i, i + 10));
      if (done) break;
    }
    assert.equal(done, true);
    assert.equal(assembler.isComplete(), true);
    const response = assembler.takeResponse();
    assert.deepEqual(response, {
      ok: true,
      token: 'abc123',
      ipAddress: '192.168.1.20',
      apiPort: 8787,
    });
    assert.equal(assembler.isComplete(), false);
  });

  test('handles the newline arriving in the same chunk as the closing brace', async () => {
    const { createResponseAssembler } = await loadModule();
    const encoder = new TextEncoder();
    const assembler = createResponseAssembler();
    const done = assembler.push(encoder.encode('{"ok":false,"error":"bad code"}\n'));
    assert.equal(done, true);
    assert.deepEqual(assembler.takeResponse(), { ok: false, error: 'bad code' });
  });

  test('throws when takeResponse is called before a newline is seen', async () => {
    const { createResponseAssembler } = await loadModule();
    const encoder = new TextEncoder();
    const assembler = createResponseAssembler();
    assembler.push(encoder.encode('{"ok":true'));
    assert.throws(() => assembler.takeResponse());
  });

  test('rejects malformed JSON once terminated', async () => {
    const { createResponseAssembler } = await loadModule();
    const encoder = new TextEncoder();
    const assembler = createResponseAssembler();
    assembler.push(encoder.encode('not json\n'));
    assert.throws(() => assembler.takeResponse());
  });

  test('rejects a response missing the required "ok" field', async () => {
    const { createResponseAssembler } = await loadModule();
    const encoder = new TextEncoder();
    const assembler = createResponseAssembler();
    assembler.push(encoder.encode('{"token":"abc"}\n'));
    assert.throws(() => assembler.takeResponse());
  });

  test('can be reused for a second response after consuming the first', async () => {
    const { createResponseAssembler } = await loadModule();
    const encoder = new TextEncoder();
    const assembler = createResponseAssembler();
    assembler.push(encoder.encode('{"ok":true}\n'));
    assembler.takeResponse();
    const done = assembler.push(encoder.encode('{"ok":false,"message":"second"}\n'));
    assert.equal(done, true);
    assert.deepEqual(assembler.takeResponse(), { ok: false, message: 'second' });
  });

  test('discards an unconsumed complete response before starting the next one', async () => {
    const { createResponseAssembler } = await loadModule();
    const encoder = new TextEncoder();
    const assembler = createResponseAssembler();
    assembler.push(encoder.encode('{"ok":true}\n'));
    // Never called takeResponse() here -- push again without consuming.
    const done = assembler.push(encoder.encode('{"ok":false,"message":"fresh"}\n'));
    assert.equal(done, true);
    assert.deepEqual(assembler.takeResponse(), { ok: false, message: 'fresh' });
  });
});
