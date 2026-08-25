# Mirror Companion

A local Node.js service and static browser UI for setting up and controlling
a repurposed MIRROR device running Mirror Home. See the repository root
[architecture](../docs/architecture.md) and [protocol](../docs/protocol.md)
docs for how the companion fits into the wider project.

Mirror Home now serves its primary responsive controls directly from port
`8787`; this desktop companion is optional. Keep it for ADB recovery, USB media
hosting, development, or managing a Mirror before normal LAN access is
available.

The companion never talks to the device API directly from the browser: the
device API intentionally has no CORS headers, so the static UI calls
same-origin companion endpoints, and the companion server forwards those calls to the device over an `adb forward`-ed
loopback port or a validated LAN connection, attaching the bearer token itself.

## Features

- **ADB discovery**: finds `adb` via `ADB_PATH`, `ANDROID_SDK_ROOT`/`ANDROID_HOME`
  platform-tools, or `PATH`.
- **Device validation**: lists connected devices and validates the exact
  product/device/build-ID/fingerprint against the supported hardware profile
  (mirrors `tools/device-profiles/ifc6309-mirror-329.json`). Mismatched or
  unauthorized devices are reported, never silently accepted.
- **Port forwarding**: `adb forward` to `127.0.0.1:18787` (configurable), or a
  dynamically allocated host port if that one is already taken.
- **Same-origin proxy**: `/api/device/*` endpoints proxy to the device's
  `/api/v1/*` control API without ever placing the bearer token in a URL or
  a log line.
- **System helper proxy**: `GET /api/device/system`,
  `POST /api/device/system/prepare-kiosk`, and
  `POST /api/device/system/home` proxy the device's system-helper status,
  kiosk-mode preparation, and default-launcher (Mirror Home vs. stock HOME)
  switch endpoints. The UI has "Prepare kiosk mode", "Use Mirror Home", and
  "Restore stock HOME" buttons for these.
- **LAN access control**: loopback-only deployments (the default) require no
  extra setup; binding the companion to a LAN-reachable host requires a
  configured `COMPANION_ACCESS_TOKEN`, enforced via an `X-Companion-Token`
  header on every `/api/device`, `/api/companion`, and `/media-files`
  request. The companion refuses to start on a non-loopback host without one.
- **Pairing token storage**: stored in a local, gitignored JSON file with a
  best-effort restrictive file mode (`0600` on POSIX; an advisory `icacls`
  ACL tightening is attempted on Windows).
- **Wi-Fi provisioning form**: the passphrase is forwarded to the device and
  is never written to disk or logged by the companion.
- **Dashboard / name / brightness controls** mapped 1:1 to the device API.
- **Media library**: confined-path file listing, single-file
  upload (raw PUT body -> disk), deletion, and HTTP range-capable serving
  for LAN playback on the Mirror.
- **USB media reverse**: uses a bounded fixed-port fallback because Android 6
  does not report dynamically allocated `adb reverse tcp:0` ports.
- **Device media controls**: status/play/pause/resume/seek/volume/stop,
  proxied to the device's authenticated media API, plus Play/Stop buttons
  next to each hosted media file that build a playback URL the device can
  actually reach (USB via `adb reverse`, LAN via `COMPANION_PUBLIC_URL` or
  an auto-selected private interface address -- never a browser-supplied
  `Host` header).
- **LAN connection mode**: connects to the device directly over the
  network using an IPv4 host validated as RFC1918/link-local only (no
  hostnames, no DNS resolution, no loopback) to prevent SSRF via an
  attacker-supplied address; persists only the pairing token, never the
  Wi-Fi passphrase.

## Requirements

- Node.js 22+ and npm.
- `adb` (Android Platform Tools) available via `ADB_PATH`, the Android SDK,
  or `PATH`, for the USB setup flow.

## Getting started

```powershell
cd companion
npm install
npm run build
npm start
```

Then open `http://127.0.0.1:4317` (or your configured `COMPANION_PORT`).

Copy `.env.example` to `.env` and adjust as needed; see that file for every
supported environment variable. `.env` itself is gitignored and must never
be committed.

## Development

```powershell
npm run build:server   # compile src/server -> dist/server (CommonJS)
npm run build:client   # compile src/client -> dist/client (browser ES modules)
npm run build:assets   # copy src/client/public/* into dist/client
npm run build          # all of the above, from a clean dist/
npm test                # builds server + client + assets, then runs node:test against dist/server
npm run typecheck       # type-check both server and client without emitting
```

`npm test`'s `pretest` step builds the client too (not just the server)
because one test file (`client-api-delete-media.test.ts`) dynamically imports
the *compiled* `dist/client/api.js` as a real ES module, to unit-test the
browser-side fetch wrapper without duplicating it server-side.
`scripts/copy-assets.mjs` writes a `dist/client/package.json` with
`{"type": "module"}` so Node's loader treats that directory as ESM.

Tests use Node's built-in test runner (`node:test`) against the compiled
server output and cover:

- HTTP `Range` header parsing (single ranges, suffix ranges, clamping,
  multi-range/malformed rejection, zero-size resources).
- Media path confinement (parent traversal, absolute paths, drive letters,
  UNC paths, embedded NUL bytes, and the no-double-decode contract:
  already-decoded traversal sequences are rejected, still-encoded literals
  and filenames with a genuine `%` character are accepted unchanged).
- `adb devices -l` and `adb shell getprop` output parsing.
- `adb` executable discovery precedence (`ADB_PATH` > SDK env vars > `PATH`).
- Exact device-profile validation (product/device/build ID/fingerprint).
- Local config-store token persistence, and that Wi-Fi passphrases are
  never present in the persisted file.
- Async error handling in `GET /api/companion/media` and
  `POST /api/companion/devices/disconnect` (both return a proper error
  response instead of hanging or crashing the process).
- Upload content-type handling: raw byte preservation for `application/json`
  and unlabeled binary uploads, and explicit `415` rejection of
  `multipart/form-data`.
- End-to-end percent-encoding / path-traversal behavior over real HTTP
  requests to `/media-files/*` and `/media/:name`.
- `COMPANION_ACCESS_TOKEN` / `COMPANION_HOST` loopback policy: startup
  fail-fast, the `X-Companion-Token` middleware (missing/wrong/correct
  header), and full-app wiring across `/api/device`, `/api/companion`, and
  `/media-files`.
- The new `/api/device/system` and `/api/device/system/prepare-kiosk` proxy
  routes.
- `/api/device/system/home` request validation (rejects a missing/
  non-boolean `enabled` field with 400) and both the enable and disable
  paths, plus the same unauthenticated-device 401 mapping as the other
  system-helper routes.
- Private/RFC1918/link-local IPv4 host validation (`net-validation.ts`),
  including leading-zero and boundary-of-range rejection and the deliberate
  loopback exclusion (an SSRF guard against an attacker-supplied address).
- LAN connection mode (`DeviceManager.connectLan`): accepts valid private
  hosts, rejects public/loopback/hostname values, persists only the token,
  and its `disconnect()`/`getClient()` branches.
- Media URL generation (`media-url.ts`): USB loopback URLs, LAN URLs via
  `COMPANION_PUBLIC_URL` and via auto-selected private interfaces (with
  injectable `os.networkInterfaces()` data), and the "no reachable address"
  error case.
- `adb reverse`/`adb reverse --remove` argument-array building (pure
  functions, mirroring the existing `adb forward` argument tests).
- The device media control proxy routes (`/api/device/media/*`) and
  `/api/companion/media/:name/play`, covering the USB `adb reverse` URL
  branch, the LAN `COMPANION_PUBLIC_URL` branch (including a deliberately
  spoofed `Host` header to prove it has no effect), 404 for a missing file,
  409 for no active connection, and `/api/companion/devices/lan/connect`
  request validation and SSRF-guard error surfacing.

## API surface

All `/api/device/*` routes proxy to the device's matching `/api/v1/*`
endpoint (see `docs/protocol.md`), attaching the stored bearer token for the
authenticated ones:

| Companion route | Device route | Device auth |
|---|---|---|
| `GET /api/device/status` | `GET /api/v1/status` | no |
| `POST /api/device/pair` | `POST /api/v1/pair` | no |
| `POST /api/device/pair/revoke` | `POST /api/v1/pair/revoke` | yes |
| `GET /api/device/dashboard` | `GET /api/v1/dashboard` | yes |
| `PUT /api/device/dashboard` | `PUT /api/v1/dashboard` | yes |
| `POST /api/device/wifi/configure` | `POST /api/v1/wifi/configure` | yes |
| `POST /api/device/control/brightness` | `POST /api/v1/control/brightness` | yes |
| `POST /api/device/control/name` | `POST /api/v1/control/name` | yes |
| `GET /api/device/system` | `GET /api/v1/system` | yes |
| `POST /api/device/system/prepare-kiosk` | `POST /api/v1/system/prepare-kiosk` | yes |
| `POST /api/device/system/home` | `POST /api/v1/system/home` | yes |
| `GET /api/device/media/status` | `GET /api/v1/media/status` | yes |
| `POST /api/device/media/play` | `POST /api/v1/media/play` | yes |
| `POST /api/device/media/pause` | `POST /api/v1/media/pause` | yes |
| `POST /api/device/media/resume` | `POST /api/v1/media/resume` | yes |
| `POST /api/device/media/stop` | `POST /api/v1/media/stop` | yes |
| `POST /api/device/media/seek` | `POST /api/v1/media/seek` | yes |
| `POST /api/device/media/volume` | `POST /api/v1/media/volume` | yes |

"Device auth" above is the bearer token the companion attaches to the
device call automatically once paired; see "Access control" below for the
separate, companion-side `X-Companion-Token` gate that protects these
routes from other machines on the LAN.

Companion-only endpoints:

| Route | Purpose |
|---|---|
| `GET /api/companion/devices` | List connected ADB devices with profile validation results |
| `POST /api/companion/devices/connect` | Set up (or reuse) an `adb forward` and mark it active |
| `POST /api/companion/devices/disconnect` | Remove the active `adb forward` (or clear LAN state) |
| `POST /api/companion/devices/lan/connect` | Connect over LAN using a validated RFC1918/link-local IPv4 host + an existing pairing token |
| `GET /api/companion/devices/connection` | Current connection state |
| `GET /api/companion/media` | List uploaded media files |
| `PUT /api/companion/media/:name` | Upload a file (raw body) |
| `DELETE /api/companion/media/:name` | Delete a file |
| `POST /api/companion/media/:name/play` | Ask the connected device to play a hosted file (builds a USB or LAN playback URL server-side) |
| `GET /media-files/*` | Range-capable file serving for playback |

## Access control

The companion binds to `127.0.0.1` by default (`COMPANION_HOST`), which
needs no additional authentication: only processes on the same machine can
reach it.

Setting `COMPANION_HOST` to anything else (e.g. `0.0.0.0`, or a specific LAN
IP) exposes the companion to other devices on the network. In that case a
`COMPANION_ACCESS_TOKEN` **must** be configured, or the companion refuses to
start (fail-fast at startup, not a silent open API). Once configured, every
request to `/api/device/*`, `/api/companion/*`, and `/media-files/*` must
include a matching `X-Companion-Token: <token>` header; requests without it,
or with the wrong value, get `401 Unauthorized`. The token is never accepted
via query string, so it can't leak into access logs, browser history, or
`Referer` headers. The static UI itself (`/`, `/index.html`, etc.) is not
gated, so the page always loads; it's the API calls it makes that require
the token.

The web UI has a "Companion access" card where you paste the token; it is
kept only in that browser tab's `sessionStorage` (cleared when the tab
closes) and attached automatically to every companion API call. Media
filenames are displayed as text rather than unauthenticated direct links;
the Play, Stop, and Delete actions all use authenticated API requests.

Generate a token with, for example:

```powershell
node -e "console.log(require('crypto').randomBytes(24).toString('hex'))"
```

## Local data

By default the companion stores its pairing token/preferences and media
library under `companion/.data/`, which is gitignored. Override the
location with `COMPANION_DATA_DIR` / `COMPANION_MEDIA_ROOT` in `.env`.

## Security notes

- Wi-Fi passphrases are accepted from the browser, forwarded once to the
  device, and never written to disk, logged, or echoed back by the
  companion.
- The pairing bearer token is attached to outgoing device requests as an
  `Authorization: Bearer <token>` header only; it is never placed in a URL,
  and request logging only records the HTTP method and path.
- The companion's own `X-Companion-Token` access-token check (see "Access
  control" above) uses a SHA-256-hashed, constant-time comparison and is
  only enforced once `COMPANION_HOST` is non-loopback; it is independent of,
  and layered on top of, the device's own pairing bearer token.
- All `adb` invocations use `execFile` with argument arrays; no shell is
  ever invoked, so arguments cannot be reinterpreted by a shell on any
  platform.
- Media file access is confined to the configured media root through
  explicit segment-stack path resolution (see `src/server/media/path-safety.ts`).
  That module never calls `decodeURIComponent` itself -- Express already
  decodes route params exactly once before handlers run, so re-decoding
  would both double-decode malicious input and break legitimate filenames
  containing a literal `%` -- and resolution is independent of host OS path
  semantics, so both Windows and POSIX hosts reject the same set of
  traversal attempts.
- The media router never has a JSON (or other) body-parsing middleware
  applied ahead of it, so upload request bodies are always streamed to disk
  byte-for-byte regardless of the `Content-Type` header sent; only
  `multipart/form-data` is explicitly rejected (`415`), since it isn't
  supported yet.

## LAN connection mode

`POST /api/companion/devices/lan/connect` (body `{ipAddress, token}`)
connects to the device directly over the network instead of through an `adb
forward`, using a pairing token the browser already holds (for example, one
issued through the Mirror's on-glass QR + pairing-code flow). The host is
validated as an RFC1918 (`10/8`, `172.16/12`, `192.168/16`) or link-local
(`169.254/16`) IPv4 literal -- hostnames, IPv6, and loopback addresses are all
rejected, since the value is caller-supplied and accepting anything else would
be an SSRF vector (e.g. an address pointing at `127.0.0.1` or an internal
service). No DNS resolution is ever performed. Only the pairing token is
persisted (via the same `ConfigStore` used for USB pairing); the Wi-Fi
passphrase never reaches this endpoint.

## Device media playback and hosted-file media URLs

The "Device media playback" card proxies the device's authenticated media
control API (`GET /api/v1/media/status`, `POST /api/v1/media/{play,pause,
resume,stop,seek,volume}`) through `/api/device/media/*`. Each hosted media
file also gets "Play" / "Stop" buttons.

Pressing "Play" calls `POST /api/companion/media/:name/play`, which builds a
playback URL the device can actually fetch and forwards it to `POST
/api/v1/media/play`:

- **USB**: the companion lazily runs `adb reverse tcp:<port> tcp:<companionPort>`
  the first time a file is played during a connection (reused afterwards).
  It tries `MIRROR_MEDIA_REVERSE_PORT` (default `14317`) and, if that's
  already bound, the next nine ports in sequence -- the on-device adb build
  does not report back a device-side port for a dynamically allocated
  `adb reverse tcp:0 ...`, so a fixed preferred port with a small fallback
  range is used instead. The device then fetches
  `http://127.0.0.1:<device-port>/media-files/<name>` from its own loopback
  interface, tunnelled back to this companion.
- **LAN**: the URL uses `COMPANION_PUBLIC_URL` if set, otherwise the
  companion auto-selects one of its own private (RFC1918/link-local) IPv4
  interface addresses via `os.networkInterfaces()`. **This never trusts a
  browser-supplied `Host` header** -- that header is fully attacker
  controlled and would otherwise let a malicious page redirect the device's
  media fetch to an arbitrary server.

When the companion's `/media-files` route is protected (`COMPANION_ACCESS_TOKEN`
configured, i.e. a non-loopback `COMPANION_HOST`), a LAN-mode play request
also tells the device to send that same token back: the companion passes
`headers: {'X-Companion-Token': '<token>'}` inside the JSON body of `POST
/api/v1/media/play`, and the device is expected to attach those headers to
its own outbound fetch of the media URL. The token is never appended to the
media URL itself and is never logged. USB playback always resolves to a
`127.0.0.1` URL reached through the `adb reverse` tunnel, which mirrors a
loopback-style origin from the device's perspective, so no token is
attached there.
