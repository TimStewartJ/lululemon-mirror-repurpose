import {
  BLE_SERVICE_UUID,
  BLE_REQUEST_CHARACTERISTIC_UUID,
  BLE_RESPONSE_CHARACTERISTIC_UUID,
  encodeProvisionRequest,
  createResponseAssembler,
  type ProvisionRequest,
  type ProvisionResponse,
} from './ble-protocol.js';

/**
 * Browser-side Web Bluetooth provisioning flow. Everything that touches
 * navigator.bluetooth lives here, kept separate from the pure chunking/
 * assembly logic in ble-protocol.ts so that logic can be unit-tested
 * without a real Bluetooth stack.
 */

export class WebBluetoothUnsupportedError extends Error {
  constructor() {
    super(
      'Web Bluetooth is not available in this browser. Try Chrome or Edge ' +
        '(desktop or Android), served over HTTPS or from localhost.',
    );
    this.name = 'WebBluetoothUnsupportedError';
  }
}

export function isWebBluetoothSupported(): boolean {
  return typeof navigator !== 'undefined' && Boolean(navigator.bluetooth);
}

/**
 * Runs the full BLE provisioning exchange: requests a device advertising
 * the provisioning service, connects, writes the chunked provisioning
 * request, and assembles/parses the chunked response.
 *
 * MUST be invoked synchronously from within a user-gesture event handler
 * (e.g. a form submit triggered by a button click) -- navigator.bluetooth
 * .requestDevice() requires "transient activation", which is consumed by
 * the browser at the moment this call chain reaches it. Calling this
 * function as the first thing an event handler does (even though it is
 * itself async and awaits internally) preserves that activation, since no
 * `await` occurs before requestDevice() is invoked.
 */
export async function provisionOverBluetooth(request: ProvisionRequest): Promise<ProvisionResponse> {
  if (!isWebBluetoothSupported()) {
    throw new WebBluetoothUnsupportedError();
  }
  const bluetooth = navigator.bluetooth;
  if (!bluetooth) {
    throw new WebBluetoothUnsupportedError();
  }

  const device = await bluetooth.requestDevice({
    filters: [{ services: [BLE_SERVICE_UUID] }],
  });

  const server = await device.gatt?.connect();
  if (!server) {
    throw new Error('Unable to open a GATT connection to the device.');
  }

  try {
    const service = await server.getPrimaryService(BLE_SERVICE_UUID);
    const requestCharacteristic = await service.getCharacteristic(BLE_REQUEST_CHARACTERISTIC_UUID);
    const responseCharacteristic = await service.getCharacteristic(BLE_RESPONSE_CHARACTERISTIC_UUID);

    const assembler = createResponseAssembler();
    const responsePromise = new Promise<ProvisionResponse>((resolve, reject) => {
      const onNotify = (event: Event): void => {
        try {
          const target = event.target as BluetoothRemoteGATTCharacteristic;
          const value = target.value;
          if (!value) return;
          const chunk = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
          if (assembler.push(chunk)) {
            responseCharacteristic.removeEventListener('characteristicvaluechanged', onNotify);
            resolve(assembler.takeResponse());
          }
        } catch (error) {
          responseCharacteristic.removeEventListener('characteristicvaluechanged', onNotify);
          reject(error);
        }
      };
      responseCharacteristic.addEventListener('characteristicvaluechanged', onNotify);
    });

    await responseCharacteristic.startNotifications();

    for (const chunk of encodeProvisionRequest(request)) {
      await requestCharacteristic.writeValueWithoutResponse(chunk);
    }

    return await responsePromise;
  } finally {
    server.disconnect();
  }
}
