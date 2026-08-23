import path from 'node:path';

/**
 * Root directory of the companion project (the directory containing
 * package.json), resolved relative to the compiled output (dist/server)
 * so it works regardless of the process's current working directory.
 */
export const COMPANION_ROOT = path.resolve(__dirname, '..', '..');

function readIntEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

export interface CompanionEnv {
  port: number;
  host: string;
  dataDir: string;
  mediaRoot: string;
  adbPath: string | undefined;
  adbForwardPort: number;
  deviceMediaPort: number;
  deviceApiPort: number;
  /**
   * Shared secret required on every /api/device, /api/companion, and
   * /media-files request whenever `host` is not loopback-only. Undefined
   * when unset; loopback-only deployments may run without one.
   */
  accessToken: string | undefined;
  /**
   * Explicit base URL (e.g. "http://192.168.1.20:4317") the LAN-connected
   * device should use to reach this companion's hosted media. When unset,
   * the companion auto-selects a private LAN interface address instead.
   * Never derived from a browser-supplied Host header.
   */
  publicUrl: string | undefined;
}

export function loadEnv(env: NodeJS.ProcessEnv = process.env): CompanionEnv {
  const dataDir = env.COMPANION_DATA_DIR
    ? path.resolve(env.COMPANION_DATA_DIR)
    : path.join(COMPANION_ROOT, '.data');
  const mediaRoot = env.COMPANION_MEDIA_ROOT
    ? path.resolve(env.COMPANION_MEDIA_ROOT)
    : path.join(dataDir, 'media');

  return {
    port: readIntEnv('COMPANION_PORT', 4317),
    // Defaults to loopback-only. Set COMPANION_HOST=0.0.0.0 to also serve
    // the UI over LAN once the device has its own Wi-Fi connection.
    host: env.COMPANION_HOST && env.COMPANION_HOST.trim().length > 0 ? env.COMPANION_HOST : '127.0.0.1',
    dataDir,
    mediaRoot,
    adbPath: env.ADB_PATH && env.ADB_PATH.trim().length > 0 ? env.ADB_PATH : undefined,
    adbForwardPort: readIntEnv('COMPANION_ADB_FORWARD_PORT', 18787),
    deviceMediaPort: readIntEnv('MIRROR_MEDIA_REVERSE_PORT', 14317),
    deviceApiPort: readIntEnv('DEVICE_API_PORT', 8787),
    accessToken:
      env.COMPANION_ACCESS_TOKEN && env.COMPANION_ACCESS_TOKEN.trim().length > 0
        ? env.COMPANION_ACCESS_TOKEN.trim()
        : undefined,
    publicUrl:
      env.COMPANION_PUBLIC_URL && env.COMPANION_PUBLIC_URL.trim().length > 0
        ? env.COMPANION_PUBLIC_URL.trim()
        : undefined,
  };
}
