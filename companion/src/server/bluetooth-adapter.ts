/**
 * Bluetooth provisioning is not implemented yet. This module defines the
 * adapter interface the companion will use once a Web Bluetooth (browser)
 * or native BLE provisioning path is implemented, and a stub that reports
 * itself as unsupported so routes/UI have something stable to call today.
 *
 * Rationale for a browser-facing interface: per docs/architecture.md, BLE
 * provisioning is expected to run through the browser's Web Bluetooth API
 * (navigator.bluetooth) directly from the companion's static UI, with the
 * companion server mostly out of the data path except perhaps to relay
 * discovered device metadata. This interface is deliberately abstract
 * enough to support either a server-mediated or browser-mediated
 * implementation later.
 */

export interface BluetoothDeviceDescriptor {
  id: string;
  name: string | null;
}

export interface BluetoothAdapterStatus {
  supported: boolean;
  reason?: string;
}

export interface BluetoothProvisioningAdapter {
  /** Whether this adapter can currently perform BLE provisioning. */
  status(): BluetoothAdapterStatus;

  /** Discover nearby Mirror devices advertising the provisioning service. */
  scan(): Promise<BluetoothDeviceDescriptor[]>;

  /** Connect to a previously discovered device by id. */
  connect(deviceId: string): Promise<void>;

  /** Send Wi-Fi provisioning payload over the connected GATT link. */
  provisionWifi(deviceId: string, ssid: string, passphrase: string): Promise<void>;

  /** Disconnect and release any held resources. */
  disconnect(deviceId: string): Promise<void>;
}

const NOT_IMPLEMENTED_REASON =
  'Server-mediated Bluetooth provisioning is not implemented. Use the ' +
  '"BLE provisioning" card (browser Web Bluetooth), USB (ADB), or LAN pairing instead.';

/**
 * Stub adapter for a *server-mediated* BLE path (e.g. a native BLE library
 * running alongside the companion process), which remains unimplemented.
 * This is a separate code path from the browser-mediated Web Bluetooth flow
 * in src/client/ble.ts, which talks to the device directly from the page
 * and only reports the resulting token/IP back to the companion over a
 * normal same-origin HTTP call (POST /api/companion/devices/lan/connect).
 * All operations reject clearly rather than silently no-op, so callers
 * cannot mistake this for a working, empty-result implementation.
 */
export class NotImplementedBluetoothAdapter implements BluetoothProvisioningAdapter {
  status(): BluetoothAdapterStatus {
    return { supported: false, reason: NOT_IMPLEMENTED_REASON };
  }

  async scan(): Promise<BluetoothDeviceDescriptor[]> {
    throw new Error(NOT_IMPLEMENTED_REASON);
  }

  async connect(): Promise<void> {
    throw new Error(NOT_IMPLEMENTED_REASON);
  }

  async provisionWifi(): Promise<void> {
    throw new Error(NOT_IMPLEMENTED_REASON);
  }

  async disconnect(): Promise<void> {
    throw new Error(NOT_IMPLEMENTED_REASON);
  }
}
