import { logger } from './logger';

/**
 * Thin client for the Mirror Home device control API, reached through the
 * companion's adb-forwarded loopback port. This is the only module that
 * attaches the Authorization header; it deliberately never places the
 * token in a URL (query string or path) and never logs header values.
 */

export class DeviceApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly body: unknown,
  ) {
    super(message);
    this.name = 'DeviceApiError';
  }
}

export interface DeviceClientOptions {
  /** Loopback port the adb forward currently targets, e.g. 18787. */
  port: number;
  /**
   * Device host to reach. Defaults to 127.0.0.1 (the usual adb-forwarded
   * loopback case). LAN connections set this to the device's own private
   * IPv4 address (validated by net-validation.ts before it ever reaches
   * here).
   */
  host?: string;
  /** Bearer token to attach for authenticated endpoints, if any. */
  token?: string | null;
}

export interface DeviceApiResult {
  status: number;
  body: unknown;
}

export interface PlayMediaRequest {
  url: string;
  mimeType?: string;
  title?: string;
  time?: number;
  volume?: number;
  speed?: number;
  /**
   * Extra HTTP headers the *device* should attach when it fetches `url`
   * back from the companion (e.g. `X-Companion-Token` for a LAN-bound
   * companion whose `/media-files` route requires it). These are sent to
   * the device inside the JSON request body -- never appended to `url`
   * itself and never logged -- so the device API must be told how to
   * forward them onto its own outbound media fetch.
   */
  headers?: Record<string, string>;
}

/**
 * Public surface of DeviceClient, extracted so routers and their tests can
 * depend on this interface instead of the concrete class. TypeScript
 * enforces nominal typing for classes with private members, so a plain
 * fake/mock object could not otherwise satisfy the DeviceClient type
 * directly in tests.
 */
export interface DeviceClientPort {
  getStatus(): Promise<DeviceApiResult>;
  pair(code: string): Promise<DeviceApiResult>;
  getDashboard(): Promise<DeviceApiResult>;
  setDashboard(url: string): Promise<DeviceApiResult>;
  configureWifi(ssid: string, passphrase: string, hidden: boolean): Promise<DeviceApiResult>;
  setBrightness(value: number): Promise<DeviceApiResult>;
  setName(name: string): Promise<DeviceApiResult>;
  revokePairing(): Promise<DeviceApiResult>;
  getSystemStatus(): Promise<DeviceApiResult>;
  prepareKiosk(): Promise<DeviceApiResult>;
  setSystemHome(enabled: boolean): Promise<DeviceApiResult>;
  getMediaStatus(): Promise<DeviceApiResult>;
  playMedia(request: PlayMediaRequest): Promise<DeviceApiResult>;
  pauseMedia(): Promise<DeviceApiResult>;
  resumeMedia(): Promise<DeviceApiResult>;
  stopMedia(): Promise<DeviceApiResult>;
  seekMedia(time: number): Promise<DeviceApiResult>;
  setMediaVolume(volume: number): Promise<DeviceApiResult>;
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

export class DeviceClient implements DeviceClientPort {
  constructor(private readonly options: DeviceClientOptions) {}

  private baseUrl(): string {
    return `http://${this.options.host ?? '127.0.0.1'}:${this.options.port}`;
  }

  private async request(
    method: string,
    apiPath: string,
    options: { body?: unknown; authenticated?: boolean } = {},
  ): Promise<{ status: number; body: unknown }> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (options.authenticated) {
      if (!this.options.token) {
        throw new DeviceApiError('Not paired: no bearer token available', 401, null);
      }
      headers.Authorization = `Bearer ${this.options.token}`;
    }

    // Log only method + path (never headers, never the token, never a
    // querystring, since these endpoints never receive one).
    logger.info('device api request', { method, path: apiPath });

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl()}${apiPath}`, {
        method,
        headers,
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      });
    } catch (error) {
      throw new DeviceApiError(
        `Unable to reach the device over the forwarded port: ${(error as Error).message}`,
        502,
        null,
      );
    }

    const body = await parseBody(response);
    logger.info('device api response', { method, path: apiPath, status: response.status });
    return { status: response.status, body };
  }

  getStatus() {
    return this.request('GET', '/api/v1/status', {
      authenticated: Boolean(this.options.token),
    });
  }

  pair(code: string) {
    return this.request('POST', '/api/v1/pair', { body: { code } });
  }

  getDashboard() {
    return this.request('GET', '/api/v1/dashboard', { authenticated: true });
  }

  setDashboard(url: string) {
    return this.request('PUT', '/api/v1/dashboard', { authenticated: true, body: { url } });
  }

  configureWifi(ssid: string, passphrase: string, hidden: boolean) {
    return this.request('POST', '/api/v1/wifi/configure', {
      authenticated: true,
      body: { ssid, passphrase, hidden },
    });
  }

  setBrightness(value: number) {
    return this.request('POST', '/api/v1/control/brightness', {
      authenticated: true,
      body: { value },
    });
  }

  setName(name: string) {
    return this.request('POST', '/api/v1/control/name', { authenticated: true, body: { name } });
  }

  revokePairing() {
    return this.request('POST', '/api/v1/pair/revoke', { authenticated: true });
  }

  getSystemStatus() {
    return this.request('GET', '/api/v1/system', { authenticated: true });
  }

  prepareKiosk() {
    return this.request('POST', '/api/v1/system/prepare-kiosk', { authenticated: true });
  }

  setSystemHome(enabled: boolean) {
    return this.request('POST', '/api/v1/system/home', { authenticated: true, body: { enabled } });
  }

  getMediaStatus() {
    return this.request('GET', '/api/v1/media/status', { authenticated: true });
  }

  playMedia(mediaRequest: PlayMediaRequest) {
    return this.request('POST', '/api/v1/media/play', {
      authenticated: true,
      body: {
        url: mediaRequest.url,
        mimeType: mediaRequest.mimeType,
        title: mediaRequest.title,
        time: mediaRequest.time,
        volume: mediaRequest.volume,
        speed: mediaRequest.speed,
        headers: mediaRequest.headers,
      },
    });
  }

  pauseMedia() {
    return this.request('POST', '/api/v1/media/pause', { authenticated: true });
  }

  resumeMedia() {
    return this.request('POST', '/api/v1/media/resume', { authenticated: true });
  }

  stopMedia() {
    return this.request('POST', '/api/v1/media/stop', { authenticated: true });
  }

  seekMedia(time: number) {
    return this.request('POST', '/api/v1/media/seek', { authenticated: true, body: { time } });
  }

  setMediaVolume(volume: number) {
    return this.request('POST', '/api/v1/media/volume', { authenticated: true, body: { volume } });
  }
}
