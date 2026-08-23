import path from 'node:path';

/**
 * Path confinement for the media library. Every media path segment
 * supplied by an HTTP client must resolve to a location inside the
 * configured media root; this module is the single place that decision is
 * made so it can be exhaustively unit tested.
 *
 * Callers must pass already-decoded path segments. Express decodes route
 * params (including wildcard captures) with decodeURIComponent before
 * handlers ever see them, so decoding again here would double-decode the
 * input. Double decoding both breaks legitimate filenames that contain a
 * literal '%' (decodeURIComponent throws on a lone '%' not part of a valid
 * escape, e.g. "50%.mp3") and is a well-known bypass technique for path
 * confinement checks. This function therefore never calls
 * decodeURIComponent itself; it only normalizes separators and walks
 * segments.
 *
 * Confinement is implemented with an explicit segment stack rather than by
 * delegating solely to the host OS's path.resolve/path.isAbsolute
 * semantics. That keeps behavior identical on Windows and POSIX: a client
 * that sends backslash-separated traversal sequences (or drive-letter /
 * UNC absolute paths) is rejected the same way regardless of which OS the
 * companion happens to be running on, rather than only being caught when
 * the host OS itself treats '\\' as a separator.
 */

export class PathTraversalError extends Error {
  constructor(requested: string) {
    super(`Rejected path outside the media library root: ${requested}`);
    this.name = 'PathTraversalError';
  }
}

const WINDOWS_DRIVE_PATTERN = /^[a-zA-Z]:/;

/**
 * Resolves an already-decoded, client-supplied relative media path against
 * `root`, throwing PathTraversalError if the result would escape the root.
 * Rejects:
 *   - absolute paths (POSIX "/...", Windows "C:\...", or UNC "\\server\...")
 *   - embedded NUL bytes
 *   - any path whose ".." segments would climb above the root
 *
 * `requested` may be a single path string (e.g. "songs/track.mp3") or an
 * array of already-split path segments (e.g. from an Express wildcard
 * route param). Do not pass a still percent-encoded string; decode exactly
 * once at the boundary that received the raw request (Express already does
 * this for route params).
 */
export function resolveMediaPath(root: string, requested: string | string[]): string {
  const rawInput = Array.isArray(requested) ? requested.join('/') : requested;

  if (rawInput.includes('\0')) {
    throw new PathTraversalError(rawInput);
  }

  // Normalize to forward slashes so backslash-based traversal attempts are
  // treated identically on every host platform, not just Windows.
  const normalized = rawInput.replace(/\\/g, '/');

  if (
    normalized.startsWith('/') || // POSIX-absolute or UNC-style ("//server/share")
    WINDOWS_DRIVE_PATTERN.test(normalized) // "C:/..." or "C:..."
  ) {
    throw new PathTraversalError(rawInput);
  }

  const stack: string[] = [];
  for (const segment of normalized.split('/')) {
    if (segment === '' || segment === '.') continue;
    if (segment === '..') {
      if (stack.length === 0) {
        throw new PathTraversalError(rawInput);
      }
      stack.pop();
      continue;
    }
    stack.push(segment);
  }

  const normalizedRoot = path.resolve(root);
  return stack.length === 0 ? normalizedRoot : path.join(normalizedRoot, ...stack);
}

/**
 * Convenience check that never throws; returns null instead of raising
 * PathTraversalError for callers that prefer a boolean-style check.
 */
export function tryResolveMediaPath(root: string, requested: string | string[]): string | null {
  try {
    return resolveMediaPath(root, requested);
  } catch {
    return null;
  }
}
