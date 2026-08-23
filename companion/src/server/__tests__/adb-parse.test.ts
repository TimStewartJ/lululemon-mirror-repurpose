import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import {
  parseAdbDevicesOutput,
  parseGetprop,
  findAdbExecutable,
  buildReverseArgs,
  buildRemoveReverseArgs,
} from '../adb';

describe('parseAdbDevicesOutput', () => {
  test('parses a typical devices -l listing with one authorized device', () => {
    const output = [
      'List of devices attached',
      '0123456789ABCDEF       device product:mirror model:Mirror device:msm8916_64 transport_id:1',
      '',
    ].join('\n');

    const devices = parseAdbDevicesOutput(output);
    assert.equal(devices.length, 1);
    assert.deepEqual(devices[0], {
      serial: '0123456789ABCDEF',
      state: 'device',
      properties: {
        product: 'mirror',
        model: 'Mirror',
        device: 'msm8916_64',
        transport_id: '1',
      },
    });
  });

  test('parses multiple devices including unauthorized state', () => {
    const output = [
      'List of devices attached',
      'AAA111 device product:mirror model:Mirror device:msm8916_64 transport_id:1',
      'BBB222 unauthorized transport_id:2',
      '',
    ].join('\n');

    const devices = parseAdbDevicesOutput(output);
    assert.equal(devices.length, 2);
    assert.equal(devices[0]?.serial, 'AAA111');
    assert.equal(devices[1]?.serial, 'BBB222');
    assert.equal(devices[1]?.state, 'unauthorized');
    assert.deepEqual(devices[1]?.properties, { transport_id: '2' });
  });

  test('handles CRLF line endings', () => {
    const output = 'List of devices attached\r\nAAA111 device product:mirror\r\n';
    const devices = parseAdbDevicesOutput(output);
    assert.equal(devices.length, 1);
    assert.equal(devices[0]?.serial, 'AAA111');
  });

  test('returns an empty array when no devices are attached', () => {
    const output = 'List of devices attached\n\n';
    assert.deepEqual(parseAdbDevicesOutput(output), []);
  });

  test('ignores adb server startup banner lines', () => {
    const output = [
      '* daemon not running; starting now at tcp:5037',
      '* daemon started successfully',
      'List of devices attached',
      'AAA111 device product:mirror',
      '',
    ].join('\n');
    const devices = parseAdbDevicesOutput(output);
    assert.equal(devices.length, 1);
    assert.equal(devices[0]?.serial, 'AAA111');
  });
});

describe('parseGetprop', () => {
  test('parses standard bracketed getprop output', () => {
    const output = [
      '[ro.product.device]: [msm8916_64]',
      '[ro.product.name]: [mirror]',
      '[ro.build.id]: [IFC-6309-2.0-MIR]',
      '[ro.build.fingerprint]: [mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/329:user/release-keys]',
    ].join('\n');

    const props = parseGetprop(output);
    assert.equal(props['ro.product.device'], 'msm8916_64');
    assert.equal(props['ro.product.name'], 'mirror');
    assert.equal(props['ro.build.id'], 'IFC-6309-2.0-MIR');
    assert.equal(
      props['ro.build.fingerprint'],
      'mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/329:user/release-keys',
    );
  });

  test('handles empty bracketed values', () => {
    const props = parseGetprop('[ro.serialno]: []');
    assert.equal(props['ro.serialno'], '');
  });

  test('ignores malformed or unrelated lines', () => {
    const output = ['not a valid line', '[ro.debuggable]: [0]', ''].join('\n');
    const props = parseGetprop(output);
    assert.deepEqual(props, { 'ro.debuggable': '0' });
  });

  test('handles CRLF line endings', () => {
    const props = parseGetprop('[ro.debuggable]: [0]\r\n[ro.secure]: [1]\r\n');
    assert.deepEqual(props, { 'ro.debuggable': '0', 'ro.secure': '1' });
  });
});

describe('findAdbExecutable', () => {
  test('prefers ADB_PATH when the file exists', () => {
    const result = findAdbExecutable({
      env: { ADB_PATH: '/custom/adb', PATH: '/usr/bin' },
      platform: 'linux',
      exists: (candidate) => candidate === '/custom/adb',
    });
    assert.equal(result, '/custom/adb');
  });

  test('falls back to ANDROID_SDK_ROOT platform-tools when ADB_PATH is unset', () => {
    const result = findAdbExecutable({
      env: { ANDROID_SDK_ROOT: '/opt/android-sdk', PATH: '/usr/bin' },
      platform: 'linux',
      exists: (candidate) => candidate === '/opt/android-sdk/platform-tools/adb',
    });
    assert.equal(result, '/opt/android-sdk/platform-tools/adb');
  });

  test('falls back to ANDROID_HOME when ANDROID_SDK_ROOT is unset', () => {
    const result = findAdbExecutable({
      env: { ANDROID_HOME: '/opt/sdk-home', PATH: '/usr/bin' },
      platform: 'linux',
      exists: (candidate) => candidate === '/opt/sdk-home/platform-tools/adb',
    });
    assert.equal(result, '/opt/sdk-home/platform-tools/adb');
  });

  test('falls back to PATH entries using the .exe suffix on win32', () => {
    const result = findAdbExecutable({
      env: { PATH: ['C:\\tools\\other', 'C:\\tools\\platform-tools'].join(';') },
      platform: 'win32',
      exists: (candidate) => candidate === 'C:\\tools\\platform-tools\\adb.exe',
    });
    assert.equal(result, 'C:\\tools\\platform-tools\\adb.exe');
  });

  test('returns null when no candidate exists', () => {
    const result = findAdbExecutable({
      env: { PATH: '/usr/bin' },
      platform: 'linux',
      exists: () => false,
    });
    assert.equal(result, null);
  });

  test('ADB_PATH takes priority even when SDK env vars are also set', () => {
    const result = findAdbExecutable({
      env: {
        ADB_PATH: '/explicit/adb',
        ANDROID_SDK_ROOT: '/opt/android-sdk',
        PATH: '/usr/bin',
      },
      platform: 'linux',
      exists: () => true,
    });
    assert.equal(result, '/explicit/adb');
  });
});

describe('adb reverse argument builders', () => {
  test('buildReverseArgs uses a fixed device-side port for Android 6', () => {
    assert.deepEqual(buildReverseArgs('ABCDEF', 14317, 4317), [
      '-s',
      'ABCDEF',
      'reverse',
      'tcp:14317',
      'tcp:4317',
    ]);
  });

  test('buildRemoveReverseArgs targets the specific device-side port to remove', () => {
    assert.deepEqual(buildRemoveReverseArgs('ABCDEF', 40123), [
      '-s',
      'ABCDEF',
      'reverse',
      '--remove',
      'tcp:40123',
    ]);
  });

  test('argument arrays never concatenate serial/port into a single shell string', () => {
    // Cross-platform + injection safety: every element must be its own array
    // entry so execFile never needs shell interpretation.
    const args = buildReverseArgs('some; ; injected', 14317, 4317);
    assert.equal(args.includes('some; ; injected'), true);
    assert.equal(args.some((arg) => arg.includes(' ') && arg !== 'some; ; injected'), false);
  });
});
