# Control protocol

Mirror Home exposes a versioned JSON API over HTTP and WebSocket.

## Pairing

1. Mirror Home displays a short-lived numeric pairing code.
2. The companion submits the code over USB, LAN, or BLE.
3. Mirror Home returns a random bearer token.
4. The token is stored locally by the companion and can be revoked on-device.

Pairing codes expire, are single-use, and are rate-limited.

## Planned API surface

```text
GET  /api/v1/status
POST /api/v1/pair
POST /api/v1/wifi/configure
GET  /api/v1/dashboard
PUT  /api/v1/dashboard
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
GET  /api/v1/events          (WebSocket upgrade)
```

All endpoints except status and pairing require authentication.

Wi-Fi passphrases are accepted only through authenticated local channels and
are passed directly to Android's Wi-Fi configuration API.

The device API does not emit permissive CORS headers. Browser clients use the
companion's same-origin proxy, preventing unrelated websites from scripting the
pairing endpoint through a visitor's browser.

## FCast

Mirror Home implements FCast protocol v3 on TCP port `46899`:

- Version and Initial handshakes
- Play, Pause, Resume, Stop, Seek, SetVolume, and SetSpeed
- PlaybackUpdate, VolumeUpdate, PlaybackError, Ping, and Pong
- `_fcast._tcp` DNS-SD advertisement

Packets larger than 32 KB and more than eight simultaneous clients are rejected.
Idle clients expire, malformed commands receive a playback error without
tearing down an otherwise healthy connection.
