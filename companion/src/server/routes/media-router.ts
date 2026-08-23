import fsPromises from 'node:fs/promises';
import { Router } from 'express';
import type { MediaLibraryPort } from '../media/media-library';
import { guessContentType } from '../media/media-library';
import { PathTraversalError } from '../media/path-safety';
import { buildUsbMediaUrl, buildLanMediaUrl, LanMediaUrlUnavailableError } from '../media/media-url';
import type { DeviceManagerPort } from '../device-manager';
import { DeviceApiError } from '../proxy-client';
import { logger } from '../logger';

/** Content types this scaffolding explicitly does not accept for uploads. */
const UNSUPPORTED_UPLOAD_CONTENT_TYPES = ['multipart/form-data'];

export interface MediaRouterOptions {
  /** This companion's own HTTP port (used for the LAN media URL, and as
   * the `adb reverse` target for USB media playback). */
  companionPort: number;
  /** Optional COMPANION_PUBLIC_URL override for LAN media URLs. */
  publicUrl?: string;
  /**
   * The configured COMPANION_ACCESS_TOKEN, if any. When set and the active
   * connection is LAN, this is sent to the device (inside the play
   * request's `headers` field) so the device attaches it as
   * `X-Companion-Token` on its own fetch of the hosted media URL -- the
   * same header the companion's `/media-files` route requires once it is
   * bound to a non-loopback host (see security.ts). USB playback URLs
   * always point at 127.0.0.1 via the `adb reverse` tunnel, which mirrors
   * a loopback-style origin from the device's perspective, so no token is
   * attached there.
   */
  accessToken?: string;
}

/**
 * Media library scaffolding: list, upload (raw body stream), delete, and
 * range-capable serving, all confined to the configured media root.
 *
 * This router must never have a JSON (or other) body-parsing middleware
 * applied ahead of it: uploads stream the raw request body straight to
 * disk, and a body parser would consume the stream first, silently
 * producing a 0-byte file. See app.ts for where body parsing is scoped.
 */
export function createMediaRouter(
  mediaLibrary: MediaLibraryPort,
  deviceManager: DeviceManagerPort,
  options: MediaRouterOptions,
): Router {
  const router = Router();

  router.get('/media', async (_req, res, next) => {
    try {
      const entries = await mediaLibrary.list();
      res.json({ entries });
    } catch (error) {
      next(error);
    }
  });

  // Upload via PUT with the target filename in the path, streaming the
  // raw request body directly to disk. This avoids adding a multipart
  // parsing dependency for the initial scaffolding; a browser multipart
  // form can be layered on top by extracting a filename client-side and
  // issuing this same PUT with the file body. Any content type other than
  // multipart/form-data (including application/json, application/
  // octet-stream, or no content type at all) is accepted and its raw bytes
  // are preserved byte-for-byte, since no body-parsing middleware runs
  // ahead of this route (see app.ts / createMediaRouter doc comment).
  router.put('/media/:name', async (req, res) => {
    const contentType = (req.headers['content-type'] ?? '').toLowerCase();
    if (UNSUPPORTED_UPLOAD_CONTENT_TYPES.some((unsupported) => contentType.includes(unsupported))) {
      res.status(415).json({ error: 'Unsupported content type: multipart uploads are not supported yet' });
      return;
    }

    try {
      const entry = await mediaLibrary.save(req.params.name, req);
      res.status(201).json({ entry });
    } catch (error) {
      if (error instanceof PathTraversalError) {
        res.status(400).json({ error: 'Invalid media file name' });
        return;
      }
      logger.warn('media upload failed', { message: (error as Error).message });
      res.status(500).json({ error: 'Upload failed' });
    }
  });

  router.delete('/media/:name', async (req, res) => {
    try {
      await mediaLibrary.remove(req.params.name);
      res.status(204).end();
    } catch (error) {
      if (error instanceof PathTraversalError) {
        res.status(400).json({ error: 'Invalid media file name' });
        return;
      }
      res.status(404).json({ error: 'Media file not found' });
    }
  });

  // Convenience action for the "Play" button beside a hosted file: builds
  // a playback URL the connected device can actually reach (USB via `adb
  // reverse`, LAN via COMPANION_PUBLIC_URL or an auto-selected private
  // interface address -- never a browser-supplied Host header) and asks
  // the device to play it.
  router.post('/media/:name/play', async (req, res) => {
    const name = req.params.name;
    let target: string;
    try {
      target = mediaLibrary.resolve(name);
    } catch (error) {
      if (error instanceof PathTraversalError) {
        res.status(400).json({ error: 'Invalid media file name' });
        return;
      }
      logger.warn('media play failed to resolve path', { message: (error as Error).message });
      res.status(500).json({ error: 'Unable to resolve media file' });
      return;
    }

    try {
      await fsPromises.access(target);
    } catch {
      res.status(404).json({ error: 'Media file not found' });
      return;
    }

    const connection = deviceManager.getConnection();
    if (!connection) {
      res.status(409).json({ error: 'No device connected' });
      return;
    }

    try {
      const url =
        connection.mode === 'usb'
          ? buildUsbMediaUrl(await deviceManager.ensureUsbReverseTunnel(options.companionPort), name)
          : buildLanMediaUrl({ publicUrl: options.publicUrl, companionPort: options.companionPort }, name);

      // Only LAN playback needs the companion access token: that URL points
      // back at this companion over the network (where the token gate
      // applies), while the USB URL is always a 127.0.0.1 address reached
      // through the adb-reverse tunnel. Never send the token if none is
      // configured (the default loopback-only setup).
      const headers: Record<string, string> | undefined =
        connection.mode === 'lan' && options.accessToken
          ? { 'X-Companion-Token': options.accessToken }
          : undefined;

      const client = deviceManager.getClient();
      const { status, body } = await client.playMedia({ url, mimeType: guessContentType(target), headers });
      res.status(status).json(body);
    } catch (error) {
      if (error instanceof DeviceApiError) {
        res.status(error.status >= 400 ? error.status : 502).json({ error: error.message });
        return;
      }
      if (error instanceof LanMediaUrlUnavailableError) {
        res.status(502).json({ error: error.message });
        return;
      }
      logger.warn('media play failed', { message: (error as Error).message });
      res.status(502).json({ error: (error as Error).message });
    }
  });

  return router;
}

/**
 * Separate router mounted at the web root (not under /api) so served
 * media URLs are short and playable directly by the Mirror's media player,
 * e.g. http://<companion-lan-ip>:<port>/media-files/song.mp3
 */
export function createMediaFilesRouter(mediaLibrary: MediaLibraryPort): Router {
  const router = Router();

  router.get('/media-files/*', async (req, res, next) => {
    try {
      const params = req.params as Record<string, string>;
      const name = params[0] ?? '';
      await mediaLibrary.serve(req, res, name);
    } catch (error) {
      next(error);
    }
  });

  return router;
}
