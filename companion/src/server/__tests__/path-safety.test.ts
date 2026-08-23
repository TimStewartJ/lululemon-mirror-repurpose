import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { resolveMediaPath, tryResolveMediaPath, PathTraversalError } from '../media/path-safety';

describe('resolveMediaPath', () => {
  const root = path.join('C:', 'companion-media');
  const posixRoot = '/var/companion-media';

  test('resolves a simple relative filename inside the root', () => {
    const resolved = resolveMediaPath(posixRoot, 'song.mp3');
    assert.equal(resolved, path.resolve(posixRoot, 'song.mp3'));
  });

  test('resolves a nested relative path inside the root', () => {
    const resolved = resolveMediaPath(posixRoot, 'albums/one/song.mp3');
    assert.equal(resolved, path.resolve(posixRoot, 'albums', 'one', 'song.mp3'));
  });

  test('rejects simple parent traversal', () => {
    assert.throws(() => resolveMediaPath(posixRoot, '../secret.txt'), PathTraversalError);
  });

  test('rejects deeply nested parent traversal', () => {
    assert.throws(
      () => resolveMediaPath(posixRoot, 'a/b/../../../etc/passwd'),
      PathTraversalError,
    );
  });

  test('rejects backslash-style traversal on any platform', () => {
    assert.throws(() => resolveMediaPath(posixRoot, '..\\..\\windows\\system32'), PathTraversalError);
  });

  test('rejects absolute POSIX paths', () => {
    assert.throws(() => resolveMediaPath(posixRoot, '/etc/passwd'), PathTraversalError);
  });

  test('rejects absolute Windows paths with a drive letter', () => {
    assert.throws(() => resolveMediaPath(root, 'C:\\Windows\\System32\\config'), PathTraversalError);
  });

  test('rejects UNC-style paths', () => {
    assert.throws(() => resolveMediaPath(root, '\\\\server\\share\\file'), PathTraversalError);
  });

  test('treats an already-decoded traversal sequence as unsafe (post-Express-decode input)', () => {
    // Express decodes route params with decodeURIComponent before handlers
    // ever run, so by the time a handler calls resolveMediaPath, a request
    // for "%2e%2e%2fetc%2fpasswd" has already become this literal string.
    assert.throws(() => resolveMediaPath(posixRoot, '../etc/passwd'), PathTraversalError);
  });

  test('does not re-decode percent sequences: a still-encoded literal is treated as an opaque filename', () => {
    // This function must never call decodeURIComponent itself (that would
    // double-decode input Express already decoded once). A raw percent-
    // encoded string with no literal slashes is therefore just an unusual
    // (but harmless) single filename, not a traversal attempt.
    const resolved = resolveMediaPath(posixRoot, '%2e%2e%2fetc%2fpasswd');
    assert.equal(resolved, path.resolve(posixRoot, '%2e%2e%2fetc%2fpasswd'));
  });

  test('accepts a legitimate filename containing a literal percent character', () => {
    // decodeURIComponent('50%.mp3') would throw (a lone '%' not followed by
    // two hex digits is not a valid escape), which is exactly why this
    // module must not decode its input a second time.
    const resolved = resolveMediaPath(posixRoot, '50%.mp3');
    assert.equal(resolved, path.resolve(posixRoot, '50%.mp3'));
  });

  test('rejects embedded NUL bytes', () => {
    assert.throws(() => resolveMediaPath(posixRoot, 'song.mp3\0.txt'), PathTraversalError);
  });

  test('rejects an array of segments that traverse out via ".."', () => {
    assert.throws(() => resolveMediaPath(posixRoot, ['..', '..', 'etc', 'passwd']), PathTraversalError);
  });

  test('accepts an array of segments that stay confined', () => {
    const resolved = resolveMediaPath(posixRoot, ['albums', 'one', 'song.mp3']);
    assert.equal(resolved, path.resolve(posixRoot, 'albums', 'one', 'song.mp3'));
  });

  test('treats the root itself as confined (empty relative path)', () => {
    const resolved = resolveMediaPath(posixRoot, '.');
    assert.equal(resolved, path.resolve(posixRoot));
  });

  test('tryResolveMediaPath returns null instead of throwing', () => {
    assert.equal(tryResolveMediaPath(posixRoot, '../escape'), null);
  });

  test('tryResolveMediaPath returns the resolved path when safe', () => {
    assert.equal(tryResolveMediaPath(posixRoot, 'ok.mp3'), path.resolve(posixRoot, 'ok.mp3'));
  });
});
