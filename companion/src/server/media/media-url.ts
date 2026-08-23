import os from 'node:os';
import { isPrivateIPv4Host } from '../net-validation';

/**
 * Pure URL-building helpers for handing the Mirror device a playback URL
 * for a companion-hosted media file. Two delivery paths exist:
 *
 *  - USB: the device fetches from its own loopback interface, tunnelled
 *    back to this companion by `adb reverse` (see adb.ts setupReverse), so
 *    the URL is always relative to 127.0.0.1 from the device's perspective
 *    regardless of this host's real network configuration.
 *  - LAN: the device fetches this companion directly over the network, so
 *    the URL must contain an address the device can actually reach. This
 *    NEVER trusts a browser-supplied Host header (which is fully
 *    attacker-controlled and would let a malicious page redirect the
 *    device's fetch anywhere); it uses an explicit COMPANION_PUBLIC_URL
 *    override when configured, or otherwise inspects this host's own
 *    network interfaces for a private LAN address.
 */

export function buildUsbMediaUrl(devicePort: number, mediaName: string): string {
  return `http://127.0.0.1:${devicePort}/media-files/${encodeURIComponent(mediaName)}`;
}

export interface LanMediaUrlOptions {
  /** Explicit override (e.g. `http://192.168.1.20:4317`), no trailing slash required. */
  publicUrl?: string;
  companionPort: number;
  /** Injected for testability; defaults to os.networkInterfaces(). */
  interfaces?: NodeJS.Dict<os.NetworkInterfaceInfo[]>;
}

export class LanMediaUrlUnavailableError extends Error {
  constructor() {
    super(
      'Unable to determine a LAN-reachable address for media hosting. Set ' +
        'COMPANION_PUBLIC_URL to an explicit base URL the device can reach.',
    );
    this.name = 'LanMediaUrlUnavailableError';
  }
}

/**
 * Selects a private (RFC1918/link-local) IPv4 address from a non-internal
 * network interface -- i.e. the companion host's own real LAN address, not
 * anything derived from request data.
 */
export function selectPrivateInterfaceAddress(
  interfaces: NodeJS.Dict<os.NetworkInterfaceInfo[]> = os.networkInterfaces(),
): string | null {
  for (const entries of Object.values(interfaces)) {
    if (!entries) continue;
    for (const entry of entries) {
      if (entry.internal) continue;
      if (entry.family !== 'IPv4') continue;
      if (isPrivateIPv4Host(entry.address)) return entry.address;
    }
  }
  return null;
}

export function buildLanMediaUrl(options: LanMediaUrlOptions, mediaName: string): string {
  const encodedName = encodeURIComponent(mediaName);
  if (options.publicUrl) {
    return `${options.publicUrl.replace(/\/+$/, '')}/media-files/${encodedName}`;
  }
  const address = selectPrivateInterfaceAddress(options.interfaces);
  if (!address) {
    throw new LanMediaUrlUnavailableError();
  }
  return `http://${address}:${options.companionPort}/media-files/${encodedName}`;
}
