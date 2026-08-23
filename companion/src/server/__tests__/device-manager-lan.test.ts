import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { ConfigStore } from '../config-store';
import { DeviceManager } from '../device-manager';

// These tests exercise the LAN connection mode end-to-end against a real
// DeviceManager + ConfigStore (no adb involved at all for this mode), to
// verify the RFC1918/link-local validation gate and the "token persisted,
// passphrase never touched" invariant. USB-only behavior (adb
// forward/reverse) is covered indirectly by the router-level fakes and the
// pure adb argument-builder tests, since it requires an actual adb binary.
describe('DeviceManager LAN connection mode', () => {
  let dataDir: string;
  let manager: DeviceManager;
  let configStore: ConfigStore;

  beforeEach(() => {
    dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'companion-device-manager-test-'));
    configStore = new ConfigStore(dataDir);
    manager = new DeviceManager(configStore, 18787, 8787);
  });

  afterEach(() => {
    fs.rmSync(dataDir, { recursive: true, force: true });
  });

  test('connects successfully to a private IPv4 host and persists the token', async () => {
    const state = await manager.connectLan('192.168.1.42', 'secret-token');
    assert.deepEqual(state, { mode: 'lan', host: '192.168.1.42', port: 8787 });
    assert.deepEqual(manager.getConnection(), { mode: 'lan', host: '192.168.1.42', port: 8787 });
    assert.equal(configStore.getToken(), 'secret-token');
  });

  test('accepts a link-local (169.254.0.0/16) address', async () => {
    const state = await manager.connectLan('169.254.10.20', 'token-2');
    assert.equal(state.host, '169.254.10.20');
  });

  test('rejects a public IPv4 address (SSRF guard)', async () => {
    await assert.rejects(() => manager.connectLan('8.8.8.8', 'token'), /private.*RFC1918|link-local/i);
    assert.equal(manager.getConnection(), null);
    assert.equal(configStore.getToken(), null);
  });

  test('rejects loopback even though it is otherwise a valid IPv4 literal', async () => {
    await assert.rejects(() => manager.connectLan('127.0.0.1', 'token'));
    assert.equal(manager.getConnection(), null);
  });

  test('rejects a hostname (no DNS resolution allowed)', async () => {
    await assert.rejects(() => manager.connectLan('mirror.local', 'token'));
    assert.equal(manager.getConnection(), null);
  });

  test('rejects an empty token', async () => {
    await assert.rejects(() => manager.connectLan('10.0.0.5', ''));
    assert.equal(manager.getConnection(), null);
  });

  test('getClient() targets the LAN host/port with the stored token', async () => {
    await manager.connectLan('10.1.2.3', 'lan-token');
    const client = manager.getClient() as unknown as { options: { host?: string; port: number; token: string | null } };
    assert.equal(client.options.host, '10.1.2.3');
    assert.equal(client.options.port, 8787);
    assert.equal(client.options.token, 'lan-token');
  });

  test('disconnect() on a LAN connection clears state without any adb interaction', async () => {
    await manager.connectLan('10.1.2.3', 'lan-token');
    await manager.disconnect();
    assert.equal(manager.getConnection(), null);
    // The token itself is intentionally left in place across disconnect
    // (it's a durable pairing credential, not connection-session state).
    assert.equal(configStore.getToken(), 'lan-token');
  });

  test('disconnect() is a no-op when nothing is connected', async () => {
    await assert.doesNotReject(() => manager.disconnect());
    assert.equal(manager.getConnection(), null);
  });

  test('ensureUsbReverseTunnel() rejects when connected over LAN, not USB', async () => {
    await manager.connectLan('10.1.2.3', 'lan-token');
    await assert.rejects(() => manager.ensureUsbReverseTunnel(4317), /USB/);
  });

  test('getClient() throws when nothing is connected', () => {
    assert.throws(() => manager.getClient(), /No device connected/);
  });
});
