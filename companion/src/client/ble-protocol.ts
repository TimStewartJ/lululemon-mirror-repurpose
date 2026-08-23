/**
 * Pure, dependency-free BLE provisioning protocol helpers: request framing/
 * chunking and response assembly/parsing. Kept free of any
 * navigator.bluetooth or DOM calls (beyond the universally-available
 * TextEncoder/TextDecoder globals) so they can be unit-tested directly --
 * see src/server/__tests__/ble-protocol.test.ts, which dynamically imports
 * this compiled module from the Node test runner without a real Bluetooth
 * stack.
 *
 * Protocol (mirrors android/mirror-home BleProvisioningServer.java):
 *  - Service UUID 7d7a0001-6d69-7272-6f72-726570757270
 *  - Write (request) characteristic UUID 7d7a0002-6d69-7272-6f72-726570757270
 *  - Notify/read (response) characteristic UUID 7d7a0003-6d69-7272-6f72-726570757270
 *  - Requests and responses are newline (\n, 0x0A) terminated compact JSON.
 *  - The browser writes request bytes in chunks no larger than
 *    MAX_REQUEST_CHUNK_BYTES so they safely fit a legacy default ATT MTU
 *    payload without depending on MTU negotiation succeeding. A multi-byte
 *    UTF-8 sequence may be split across two chunks; this is fine because
 *    the device buffers raw bytes and only decodes/parses once the
 *    terminating newline byte has been seen across all writes.
 */

export const BLE_SERVICE_UUID = '7d7a0001-6d69-7272-6f72-726570757270';
export const BLE_REQUEST_CHARACTERISTIC_UUID = '7d7a0002-6d69-7272-6f72-726570757270';
export const BLE_RESPONSE_CHARACTERISTIC_UUID = '7d7a0003-6d69-7272-6f72-726570757270';

/** Conservative browser-side write chunk size (bytes). */
export const MAX_REQUEST_CHUNK_BYTES = 18;

export interface ProvisionRequest {
  code: string;
  ssid: string;
  passphrase: string;
  hidden: boolean;
}

export interface ProvisionResponse {
  ok: boolean;
  token?: string;
  message?: string;
  ipAddress?: string | null;
  apiPort?: number;
  error?: string;
}

const encoder = new TextEncoder();
const decoder = new TextDecoder();

/** Splits a byte array into chunks of at most maxBytes each, in order. */
export function chunkBytes(bytes: Uint8Array, maxBytes: number): Uint8Array[] {
  if (maxBytes <= 0) throw new Error('maxBytes must be positive');
  const chunks: Uint8Array[] = [];
  for (let offset = 0; offset < bytes.length; offset += maxBytes) {
    chunks.push(bytes.slice(offset, offset + maxBytes));
  }
  if (chunks.length === 0) chunks.push(new Uint8Array(0));
  return chunks;
}

/**
 * Encodes a provisioning request as compact, newline-terminated JSON
 * (field order/shape matching BleProvisioningServer.java's expectations
 * exactly: {type:"provision", code, ssid, passphrase, hidden}), split into
 * chunks of at most MAX_REQUEST_CHUNK_BYTES bytes each.
 */
export function encodeProvisionRequest(request: ProvisionRequest): Uint8Array[] {
  const payload = JSON.stringify({
    type: 'provision',
    code: request.code,
    ssid: request.ssid,
    passphrase: request.passphrase,
    hidden: request.hidden,
  });
  const bytes = encoder.encode(`${payload}\n`);
  return chunkBytes(bytes, MAX_REQUEST_CHUNK_BYTES);
}

export interface ResponseAssembler {
  /**
   * Feed a newly received notification chunk. Returns true once a complete
   * newline-terminated response is available; call takeResponse() next.
   */
  push(chunk: Uint8Array): boolean;
  /** True once push() has returned true and takeResponse() hasn't run yet. */
  isComplete(): boolean;
  /** Parses and returns the buffered response, then resets for reuse. */
  takeResponse(): ProvisionResponse;
}

/**
 * Accumulates raw notification byte chunks until a newline (0x0A) byte
 * terminates a complete JSON response, mirroring the device's own framing.
 */
export function createResponseAssembler(): ResponseAssembler {
  let buffer: number[] = [];
  let complete = false;

  return {
    push(chunk: Uint8Array): boolean {
      if (complete) {
        // A previous response was never consumed via takeResponse(); drop
        // it and start fresh so a stray leftover notification can't
        // corrupt the next parse.
        buffer = [];
        complete = false;
      }
      for (const byte of chunk) {
        if (byte === 0x0a) {
          complete = true;
          break;
        }
        buffer.push(byte);
      }
      return complete;
    },
    isComplete(): boolean {
      return complete;
    },
    takeResponse(): ProvisionResponse {
      if (!complete) {
        throw new Error('Response is not complete yet');
      }
      const text = decoder.decode(new Uint8Array(buffer));
      buffer = [];
      complete = false;
      const parsed = JSON.parse(text) as Partial<ProvisionResponse>;
      if (typeof parsed.ok !== 'boolean') {
        throw new Error('Malformed provisioning response: missing "ok" field');
      }
      return parsed as ProvisionResponse;
    },
  };
}
