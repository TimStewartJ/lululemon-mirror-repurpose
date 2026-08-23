/**
 * Companion access token storage. Held only in sessionStorage (cleared
 * when the tab/browser closes) — never localStorage, never a cookie, and
 * never persisted server-side beyond the companion's own env-configured
 * secret. Loopback-only deployments don't need a token at all; this is
 * only required once the companion is reachable over the LAN.
 */

const STORAGE_KEY = 'companion.accessToken';

export function getStoredToken(): string {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY) ?? '';
  } catch {
    // sessionStorage can throw in locked-down contexts (e.g. some private
    // browsing modes); treat that the same as "no token set".
    return '';
  }
}

export function setStoredToken(token: string): void {
  try {
    if (token) {
      window.sessionStorage.setItem(STORAGE_KEY, token);
    } else {
      window.sessionStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Best-effort only; nothing else to fall back to client-side.
  }
}

/** Header name the companion server checks; kept in one place for reuse. */
export const ACCESS_TOKEN_HEADER = 'X-Companion-Token';
