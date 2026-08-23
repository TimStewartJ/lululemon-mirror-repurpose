import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';

/**
 * Local, gitignored persistence for the companion's pairing token and a
 * few small cached preferences. This file never stores Wi-Fi passphrases.
 *
 * Permissions are best-effort: on POSIX we chmod the file to 0600
 * immediately after writing. On Windows, POSIX permission bits are not
 * meaningful; we additionally attempt an `icacls` ACL restriction to the
 * current user, but this is advisory only and failures are non-fatal.
 */

export interface PersistedConfig {
  /** Bearer token returned by POST /api/v1/pair. Never a passphrase. */
  token: string | null;
  /** Cached last-known dashboard URL, purely for UI convenience. */
  dashboardUrl?: string;
  /** Cached last-known display name, purely for UI convenience. */
  displayName?: string;
}

const DEFAULT_CONFIG: PersistedConfig = { token: null };

export class ConfigStore {
  private readonly filePath: string;
  private cache: PersistedConfig | null = null;

  constructor(dataDir: string, fileName = 'config.json') {
    this.filePath = path.join(dataDir, fileName);
  }

  private ensureDataDir(): void {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
  }

  load(): PersistedConfig {
    if (this.cache) return this.cache;
    try {
      const raw = fs.readFileSync(this.filePath, 'utf8');
      const parsed = JSON.parse(raw) as Partial<PersistedConfig>;
      this.cache = { ...DEFAULT_CONFIG, ...parsed };
    } catch {
      this.cache = { ...DEFAULT_CONFIG };
    }
    return this.cache;
  }

  private save(config: PersistedConfig): void {
    this.ensureDataDir();
    this.cache = config;
    // Create/truncate with owner-only mode up front (best-effort on all
    // platforms; meaningful on POSIX, ignored on Windows).
    fs.writeFileSync(this.filePath, JSON.stringify(config, null, 2), { mode: 0o600 });
    this.restrictPermissions();
  }

  private restrictPermissions(): void {
    try {
      fs.chmodSync(this.filePath, 0o600);
    } catch {
      // Best-effort only; ignore platforms/filesystems that reject chmod.
    }
    if (process.platform === 'win32') {
      // Best-effort ACL tightening: restrict to the current user. Failure
      // (e.g. icacls unavailable, or non-NTFS volume) must never crash the
      // companion; the JSON file's contents are still gated behind pairing.
      const username = process.env.USERNAME;
      if (username) {
        execFile(
          'icacls.exe',
          [this.filePath, '/inheritance:r', '/grant:r', `${username}:F`],
          { windowsHide: true },
          () => {
            /* ignore result; advisory hardening only */
          },
        );
      }
    }
  }

  getToken(): string | null {
    return this.load().token;
  }

  setToken(token: string | null): void {
    const config = this.load();
    this.save({ ...config, token });
  }

  setDashboardUrl(url: string): void {
    const config = this.load();
    this.save({ ...config, dashboardUrl: url });
  }

  setDisplayName(name: string): void {
    const config = this.load();
    this.save({ ...config, displayName: name });
  }
}
