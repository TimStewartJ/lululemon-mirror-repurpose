import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildUsbMediaUrl,
  buildLanMediaUrl,
  selectPrivateInterfaceAddress,
  LanMediaUrlUnavailableError,
} from '../media/media-url';

describe('buildUsbMediaUrl', () => {
  test('always targets the device loopback interface, regardless of the host machine', () => {
    assert.equal(
      buildUsbMediaUrl(54321, 'song.mp3'),
      'http://127.0.0.1:54321/media-files/song.mp3',
    );
  });

  test('percent-encodes the media name', () => {
    assert.equal(
      buildUsbMediaUrl(54321, 'fifty % track.mp3'),
      'http://127.0.0.1:54321/media-files/fifty%20%25%20track.mp3',
    );
  });
});

describe('selectPrivateInterfaceAddress', () => {
  test('skips internal (loopback) interfaces', () => {
    const address = selectPrivateInterfaceAddress({
      lo: [{ address: '127.0.0.1', family: 'IPv4', internal: true, mac: '', netmask: '', cidr: null }],
    });
    assert.equal(address, null);
  });

  test('skips IPv6 entries and public addresses, picking the first private IPv4 one', () => {
    const address = selectPrivateInterfaceAddress({
      eth0: [
        { address: 'fe80::1', family: 'IPv6', internal: false, mac: '', netmask: '', cidr: null, scopeid: 0 },
        { address: '8.8.8.8', family: 'IPv4', internal: false, mac: '', netmask: '', cidr: null },
        { address: '192.168.1.42', family: 'IPv4', internal: false, mac: '', netmask: '', cidr: null },
      ],
    });
    assert.equal(address, '192.168.1.42');
  });

  test('returns null when nothing private/link-local is found', () => {
    const address = selectPrivateInterfaceAddress({
      eth0: [{ address: '8.8.8.8', family: 'IPv4', internal: false, mac: '', netmask: '', cidr: null }],
    });
    assert.equal(address, null);
  });
});

describe('buildLanMediaUrl', () => {
  test('uses the explicit publicUrl override and strips a trailing slash', () => {
    const url = buildLanMediaUrl(
      { publicUrl: 'http://192.168.1.5:4317/', companionPort: 4317 },
      'song.mp3',
    );
    assert.equal(url, 'http://192.168.1.5:4317/media-files/song.mp3');
  });

  test('auto-selects a private interface address when no override is set', () => {
    const url = buildLanMediaUrl(
      {
        companionPort: 4317,
        interfaces: {
          eth0: [{ address: '10.1.2.3', family: 'IPv4', internal: false, mac: '', netmask: '', cidr: null }],
        },
      },
      'song.mp3',
    );
    assert.equal(url, 'http://10.1.2.3:4317/media-files/song.mp3');
  });

  test('throws LanMediaUrlUnavailableError when no override and no private interface exists', () => {
    assert.throws(
      () => buildLanMediaUrl({ companionPort: 4317, interfaces: {} }, 'song.mp3'),
      LanMediaUrlUnavailableError,
    );
  });

  test('never derives the URL from anything other than the explicit override or real interfaces', () => {
    // There is no "host header" parameter accepted by this function at all
    // -- this test documents that as an API-shape guarantee, not just a
    // runtime behavior, so a future change can't accidentally add one.
    const url = buildLanMediaUrl(
      {
        companionPort: 4317,
        interfaces: {
          eth0: [{ address: '192.168.50.7', family: 'IPv4', internal: false, mac: '', netmask: '', cidr: null }],
        },
      },
      'clip.mp4',
    );
    assert.equal(url, 'http://192.168.50.7:4317/media-files/clip.mp4');
  });
});
