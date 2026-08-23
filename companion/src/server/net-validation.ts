/**
 * Validates host strings supplied for the LAN device-connection path (the
 * IP address reported back over BLE after provisioning). This is the only
 * gate between a user/device-controlled string and an outbound fetch() the
 * companion makes on the browser's behalf, so it is deliberately strict:
 * only a literal IPv4 dotted-quad is ever accepted (no DNS resolution, no
 * hostnames -- both of which could be used for DNS-rebinding SSRF), and
 * only RFC1918 private ranges plus link-local addresses are allowed. This
 * intentionally excludes 127.0.0.0/8 (loopback): a BLE-provisioned device
 * always reports a real LAN address, and accepting loopback here would
 * reopen an SSRF-to-localhost vector via a spoofed BLE response.
 */

/**
 * Strict IPv4 dotted-quad parser: exactly four decimal octets in [0, 255],
 * no leading zeros (which some libraries misparse as octal), no
 * surrounding whitespace/text. Returns null for anything else, including
 * hostnames, IPv6 literals, and CIDR suffixes.
 */
export function parseIPv4(host: string): [number, number, number, number] | null {
  const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host.trim());
  if (!match) return null;

  const octets: number[] = [];
  for (const segment of match.slice(1, 5)) {
    if (segment === undefined) return null;
    if (segment.length > 1 && segment.startsWith('0')) return null; // reject "010"-style ambiguity
    const value = Number.parseInt(segment, 10);
    if (!Number.isInteger(value) || value < 0 || value > 255) return null;
    octets.push(value);
  }
  return octets as [number, number, number, number];
}

/**
 * True only for a literal IPv4 address within a private (RFC1918) or
 * link-local range: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, or
 * 169.254.0.0/16. Hostnames, IPv6, loopback, and public addresses all
 * return false.
 */
export function isPrivateIPv4Host(host: string): boolean {
  const octets = parseIPv4(host);
  if (!octets) return false;
  const [a, b] = octets;

  if (a === 10) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a === 169 && b === 254) return true;
  return false;
}
