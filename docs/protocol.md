# Control protocol

Mirror Home exposes a versioned JSON API over HTTP.

## Pairing

1. Mirror Home displays a short-lived numeric pairing code.
2. A browser or companion submits the code over Wi-Fi Direct, USB, or LAN.
3. Mirror Home returns a random per-client bearer token.
4. Only its SHA-256 hash is persisted on the Mirror.
5. Individual clients can be listed and revoked.

Pairing codes expire, are single-use, and are rate-limited.

## API surface

```text
GET  /api/v1/bootstrap
GET  /api/v1/status
POST /api/v1/pair
POST /api/v1/pair/revoke
GET  /api/v1/clients
POST /api/v1/clients/revoke
POST /api/v1/wifi/configure
GET  /api/v1/dashboard
PUT  /api/v1/dashboard
GET  /api/v1/dashboard/layout
PUT  /api/v1/dashboard/layout
POST /api/v1/dashboard/layout/reset
POST /api/v1/dashboard/layout/validate
GET  /api/v1/notes
POST /api/v1/notes
PUT  /api/v1/notes/{id}
DELETE /api/v1/notes/{id}
GET  /api/v1/preferences
PUT  /api/v1/preferences
GET  /api/v1/weather
PUT  /api/v1/weather
POST /api/v1/weather/refresh
GET  /api/v1/weather/locations?q=...
GET  /api/v1/automation
PUT  /api/v1/automation
POST /api/v1/automation/sleep
POST /api/v1/automation/wake
GET  /api/v1/photos
GET  /api/v1/photos/{name}
GET  /api/v1/photos/{name}/thumbnail
GET  /api/v1/photos/{name}/display
PUT  /api/v1/photos/{name}
DELETE /api/v1/photos/{name}
GET  /api/v1/background-videos/bootstrap
POST /api/v1/background-videos/bootstrap
POST /api/v1/background-videos/bootstrap/confirm
GET  /api/v1/background-videos
PUT  /api/v1/background-videos/upload/{name}
GET  /api/v1/background-videos/{id}/poster
POST /api/v1/background-videos/{id}/activate
POST /api/v1/background-videos/rollback
PUT  /api/v1/background-videos/schedule
POST /api/v1/background-videos/schedule/resume
DELETE /api/v1/background-videos/{id}
GET  /api/v1/dashboard/ambient-video
POST /api/v1/control/brightness
POST /api/v1/control/name
POST /api/v1/media/play
POST /api/v1/media/pause
POST /api/v1/media/resume
POST /api/v1/media/seek
POST /api/v1/media/volume
POST /api/v1/media/stop
GET  /api/v1/media/status
GET  /api/v1/system
POST /api/v1/system/prepare-kiosk
POST /api/v1/system/home
```

Only bootstrap, pairing, static controls, and loopback-only dashboard resources
are public. Full status and all state changes require authentication for LAN
clients. ADB-forwarded and on-device loopback status remains available for
recovery and local templates.

Background video list, upload, activation, rollback, schedule, deletion, and poster routes
require an ordinary bearer credential. The bootstrap availability route is
public; its POST exchanges an independently generated build-scoped capability
for one normal revocable client credential. Repeating the exchange returns the
same pending credential until the authenticated confirmation succeeds, so a
lost response cannot strand the bootstrap. Uploaded bytes are content-addressed
by SHA-256 and validated on the Mirror before they become selectable. See
[Background videos](background-videos.md).

Notes are content, not layout. `GET /api/v1/notes` returns
`{notes: [{id, text, createdAt, updatedAt}], version, maxLength, maxNotes}`
newest first and is readable from loopback so the glass can fetch it; the
mutating routes require authentication and take `{text}`. `status` and
`dashboard/runtime` carry `notesVersion`, a counter that changes with every
mutation, so clients re-fetch notes only when something changed.

Wi-Fi passphrases are accepted only through authenticated local channels and
are passed directly to Android's Wi-Fi configuration API.

The device API does not emit permissive CORS headers. Normal browser clients
load the control application from the Mirror itself, keeping API calls
same-origin. The companion remains a same-origin proxy for its optional UI.

## FCast

Mirror Home implements FCast protocol v3 on TCP port `46899`:

- Version and Initial handshakes
- Play, Pause, Resume, Stop, Seek, SetVolume, and SetSpeed
- PlaybackUpdate, VolumeUpdate, PlaybackError, Ping, and Pong
- `_fcast._tcp` DNS-SD advertisement

Packets larger than 32 KB and more than eight simultaneous clients are rejected.
Idle clients expire, malformed commands receive a playback error without
tearing down an otherwise healthy connection.

## OTA supervisor

The separate device-owner supervisor listens on TCP `8791`:

```text
GET  /api/v1/bootstrap             loopback only
POST /api/v1/provision             loopback + bootstrap secret
POST /api/v1/provision/confirm     loopback + HMAC
POST /api/v1/provision/recover     loopback + bootstrap secret
GET  /api/v1/status                HMAC
PUT  /api/v1/update                HMAC + signed APK body
POST /api/v1/rollback              HMAC
POST /api/v1/deprovision           loopback + HMAC + explicit confirmation
```

The HMAC canonical value is five newline-delimited fields:

```text
METHOD
/path
monotonic-counter
random-nonce
lowercase-body-sha256
```

See [LAN OTA updates](ota-updates.md) for lifecycle and recovery behavior.
