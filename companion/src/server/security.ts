import crypto from 'node:crypto';
import type { Request, Response, NextFunction } from 'express';
import type { CompanionEnv } from './env';

/**
 * Recognizes loopback-only hosts. Anything else (including "0.0.0.0",
 * which binds every interface) is treated as LAN-reachable and therefore
 * requires an access token before the companion will start.
 */
export function isLoopbackHost(host: string): boolean {
  const normalized = host.trim().toLowerCase();
  if (normalized === 'localhost' || normalized === '::1' || normalized === '::ffff:127.0.0.1') {
    return true;
  }
  return /^127\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.test(normalized);
}

export class MissingAccessTokenError extends Error {
  constructor(host: string) {
    super(
      `COMPANION_HOST is set to "${host}", which is not loopback-only. ` +
        'Set COMPANION_ACCESS_TOKEN before binding the companion to a LAN-reachable address.',
    );
    this.name = 'MissingAccessTokenError';
  }
}

/**
 * Fails fast at startup rather than silently serving an unauthenticated
 * API on the LAN. Loopback-only deployments (the default) are unaffected.
 */
export function assertAccessTokenConfigured(env: Pick<CompanionEnv, 'host' | 'accessToken'>): void {
  if (isLoopbackHost(env.host)) return;
  if (!env.accessToken) {
    throw new MissingAccessTokenError(env.host);
  }
}

const TOKEN_HEADER = 'x-companion-token';

/** Constant-time-ish comparison that tolerates differing input lengths. */
function tokensMatch(provided: string, expected: string): boolean {
  const providedHash = crypto.createHash('sha256').update(provided, 'utf8').digest();
  const expectedHash = crypto.createHash('sha256').update(expected, 'utf8').digest();
  return crypto.timingSafeEqual(providedHash, expectedHash);
}

/**
 * Express middleware guarding /api/device, /api/companion, and
 * /media-files. When no access token is configured (loopback-only
 * deployments), this is a no-op passthrough. When a token is configured,
 * every request must present it via the X-Companion-Token header; it is
 * never accepted via query string so it can't leak into access logs or
 * browser history.
 */
export function createAccessTokenMiddleware(accessToken: string | undefined) {
  return function accessTokenMiddleware(req: Request, res: Response, next: NextFunction): void {
    if (!accessToken) {
      next();
      return;
    }
    const provided = req.header(TOKEN_HEADER);
    if (typeof provided !== 'string' || provided.length === 0 || !tokensMatch(provided, accessToken)) {
      res.status(401).json({ error: 'Missing or invalid X-Companion-Token header' });
      return;
    }
    next();
  };
}
