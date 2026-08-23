import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { isPrivateIPv4Host, parseIPv4 } from '../net-validation';

describe('parseIPv4', () => {
  test('parses a well-formed dotted quad', () => {
    assert.deepEqual(parseIPv4('192.168.1.20'), [192, 168, 1, 20]);
  });

  test('accepts a single "0" octet but rejects leading zeros', () => {
    assert.deepEqual(parseIPv4('10.0.0.1'), [10, 0, 0, 1]);
    assert.equal(parseIPv4('10.00.0.1'), null);
    assert.equal(parseIPv4('010.0.0.1'), null);
  });

  test('rejects out-of-range octets', () => {
    assert.equal(parseIPv4('256.0.0.1'), null);
    assert.equal(parseIPv4('192.168.1.999'), null);
  });

  test('rejects hostnames', () => {
    assert.equal(parseIPv4('mirror.local'), null);
    assert.equal(parseIPv4('localhost'), null);
  });

  test('rejects IPv6 literals', () => {
    assert.equal(parseIPv4('::1'), null);
    assert.equal(parseIPv4('fe80::1'), null);
  });

  test('rejects surrounding text and whitespace-only garbage', () => {
    assert.equal(parseIPv4('http://192.168.1.20'), null);
    assert.equal(parseIPv4('192.168.1.20/24'), null);
    assert.equal(parseIPv4(''), null);
  });

  test('trims surrounding whitespace on an otherwise valid address', () => {
    assert.deepEqual(parseIPv4('  192.168.1.20  '), [192, 168, 1, 20]);
  });
});

describe('isPrivateIPv4Host', () => {
  test('accepts 10.0.0.0/8', () => {
    assert.equal(isPrivateIPv4Host('10.0.0.1'), true);
    assert.equal(isPrivateIPv4Host('10.255.255.254'), true);
  });

  test('accepts 172.16.0.0/12 and rejects the boundary just outside it', () => {
    assert.equal(isPrivateIPv4Host('172.16.0.1'), true);
    assert.equal(isPrivateIPv4Host('172.31.255.254'), true);
    assert.equal(isPrivateIPv4Host('172.15.255.255'), false);
    assert.equal(isPrivateIPv4Host('172.32.0.1'), false);
  });

  test('accepts 192.168.0.0/16', () => {
    assert.equal(isPrivateIPv4Host('192.168.0.1'), true);
    assert.equal(isPrivateIPv4Host('192.168.255.254'), true);
  });

  test('accepts 169.254.0.0/16 link-local', () => {
    assert.equal(isPrivateIPv4Host('169.254.1.1'), true);
  });

  test('rejects loopback (127.0.0.0/8) even though it is a valid IPv4 literal', () => {
    assert.equal(isPrivateIPv4Host('127.0.0.1'), false);
  });

  test('rejects public addresses', () => {
    assert.equal(isPrivateIPv4Host('8.8.8.8'), false);
    assert.equal(isPrivateIPv4Host('1.1.1.1'), false);
  });

  test('rejects hostnames and IPv6 literals', () => {
    assert.equal(isPrivateIPv4Host('mirror.local'), false);
    assert.equal(isPrivateIPv4Host('::1'), false);
  });

  test('rejects malformed input entirely', () => {
    assert.equal(isPrivateIPv4Host('192.168.1'), false);
    assert.equal(isPrivateIPv4Host('192.168.1.1.1'), false);
    assert.equal(isPrivateIPv4Host(''), false);
  });
});
