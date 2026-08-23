import { test, describe, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { ConfigStore } from '../config-store';

describe('ConfigStore', () => {
  let dataDir: string;

  beforeEach(() => {
    dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'companion-config-test-'));
  });

  afterEach(() => {
    fs.rmSync(dataDir, { recursive: true, force: true });
  });

  test('starts with no token before anything is saved', () => {
    const store = new ConfigStore(dataDir);
    assert.equal(store.getToken(), null);
  });

  test('persists a token across a fresh ConfigStore instance', () => {
    const store = new ConfigStore(dataDir);
    store.setToken('abc123');
    assert.equal(store.getToken(), 'abc123');

    const reloaded = new ConfigStore(dataDir);
    assert.equal(reloaded.getToken(), 'abc123');
  });

  test('clears a token when set to null (e.g. after revoke)', () => {
    const store = new ConfigStore(dataDir);
    store.setToken('abc123');
    store.setToken(null);
    assert.equal(store.getToken(), null);

    const reloaded = new ConfigStore(dataDir);
    assert.equal(reloaded.getToken(), null);
  });

  test('creates the data directory if it does not yet exist', () => {
    const nestedDir = path.join(dataDir, 'nested', 'subdir');
    const store = new ConfigStore(nestedDir);
    store.setToken('token-value');
    assert.ok(fs.existsSync(path.join(nestedDir, 'config.json')));
  });

  test('never has a passphrase field written to disk', () => {
    const store = new ConfigStore(dataDir);
    store.setToken('token-value');
    store.setDashboardUrl('https://dashboard.example.com');
    store.setDisplayName('Kitchen Mirror');

    const raw = fs.readFileSync(path.join(dataDir, 'config.json'), 'utf8');
    assert.ok(!raw.toLowerCase().includes('passphrase'));
    assert.ok(!raw.toLowerCase().includes('password'));
  });

  test('applies an owner-only file mode on POSIX platforms', { skip: process.platform === 'win32' }, () => {
    const store = new ConfigStore(dataDir);
    store.setToken('token-value');
    const stat = fs.statSync(path.join(dataDir, 'config.json'));
    assert.equal(stat.mode & 0o777, 0o600);
  });

  test('caches dashboard URL and display name alongside the token', () => {
    const store = new ConfigStore(dataDir);
    store.setToken('token-value');
    store.setDashboardUrl('https://dashboard.example.com');
    store.setDisplayName('Kitchen Mirror');

    const reloaded = new ConfigStore(dataDir);
    const config = reloaded.load();
    assert.equal(config.token, 'token-value');
    assert.equal(config.dashboardUrl, 'https://dashboard.example.com');
    assert.equal(config.displayName, 'Kitchen Mirror');
  });
});
