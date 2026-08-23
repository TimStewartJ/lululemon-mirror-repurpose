import { test, describe, beforeEach, afterEach, mock } from 'node:test';
import assert from 'node:assert/strict';
// Plain `import ... = require(...)` (not `import * as`) so this resolves to
// the exact same cached module object `adb.ts`'s own `require('node:
// child_process')` uses. `import * as` gets downleveled through TypeScript's
// `__importStar` helper, which -- for a native (non-`__esModule`) built-in
// like child_process -- copies properties onto a *new* plain object rather
// than returning the module singleton, so mocking that copy would never be
// observed by adb.ts's own reference to the real module.
import childProcess = require('node:child_process');
import { setupReverse } from '../adb';

type ExecFileCallback = (error: Error | null, stdout: string, stderr: string) => void;

/**
 * Installs a fake `child_process.execFile` for the duration of a test,
 * matching the exact 4-argument call shape `adb.ts`'s internal `run()`
 * helper uses (file, args, options, callback). Returns the recorded
 * argument arrays for assertions.
 */
function mockExecFile(
  handler: (file: string, args: string[]) => { stdout?: string; stderr?: string; error?: Error },
): string[][] {
  const calls: string[][] = [];
  mock.method(
    childProcess,
    'execFile',
    ((file: string, args: string[], _options: unknown, callback: ExecFileCallback) => {
      calls.push(args);
      const result = handler(file, args);
      if (result.error) {
        callback(result.error, '', result.stderr ?? '');
      } else {
        callback(null, result.stdout ?? '', result.stderr ?? '');
      }
      return {} as ReturnType<typeof childProcess.execFile>;
    }) as unknown as typeof childProcess.execFile,
  );
  return calls;
}

// Regression coverage for the fixed-port-with-fallback compatibility fix:
// the on-device adb build (Android 6, this Mirror's platform) never reports
// a device-side port back for a dynamically allocated `adb reverse tcp:0
// ...`, so setupReverse must try a preferred fixed port and a bounded range
// of fallback ports instead, per buildReverseArgs/MIRROR_MEDIA_REVERSE_PORT.
describe('setupReverse (fixed device-side port with bounded fallback)', () => {
  beforeEach(() => {
    mock.restoreAll();
  });

  afterEach(() => {
    mock.restoreAll();
  });

  test('succeeds immediately on the preferred port with no retries', async () => {
    const calls = mockExecFile(() => ({ stdout: '' }));
    const result = await setupReverse('/fake/adb', 'ABC123', 14317, 4317);
    assert.equal(result.devicePort, 14317);
    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0], ['-s', 'ABC123', 'reverse', 'tcp:14317', 'tcp:4317']);
  });

  test('falls back to the next sequential port when the preferred one is already bound', async () => {
    const calls = mockExecFile((_file, args) => {
      if (args.includes('tcp:14317')) {
        return { error: new Error('error: cannot bind listener: Address already in use') };
      }
      return { stdout: '' };
    });
    const result = await setupReverse('/fake/adb', 'ABC123', 14317, 4317);
    assert.equal(result.devicePort, 14318);
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[1], ['-s', 'ABC123', 'reverse', 'tcp:14318', 'tcp:4317']);
  });

  test('tries up to ten sequential ports before giving up', async () => {
    const calls = mockExecFile((_file, args) => {
      // Every port except the 10th (offset 9) fails.
      if (args.includes('tcp:20009')) {
        return { stdout: '' };
      }
      return { error: new Error('Address already in use') };
    });
    const result = await setupReverse('/fake/adb', 'ABC123', 20000, 4317);
    assert.equal(result.devicePort, 20009);
    assert.equal(calls.length, 10);
  });

  test('throws a descriptive error after exhausting all ten fallback ports', async () => {
    mockExecFile(() => ({ error: new Error('adb: error: failed to reverse') }));
    await assert.rejects(
      () => setupReverse('/fake/adb', 'ABC123', 20000, 4317),
      /Unable to establish adb reverse on ports 20000-20009/,
    );
  });
});
