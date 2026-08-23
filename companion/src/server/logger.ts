/**
 * Minimal structured logger. The companion proxy must never log bearer
 * tokens, Authorization headers, or Wi-Fi passphrases. Callers should pass
 * only already-redacted data; this module additionally provides helpers to
 * make redaction convenient at call sites.
 */

type LogFields = Record<string, unknown>;

function serialize(fields?: LogFields): string {
  if (!fields || Object.keys(fields).length === 0) return '';
  try {
    return ' ' + JSON.stringify(fields);
  } catch {
    return '';
  }
}

export const logger = {
  info(message: string, fields?: LogFields): void {
    console.log(`[companion] ${message}${serialize(fields)}`);
  },
  warn(message: string, fields?: LogFields): void {
    console.warn(`[companion] ${message}${serialize(fields)}`);
  },
  error(message: string, fields?: LogFields): void {
    console.error(`[companion] ${message}${serialize(fields)}`);
  },
};

/**
 * Returns a redacted stand-in for a URL that may carry secrets in its query
 * string. The companion never places tokens in URLs, but this guards
 * against accidental future regressions when logging request paths.
 */
export function redactUrl(rawUrl: string): string {
  const [pathPart] = rawUrl.split('?');
  return pathPart ?? rawUrl;
}
