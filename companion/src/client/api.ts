/**
 * Thin fetch wrappers for the companion's own API (never the device API
 * directly — the browser always talks same-origin to the companion,
 * which proxies to the device so the device API never needs CORS).
 */

import { getStoredToken, ACCESS_TOKEN_HEADER } from './token.js';

function authHeaders(extra?: HeadersInit): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getStoredToken();
  if (token) headers[ACCESS_TOKEN_HEADER] = token;
  if (extra) Object.assign(headers, extra);
  return headers;
}

async function requestJson<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    ...init,
    headers: authHeaders({ 'Content-Type': 'application/json', ...(init?.headers ?? {}) }),
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const message = body && typeof body.error === 'string' ? body.error : `Request failed (${response.status})`;
    throw new Error(message);
  }
  return body as T;
}

export interface DiscoveredDevice {
  serial: string;
  state: string;
  supported: boolean;
  reasons: string[];
}

export const companionApi = {
  listDevices: () => requestJson<{ devices: DiscoveredDevice[] }>('/api/companion/devices'),
  connectDevice: (serial: string) =>
    requestJson<{ connected: boolean; localPort: number }>('/api/companion/devices/connect', {
      method: 'POST',
      body: JSON.stringify({ serial }),
    }),
  disconnectDevice: () =>
    requestJson<{ connected: boolean }>('/api/companion/devices/disconnect', { method: 'POST' }),
  bluetoothStatus: () =>
    requestJson<{ supported: boolean; reason?: string }>('/api/companion/bluetooth/status'),
  connectDeviceLan: (token: string, ipAddress: string) =>
    requestJson<{ connected: boolean; mode: string; host: string }>('/api/companion/devices/lan/connect', {
      method: 'POST',
      body: JSON.stringify({ token, ipAddress }),
    }),

  deviceStatus: () => requestJson<Record<string, unknown>>('/api/device/status'),
  pair: (code: string) =>
    requestJson<{ paired: boolean }>('/api/device/pair', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),
  revokePairing: () => requestJson<unknown>('/api/device/pair/revoke', { method: 'POST' }),

  setDashboardUrl: (url: string) =>
    requestJson<unknown>('/api/device/dashboard', { method: 'PUT', body: JSON.stringify({ url }) }),
  setName: (name: string) =>
    requestJson<unknown>('/api/device/control/name', { method: 'POST', body: JSON.stringify({ name }) }),
  setBrightness: (value: number) =>
    requestJson<unknown>('/api/device/control/brightness', {
      method: 'POST',
      body: JSON.stringify({ value }),
    }),
  configureWifi: (ssid: string, passphrase: string, hidden: boolean) =>
    requestJson<{ accepted: boolean; message: string }>('/api/device/wifi/configure', {
      method: 'POST',
      body: JSON.stringify({ ssid, passphrase, hidden }),
    }),

  systemStatus: () =>
    requestJson<{ connected: boolean; capabilities?: string[] }>('/api/device/system'),
  prepareKiosk: () => requestJson<{ prepared: boolean }>('/api/device/system/prepare-kiosk', { method: 'POST' }),
  setSystemHome: (enabled: boolean) =>
    requestJson<{ changed: boolean; enabled: boolean }>('/api/device/system/home', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),

  mediaStatus: () =>
    requestJson<{
      state: string;
      title: string | null;
      positionSeconds: number;
      durationSeconds: number | null;
      volume: number;
    }>('/api/device/media/status'),
  pauseMedia: () => requestJson<unknown>('/api/device/media/pause', { method: 'POST' }),
  resumeMedia: () => requestJson<unknown>('/api/device/media/resume', { method: 'POST' }),
  stopMedia: () => requestJson<unknown>('/api/device/media/stop', { method: 'POST' }),
  seekMedia: (time: number) =>
    requestJson<unknown>('/api/device/media/seek', { method: 'POST', body: JSON.stringify({ time }) }),
  setMediaVolume: (volume: number) =>
    requestJson<unknown>('/api/device/media/volume', { method: 'POST', body: JSON.stringify({ volume }) }),
  playHostedMedia: (name: string) =>
    requestJson<unknown>(`/api/companion/media/${encodeURIComponent(name)}/play`, { method: 'POST' }),

  listMedia: () => requestJson<{ entries: { name: string; sizeBytes: number }[] }>('/api/companion/media'),
  uploadMedia: async (file: File): Promise<void> => {
    const response = await fetch(`/api/companion/media/${encodeURIComponent(file.name)}`, {
      method: 'PUT',
      headers: authHeaders(),
      body: file,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error ?? `Upload failed (${response.status})`);
    }
  },
  deleteMedia: async (name: string): Promise<void> => {
    const response = await fetch(`/api/companion/media/${encodeURIComponent(name)}`, {
      method: 'DELETE',
      headers: authHeaders(),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error ?? `Delete failed (${response.status})`);
    }
  },
};

