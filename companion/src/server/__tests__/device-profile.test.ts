import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { validateDeviceProfile, SUPPORTED_DEVICE_PROFILE } from '../device-profile';

describe('validateDeviceProfile', () => {
  const matchingProps = {
    'ro.product.name': 'mirror',
    'ro.product.device': 'msm8916_64',
    'ro.build.id': 'IFC-6309-2.0-MIR',
    'ro.build.fingerprint':
      'mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/329:user/release-keys',
  };

  test('accepts an exact match against the supported profile', () => {
    const result = validateDeviceProfile(matchingProps);
    assert.equal(result.valid, true);
    assert.deepEqual(result.reasons, []);
  });

  test('falls back to ro.build.product when ro.product.name is absent', () => {
    const { 'ro.product.name': _omit, ...rest } = matchingProps;
    const result = validateDeviceProfile({ ...rest, 'ro.build.product': 'mirror' });
    assert.equal(result.valid, true);
  });

  test('rejects a device with a mismatched product', () => {
    const result = validateDeviceProfile({ ...matchingProps, 'ro.product.name': 'other-device' });
    assert.equal(result.valid, false);
    assert.ok(result.reasons.some((r) => r.includes('product')));
  });

  test('rejects a device with a mismatched device codename', () => {
    const result = validateDeviceProfile({ ...matchingProps, 'ro.product.device': 'msm8940' });
    assert.equal(result.valid, false);
    assert.ok(result.reasons.some((r) => r.includes('device')));
  });

  test('rejects a device with a mismatched build ID', () => {
    const result = validateDeviceProfile({ ...matchingProps, 'ro.build.id': 'SOMETHING-ELSE' });
    assert.equal(result.valid, false);
    assert.ok(result.reasons.some((r) => r.includes('build ID')));
  });

  test('rejects a device with a mismatched fingerprint', () => {
    const result = validateDeviceProfile({ ...matchingProps, 'ro.build.fingerprint': 'bogus' });
    assert.equal(result.valid, false);
    assert.ok(result.reasons.some((r) => r.includes('fingerprint')));
  });

  test('reports every mismatch simultaneously', () => {
    const result = validateDeviceProfile({});
    assert.equal(result.valid, false);
    assert.equal(result.reasons.length, 4);
  });

  test('exposes the exact supported profile identifiers used by docs/supported-devices.md', () => {
    assert.equal(SUPPORTED_DEVICE_PROFILE.id, 'ifc6309-mirror-329');
    assert.equal(SUPPORTED_DEVICE_PROFILE.product, 'mirror');
    assert.equal(SUPPORTED_DEVICE_PROFILE.device, 'msm8916_64');
  });
});
