import fs from 'node:fs';
import fsPromises from 'node:fs/promises';
import path from 'node:path';
import type { Request, Response } from 'express';
import { parseRangeHeader } from './range';
import { resolveMediaPath, PathTraversalError } from './path-safety';

/**
 * Scaffolding for local media hosting: list, upload (raw body -> file), and
 * range-capable file serving, all confined to a configured media root.
 * This intentionally supports single-file uploads via a filename in the
 * URL path (not multipart), keeping the dependency surface minimal. A
 * multipart/resumable upload UI can be layered on top of this later
 * without changing the storage or serving primitives.
 */

export interface MediaEntry {
  name: string;
  sizeBytes: number;
  modifiedAt: string;
}

/**
 * Public surface of MediaLibrary. The media router depends on this
 * interface (not the concrete class) so tests can substitute a lightweight
 * fake, e.g. to exercise error-handling paths without touching disk.
 */
export interface MediaLibraryPort {
  list(): Promise<MediaEntry[]>;
  resolve(name: string): string;
  save(name: string, body: NodeJS.ReadableStream): Promise<MediaEntry>;
  remove(name: string): Promise<void>;
  serve(req: Request, res: Response, name: string): Promise<void>;
}

const MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024; // 4 GiB scaffolding ceiling

export class MediaLibrary implements MediaLibraryPort {
  constructor(private readonly root: string) {}

  async ensureRoot(): Promise<void> {
    await fsPromises.mkdir(this.root, { recursive: true });
  }

  async list(): Promise<MediaEntry[]> {
    await this.ensureRoot();
    const names = await fsPromises.readdir(this.root);
    const entries: MediaEntry[] = [];
    for (const name of names) {
      const fullPath = path.join(this.root, name);
      const stat = await fsPromises.stat(fullPath);
      if (!stat.isFile()) continue;
      entries.push({ name, sizeBytes: stat.size, modifiedAt: stat.mtime.toISOString() });
    }
    return entries.sort((a, b) => a.name.localeCompare(b.name));
  }

  resolve(name: string): string {
    return resolveMediaPath(this.root, name);
  }

  async save(name: string, body: NodeJS.ReadableStream): Promise<MediaEntry> {
    await this.ensureRoot();
    const destination = this.resolve(name);
    await fsPromises.mkdir(path.dirname(destination), { recursive: true });

    const tempPath = `${destination}.upload-${process.pid}-${Date.now()}`;
    let bytesWritten = 0;

    await new Promise<void>((resolve, reject) => {
      const writeStream = fs.createWriteStream(tempPath, { mode: 0o600 });
      const onError = (error: Error) => {
        writeStream.destroy();
        fs.rm(tempPath, { force: true }, () => reject(error));
      };

      body.on('data', (chunk: Buffer) => {
        bytesWritten += chunk.length;
        if (bytesWritten > MAX_UPLOAD_BYTES) {
          (body as NodeJS.ReadableStream & { destroy?: () => void }).destroy?.();
          onError(new Error('Upload exceeds the maximum allowed size'));
        }
      });
      body.on('error', onError);
      writeStream.on('error', onError);
      writeStream.on('finish', resolve);
      body.pipe(writeStream);
    });

    await fsPromises.rename(tempPath, destination);
    const stat = await fsPromises.stat(destination);
    return { name, sizeBytes: stat.size, modifiedAt: stat.mtime.toISOString() };
  }

  async remove(name: string): Promise<void> {
    const target = this.resolve(name);
    await fsPromises.unlink(target);
  }

  /**
   * Serves a media file with HTTP Range support. Handles path confinement,
   * 404s, and 200/206/416 status selection; does not set CORS headers
   * (media is same-origin, consistent with the rest of the companion).
   */
  async serve(req: Request, res: Response, name: string): Promise<void> {
    let target: string;
    try {
      target = this.resolve(name);
    } catch (error) {
      if (error instanceof PathTraversalError) {
        res.status(400).json({ error: 'Invalid media path' });
        return;
      }
      throw error;
    }

    let stat: fs.Stats;
    try {
      stat = await fsPromises.stat(target);
    } catch {
      res.status(404).json({ error: 'Media file not found' });
      return;
    }
    if (!stat.isFile()) {
      res.status(404).json({ error: 'Media file not found' });
      return;
    }

    const contentType = guessContentType(target);
    const rangeResult = parseRangeHeader(req.headers.range, stat.size);

    res.setHeader('Accept-Ranges', 'bytes');
    res.setHeader('Content-Type', contentType);

    if (rangeResult.kind === 'unsatisfiable') {
      res.setHeader('Content-Range', `bytes */${stat.size}`);
      res.status(416).end();
      return;
    }

    if (rangeResult.kind === 'single') {
      const { start, end } = rangeResult.range;
      res.status(206);
      res.setHeader('Content-Range', `bytes ${start}-${end}/${stat.size}`);
      res.setHeader('Content-Length', String(end - start + 1));
      fs.createReadStream(target, { start, end }).pipe(res);
      return;
    }

    // 'none' or 'unsupported': serve the full file.
    res.status(200);
    res.setHeader('Content-Length', String(stat.size));
    fs.createReadStream(target).pipe(res);
  }
}

const CONTENT_TYPES: Record<string, string> = {
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.mkv': 'video/x-matroska',
  '.mp3': 'audio/mpeg',
  '.m4a': 'audio/mp4',
  '.wav': 'audio/wav',
  '.flac': 'audio/flac',
  '.ogg': 'audio/ogg',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
};

export function guessContentType(filePath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  return CONTENT_TYPES[ext] ?? 'application/octet-stream';
}
