import {
  findAdbExecutable,
  listDevices,
  getDeviceProperties,
  setupForward,
  removeForward,
  setupReverse,
  removeReverse,
  AdbNotFoundError,
  type AdbDevice,
} from './adb';
import { validateDeviceProfile, SUPPORTED_DEVICE_PROFILE } from './device-profile';
import { isPrivateIPv4Host } from './net-validation';
import { ConfigStore } from './config-store';
import { DeviceClient, type DeviceClientPort } from './proxy-client';
import { logger } from './logger';

export interface DiscoveredDevice {
  serial: string;
  state: string;
  supported: boolean;
  reasons: string[];
}

export interface UsbConnectionState {
  mode: 'usb';
  serial: string;
  localPort: number;
  /** Device-side port allocated by `adb reverse`, once USB media playback
   * has been used at least once during this connection; null until then. */
  reverseDevicePort?: number | null;
}

export interface LanConnectionState {
  mode: 'lan';
  /** Validated RFC1918/link-local IPv4 address (see net-validation.ts). */
  host: string;
  /** The device's own control API port (not an adb-forwarded port). */
  port: number;
}

export type ConnectionState = UsbConnectionState | LanConnectionState;

/**
 * Public surface of DeviceManager. Routers depend on this interface (not
 * the concrete class) so tests can substitute a lightweight fake instead of
 * standing up real adb/network state.
 */
export interface DeviceManagerPort {
  listDiscoveredDevices(): Promise<DiscoveredDevice[]>;
  connect(serial: string): Promise<UsbConnectionState>;
  connectLan(host: string, token: string): Promise<LanConnectionState>;
  disconnect(): Promise<void>;
  getConnection(): ConnectionState | null;
  getClient(): DeviceClientPort;
  ensureUsbReverseTunnel(companionPort: number): Promise<number>;
}

/**
 * Orchestrates ADB discovery/validation/forwarding and hands out a
 * DeviceClient bound to whichever loopback port is currently forwarded.
 * Holds only in-memory connection state; the pairing token is the only
 * thing persisted across restarts (via ConfigStore).
 */
export class DeviceManager implements DeviceManagerPort {
  private connection: ConnectionState | null = null;
  /**
   * Tracks an in-flight `adb reverse` setup so concurrent callers (e.g. two
   * near-simultaneous media/play requests) await the same tunnel creation
   * instead of racing to run `adb reverse` multiple times. Cleared once the
   * setup settles (success or failure) or the device disconnects.
   */
  private reverseTunnelSetup: Promise<number> | null = null;

  constructor(
    private readonly configStore: ConfigStore,
    private readonly preferredForwardPort: number,
    private readonly deviceApiPort: number,
    private readonly adbPathOverride?: string,
    private readonly preferredReversePort: number = 14317,
  ) {}

  private resolveAdbPath(): string {
    const env = this.adbPathOverride
      ? { ...process.env, ADB_PATH: this.adbPathOverride }
      : process.env;
    const adbPath = findAdbExecutable({ env });
    if (!adbPath) throw new AdbNotFoundError();
    return adbPath;
  }

  async listDiscoveredDevices(): Promise<DiscoveredDevice[]> {
    const adbPath = this.resolveAdbPath();
    const devices: AdbDevice[] = await listDevices(adbPath);

    const results: DiscoveredDevice[] = [];
    for (const device of devices) {
      if (device.state !== 'device') {
        results.push({ serial: device.serial, state: device.state, supported: false, reasons: [
          `device state is "${device.state}", expected "device" (authorize USB debugging on the Mirror)`,
        ] });
        continue;
      }
      try {
        const props = await getDeviceProperties(adbPath, device.serial);
        const validation = validateDeviceProfile(props);
        results.push({
          serial: device.serial,
          state: device.state,
          supported: validation.valid,
          reasons: validation.reasons,
        });
      } catch (error) {
        results.push({
          serial: device.serial,
          state: device.state,
          supported: false,
          reasons: [`failed to read device properties: ${(error as Error).message}`],
        });
      }
    }
    return results;
  }

  async connect(serial: string): Promise<UsbConnectionState> {
    const adbPath = this.resolveAdbPath();
    const props = await getDeviceProperties(adbPath, serial);
    const validation = validateDeviceProfile(props);
    if (!validation.valid) {
      throw new Error(
        `Refusing to connect: device does not match supported profile ` +
          `"${SUPPORTED_DEVICE_PROFILE.id}" (${validation.reasons.join('; ')})`,
      );
    }

    const forward = await setupForward(adbPath, serial, this.preferredForwardPort, this.deviceApiPort);
    this.connection = { mode: 'usb', serial, localPort: forward.localPort, reverseDevicePort: null };
    logger.info('adb forward established', { serial, localPort: forward.localPort });
    return this.connection;
  }

  /**
   * Establishes a LAN connection using an IP address and pairing token
   * obtained out-of-band (for example, from the Mirror's on-glass QR and
   * pairing-code flow). The host is validated as a private/link-local IPv4
   * literal before it is ever used to build an outbound URL, to prevent SSRF
   * via an attacker-supplied address. Only the pairing token is persisted;
   * the Wi-Fi passphrase never reaches this method or ConfigStore.
   */
  async connectLan(host: string, token: string): Promise<LanConnectionState> {
    if (!isPrivateIPv4Host(host)) {
      throw new Error(
        `Refusing to connect: "${host}" is not a private (RFC1918) or link-local IPv4 address.`,
      );
    }
    if (!token || typeof token !== 'string') {
      throw new Error('A pairing token is required to connect over LAN.');
    }

    this.configStore.setToken(token);
    this.connection = { mode: 'lan', host, port: this.deviceApiPort };
    logger.info('lan connection established', { host });
    return this.connection;
  }

  async disconnect(): Promise<void> {
    if (!this.connection) return;
    const connection = this.connection;
    // Any in-flight `adb reverse` setup is tied to the connection being
    // torn down; drop the reference so a fresh connect starts clean and
    // does not resolve into a now-disconnected device's stale port.
    this.reverseTunnelSetup = null;
    if (connection.mode === 'lan') {
      // No adb tunnel to tear down for a LAN connection; just clear state.
      this.connection = null;
      return;
    }

    const adbPath = this.resolveAdbPath();
    try {
      if (connection.reverseDevicePort != null) {
        try {
          await removeReverse(adbPath, connection.serial, connection.reverseDevicePort);
        } catch (error) {
          // Non-fatal: the device/session may already be gone. Still
          // proceed to remove the forward and clear local state below.
          logger.warn('adb reverse teardown failed', { message: (error as Error).message });
        }
      }
      await removeForward(adbPath, connection.serial, connection.localPort);
    } finally {
      this.connection = null;
    }
  }

  getConnection(): ConnectionState | null {
    return this.connection;
  }

  /** Builds a DeviceClient bound to the active connection and stored token. */
  getClient(): DeviceClientPort {
    if (!this.connection) {
      throw new Error('No device connected. Connect over USB (adb) or LAN first.');
    }
    if (this.connection.mode === 'usb') {
      return new DeviceClient({ port: this.connection.localPort, token: this.configStore.getToken() });
    }
    return new DeviceClient({
      host: this.connection.host,
      port: this.connection.port,
      token: this.configStore.getToken(),
    });
  }

  /**
   * Lazily establishes (and reuses) an `adb reverse` tunnel from the
   * device's loopback interface back to this companion's media server
   * port, for USB media playback. Only valid while connected over USB.
   *
   * Concurrent callers before the tunnel exists share a single in-flight
   * `adb reverse` invocation (memoized via `reverseTunnelSetup`) rather
   * than each racing to allocate their own device-side port.
   */
  async ensureUsbReverseTunnel(companionPort: number): Promise<number> {
    if (!this.connection || this.connection.mode !== 'usb') {
      throw new Error('USB media hosting requires an active USB (adb) connection.');
    }
    if (this.connection.reverseDevicePort != null) {
      return this.connection.reverseDevicePort;
    }
    if (this.reverseTunnelSetup) {
      return this.reverseTunnelSetup;
    }

    const connection = this.connection;
    this.reverseTunnelSetup = (async () => {
      const adbPath = this.resolveAdbPath();
      const { devicePort } = await setupReverse(
        adbPath,
        connection.serial,
        this.preferredReversePort,
        companionPort,
      );
      // Only persist the result onto the connection that requested it: if
      // a disconnect (or reconnect) raced in while `adb reverse` was
      // running, `this.connection` will no longer be `connection` and we
      // must not resurrect stale state onto whatever is active now.
      if (this.connection === connection) {
        this.connection = { ...connection, reverseDevicePort: devicePort };
      }
      logger.info('adb reverse established', { serial: connection.serial, devicePort });
      return devicePort;
    })();

    try {
      return await this.reverseTunnelSetup;
    } finally {
      this.reverseTunnelSetup = null;
    }
  }
}
