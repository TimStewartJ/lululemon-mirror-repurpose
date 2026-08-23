# Control protocol

Mirror Home exposes a versioned JSON API over HTTP.

## Pairing

1. Mirror Home displays a short-lived numeric pairing code.
2. A browser or companion submits the code over Wi-Fi Direct, USB, LAN, or BLE.
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
GET  /api/v1/preferences
PUT  /api/v1/preferences
GET  /api/v1/automation
PUT  /api/v1/automation
POST /api/v1/automation/sleep
POST /api/v1/automation/wake
GET  /api/v1/photos
PUT  /api/v1/photos/{name}
DELETE /api/v1/photos/{name}
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
