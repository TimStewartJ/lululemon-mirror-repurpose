import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { parseRangeHeader } from '../media/range';

describe('parseRangeHeader', () => {
  const size = 1000;

  test('returns none for missing header', () => {
    assert.deepEqual(parseRangeHeader(undefined, size), { kind: 'none' });
  });

  test('parses a standard bounded range', () => {
    const result = parseRangeHeader('bytes=0-499', size);
    assert.deepEqual(result, { kind: 'single', range: { start: 0, end: 499 } });
  });

  test('parses an open-ended range (bytes=N-)', () => {
    const result = parseRangeHeader('bytes=500-', size);
    assert.deepEqual(result, { kind: 'single', range: { start: 500, end: 999 } });
  });

  test('parses a suffix range (bytes=-N)', () => {
    const result = parseRangeHeader('bytes=-100', size);
    assert.deepEqual(result, { kind: 'single', range: { start: 900, end: 999 } });
  });

  test('clamps a suffix range larger than the resource', () => {
    const result = parseRangeHeader('bytes=-5000', size);
    assert.deepEqual(result, { kind: 'single', range: { start: 0, end: 999 } });
  });

  test('clamps an end beyond the resource size', () => {
    const result = parseRangeHeader('bytes=900-999999', size);
    assert.deepEqual(result, { kind: 'single', range: { start: 900, end: 999 } });
  });

  test('rejects multi-range requests as unsupported', () => {
    const result = parseRangeHeader('bytes=0-10,20-30', size);
    assert.deepEqual(result, { kind: 'unsupported' });
  });

  test('rejects non-bytes units as unsupported', () => {
    const result = parseRangeHeader('items=0-10', size);
    assert.deepEqual(result, { kind: 'unsupported' });
  });

  test('rejects malformed syntax as unsupported', () => {
    const result = parseRangeHeader('bytes=abc-def', size);
    assert.deepEqual(result, { kind: 'unsupported' });
  });

  test('marks a start beyond the resource size as unsatisfiable', () => {
    const result = parseRangeHeader('bytes=1000-1999', size);
    assert.deepEqual(result, { kind: 'unsatisfiable' });
  });

  test('marks start > end as unsatisfiable', () => {
    const result = parseRangeHeader('bytes=500-100', size);
    assert.deepEqual(result, { kind: 'unsatisfiable' });
  });

  test('marks a zero-length suffix as unsatisfiable', () => {
    const result = parseRangeHeader('bytes=-0', size);
    assert.deepEqual(result, { kind: 'unsatisfiable' });
  });

  test('treats a zero-size resource as unsatisfiable', () => {
    const result = parseRangeHeader('bytes=0-10', 0);
    assert.deepEqual(result, { kind: 'unsatisfiable' });
  });

  test('rejects an entirely empty range spec as unsupported', () => {
    const result = parseRangeHeader('bytes=-', size);
    assert.deepEqual(result, { kind: 'unsupported' });
  });
});
