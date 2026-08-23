import { test, describe, beforeEach, afterEach, mock } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { ConfigStore } from '../config-store';
import { DeviceManager, type UsbConnectionState } from '../device-manager';
import * as adb from '../adb';

// These tests exercise ensureUsbReverseTunnel()'s memoization/serialization
// directly against a real DeviceManager, with only the adb module's
// process-spawning functions mocked out (findAdbExecutable/setupReverse/
// removeForward). The USB connection itself is seeded onto the manager's
// private `connection` field rather than going through connect(), since
// connect() requires a real adb binary and device -- that flow is covered
// elsewhere by the pure adb argument-builder tests and the router-level
// fakes.
describe('DeviceManager USB adb reverse tunnel (concurrency)', () => {
  let dataDir: string;
  let manager: DeviceManager;
  let configStore: ConfigStore;

  beforeEach(() => {
    dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'companion-device-manager-reverse-test-'));
    configStore = new ConfigStore(dataDir);
    manager = new DeviceManager(configStore, 18787, 8787);
    mock.method(adb, 'findAdbExecutable', () => '/fake/adb');
    const seeded: UsbConnectionState = {
      mode: 'usb',
      serial: 'ABC123',
      localPort: 18787,
      reverseDevicePort: null,
    };
    (manager as unknown as { connection: UsbConnectionState }).connection = seeded;
  });

  afterEach(() => {
    mock.restoreAll();
    fs.rmSync(dataDir, { recursive: true, force: true });
  });

  test('concurrent callers share exactly one in-flight adb reverse invocation', async () => {
    let resolveSetup!: (value: { devicePort: number }) => void;
    const pendingSetup = new Promise<{ devicePort: number }>((resolve) => {
      resolveSetup = resolve;
    });
    const setupReverseMock = mock.method(adb, 'setupReverse', async () => pendingSetup);

    const call1 = manager.ensureUsbReverseTunnel(4317);
    const call2 = manager.ensureUsbReverseTunnel(4317);
    const call3 = manager.ensureUsbReverseTunnel(4317);

    // Let any pending microtasks run before resolving, so all three calls
    // have definitely reached (and shared) the in-flight check.
    await Promise.resolve();
    await Promise.resolve();
    resolveSetup({ devicePort: 41000 });

    const [port1, port2, port3] = await Promise.all([call1, call2, call3]);

    assert.equal(setupReverseMock.mock.callCount(), 1, 'adb reverse must run exactly once for concurrent callers');
    assert.equal(port1, 41000);
    assert.equal(port2, 41000);
    assert.equal(port3, 41000);
    const connection = manager.getConnection();
    assert.equal(connection?.mode, 'usb');
    assert.equal((connection as UsbConnectionState).reverseDevicePort, 41000);
  });

  test('a later call reuses the persisted port without invoking adb reverse again', async () => {
    mock.method(adb, 'setupReverse', async () => ({ devicePort: 42000 }));
    const first = await manager.ensureUsbReverseTunnel(4317);
    assert.equal(first, 42000);

    const secondCallSpy = mock.method(adb, 'setupReverse', async () => {
      throw new Error('adb reverse should not run again once a port is persisted');
    });
    const second = await manager.ensureUsbReverseTunnel(4317);
    assert.equal(second, 42000);
    assert.equal(secondCallSpy.mock.callCount(), 0);
  });

  test('clears the in-flight state on failure so a subsequent call can retry', async () => {
    mock.method(adb, 'setupReverse', async () => {
      throw new Error('adb reverse failed');
    });
    await assert.rejects(() => manager.ensureUsbReverseTunnel(4317), /adb reverse failed/);

    mock.method(adb, 'setupReverse', async () => ({ devicePort: 43000 }));
    const retried = await manager.ensureUsbReverseTunnel(4317);
    assert.equal(retried, 43000);
  });

  test('clears the in-flight state on disconnect so it is not resurrected onto a stale connection', async () => {
    let resolveSetup!: (value: { devicePort: number }) => void;
    const pendingSetup = new Promise<{ devicePort: number }>((resolve) => {
      resolveSetup = resolve;
    });
    mock.method(adb, 'setupReverse', async () => pendingSetup);
    mock.method(adb, 'removeForward', async () => {});

    const pending = manager.ensureUsbReverseTunnel(4317);
    await manager.disconnect();
    resolveSetup({ devicePort: 44000 });

    // The setup call still resolves (it was already in flight), but its
    // result must not be written onto the manager's connection state,
    // which disconnect() has already cleared.
    const port = await pending;
    assert.equal(port, 44000);
    assert.equal(manager.getConnection(), null);

    // A fresh USB connection must be able to establish its own tunnel
    // rather than inheriting anything from the disconnected attempt.
    const reseeded: UsbConnectionState = {
      mode: 'usb',
      serial: 'XYZ789',
      localPort: 18787,
      reverseDevicePort: null,
    };
    (manager as unknown as { connection: UsbConnectionState }).connection = reseeded;
    mock.method(adb, 'setupReverse', async () => ({ devicePort: 45000 }));
    const fresh = await manager.ensureUsbReverseTunnel(4317);
    assert.equal(fresh, 45000);
  });
});
