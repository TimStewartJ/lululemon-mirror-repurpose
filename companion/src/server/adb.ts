import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';

/**
 * Everything related to discovering, invoking, and parsing output from the
 * `adb` executable. All process invocations use execFile with argument
 * arrays (never a shell), so arguments are never subject to shell
 * interpolation/escaping on any platform.
 */

export interface AdbDevice {
  serial: string;
  state: 'device' | 'unauthorized' | 'offline' | 'no permissions' | string;
  properties: Record<string, string>;
}

export interface ForwardResult {
  localPort: number;
}

export class AdbNotFoundError extends Error {
  constructor() {
    super(
      'adb executable not found. Set ADB_PATH, install Android SDK platform-tools ' +
        'and set ANDROID_SDK_ROOT/ANDROID_HOME, or add adb to PATH.',
    );
    this.name = 'AdbNotFoundError';
  }
}

function adbExecutableName(platform: NodeJS.Platform = process.platform): string {
  return platform === 'win32' ? 'adb.exe' : 'adb';
}

export interface FindAdbOptions {
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  /** Injected for testability; defaults to fs.existsSync. */
  exists?: (candidate: string) => boolean;
}

/**
 * Resolves the adb executable to use, in priority order:
 *   1. ADB_PATH environment variable (explicit override).
 *   2. ANDROID_SDK_ROOT or ANDROID_HOME `platform-tools/adb[.exe]`.
 *   3. Any directory on PATH containing an adb executable.
 * Returns null if none of the candidates exist.
 */
export function findAdbExecutable(options: FindAdbOptions = {}): string | null {
  const env = options.env ?? process.env;
  const platform = options.platform ?? process.platform;
  const exists = options.exists ?? ((candidate: string) => fs.existsSync(candidate));
  const exeName = adbExecutableName(platform);
  // Use the path module matching the (possibly simulated) target platform,
  // rather than the host's ambient `path`, so behavior is deterministic
  // under test regardless of which OS actually runs the test, and so the
  // path delimiter used to split PATH/Path matches that platform too.
  const platformPath = platform === 'win32' ? path.win32 : path.posix;

  const candidates: string[] = [];

  if (env.ADB_PATH && env.ADB_PATH.trim().length > 0) {
    candidates.push(env.ADB_PATH);
  }

  const sdkRoot = env.ANDROID_SDK_ROOT || env.ANDROID_HOME;
  if (sdkRoot) {
    candidates.push(platformPath.join(sdkRoot, 'platform-tools', exeName));
  }

  const pathVar = env.PATH || env.Path || '';
  if (pathVar) {
    const delimiter = platform === 'win32' ? ';' : ':';
    for (const dir of pathVar.split(delimiter)) {
      if (!dir) continue;
      candidates.push(platformPath.join(dir, exeName));
    }
  }

  for (const candidate of candidates) {
    if (exists(candidate)) return candidate;
  }
  return null;
}

/**
 * Parses `adb devices -l` output into structured device entries. Handles
 * the "List of devices attached" header, blank lines, and the trailing
 * key:value property list adb appends per line (e.g. `product:mirror
 * model:Mirror device:msm8916_64 transport_id:1`).
 */
export function parseAdbDevicesOutput(output: string): AdbDevice[] {
  const lines = output.split(/\r?\n/);
  const devices: AdbDevice[] = [];

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    if (line.startsWith('List of devices attached')) continue;
    if (line.startsWith('*') || line.startsWith('adb server')) continue; // daemon banners

    const parts = line.split(/\s+/);
    const serial = parts[0];
    const state = parts[1];
    if (!serial || !state) continue;

    const properties: Record<string, string> = {};
    for (const token of parts.slice(2)) {
      const separatorIndex = token.indexOf(':');
      if (separatorIndex <= 0) continue;
      const key = token.slice(0, separatorIndex);
      const value = token.slice(separatorIndex + 1);
      properties[key] = value;
    }

    devices.push({ serial, state, properties });
  }

  return devices;
}

/**
 * Parses `adb shell getprop` output (lines shaped like
 * `[ro.product.device]: [msm8916_64]`) into a plain key/value map.
 */
export function parseGetprop(output: string): Record<string, string> {
  const result: Record<string, string> = {};
  const lineRegex = /^\[([^\]]+)\]:\s*\[([^\]]*)\]\s*$/;

  for (const rawLine of output.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const match = lineRegex.exec(line);
    if (!match) continue;
    const [, key, value] = match;
    if (key === undefined || value === undefined) continue;
    result[key] = value;
  }

  return result;
}

function run(adbPath: string, args: string[]): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    execFile(adbPath, args, { windowsHide: true, timeout: 15_000 }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(`adb ${args.join(' ')} failed: ${stderr.trim() || error.message}`));
        return;
      }
      resolve({ stdout, stderr });
    });
  });
}

export async function listDevices(adbPath: string): Promise<AdbDevice[]> {
  const { stdout } = await run(adbPath, ['devices', '-l']);
  return parseAdbDevicesOutput(stdout);
}

export async function getDeviceProperties(
  adbPath: string,
  serial: string,
): Promise<Record<string, string>> {
  const { stdout } = await run(adbPath, ['-s', serial, 'shell', 'getprop']);
  return parseGetprop(stdout);
}

/**
 * Sets up `adb forward` from a loopback TCP port to the device's control
 * API port. Tries the preferred port first; if adb refuses because the
 * port is already bound locally, falls back to asking adb to allocate a
 * free port dynamically (`adb forward tcp:0 tcp:<remote>`), which prints
 * the chosen port number to stdout.
 */
export async function setupForward(
  adbPath: string,
  serial: string,
  preferredLocalPort: number,
  remotePort: number,
): Promise<ForwardResult> {
  try {
    await run(adbPath, [
      '-s',
      serial,
      'forward',
      `tcp:${preferredLocalPort}`,
      `tcp:${remotePort}`,
    ]);
    return { localPort: preferredLocalPort };
  } catch (preferredError) {
    try {
      const { stdout } = await run(adbPath, [
        '-s',
        serial,
        'forward',
        'tcp:0',
        `tcp:${remotePort}`,
      ]);
      const allocatedPort = Number.parseInt(stdout.trim(), 10);
      if (!Number.isFinite(allocatedPort) || allocatedPort <= 0) {
        throw new Error(`adb did not return a usable port (got "${stdout.trim()}")`);
      }
      return { localPort: allocatedPort };
    } catch (fallbackError) {
      const preferredMessage =
        preferredError instanceof Error ? preferredError.message : String(preferredError);
      const fallbackMessage =
        fallbackError instanceof Error ? fallbackError.message : String(fallbackError);
      throw new Error(
        `Unable to forward a loopback port to the device: ${preferredMessage}; ` +
          `dynamic allocation also failed: ${fallbackMessage}`,
      );
    }
  }
}

export async function removeForward(adbPath: string, serial: string, localPort: number): Promise<void> {
  await run(adbPath, ['-s', serial, 'forward', '--remove', `tcp:${localPort}`]);
}

export interface ReverseResult {
  devicePort: number;
}

/**
 * Argument-array builders for `adb reverse`, extracted as pure functions so
 * they can be unit-tested without invoking execFile. Older ADB builds,
 * including this Mirror's platform-tools implementation, do not report a
 * dynamically allocated reverse port for `tcp:0`, so callers provide a
 * preferred fixed device-side port instead.
 */
export function buildReverseArgs(
  serial: string,
  devicePort: number,
  companionPort: number,
): string[] {
  return ['-s', serial, 'reverse', `tcp:${devicePort}`, `tcp:${companionPort}`];
}

export function buildRemoveReverseArgs(serial: string, devicePort: number): string[] {
  return ['-s', serial, 'reverse', '--remove', `tcp:${devicePort}`];
}

/**
 * Sets up an `adb reverse` tunnel so the device can fetch companion-hosted
 * media from its own loopback interface (127.0.0.1:<devicePort>), which adb
 * tunnels back to this companion's HTTP server. Tries a preferred fixed
 * device-side port and, if that fails (e.g. already bound), a handful of
 * sequential fallback ports -- Android 6's adb (as shipped on this Mirror)
 * does not report a device-side port back for `adb reverse tcp:0 ...`, so a
 * dynamically allocated port cannot be discovered here.
 */
export async function setupReverse(
  adbPath: string,
  serial: string,
  preferredDevicePort: number,
  companionPort: number,
): Promise<ReverseResult> {
  const failures: string[] = [];
  for (let offset = 0; offset < 10; offset += 1) {
    const devicePort = preferredDevicePort + offset;
    try {
      await run(adbPath, buildReverseArgs(serial, devicePort, companionPort));
      return { devicePort };
    } catch (error) {
      failures.push((error as Error).message);
    }
  }
  throw new Error(
    `Unable to establish adb reverse on ports ${preferredDevicePort}-` +
      `${preferredDevicePort + 9}: ${failures.join('; ')}`,
  );
}

export async function removeReverse(adbPath: string, serial: string, devicePort: number): Promise<void> {
  await run(adbPath, buildRemoveReverseArgs(serial, devicePort));
}
