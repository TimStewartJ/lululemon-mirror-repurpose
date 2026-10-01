# Control protocol

Mirror Home exposes a versioned JSON API over HTTP.

## Pairing

1. Mirror Home shows a short-lived numeric pairing code: on its setup screen,
   in the Pairing code widget, or to a paired client that asks for one with
   `POST /api/v1/pair/window`.
2. A browser or script submits the code over Wi-Fi Direct, USB, or LAN.
3. Mirror Home returns a random per-client bearer token.
4. Only its SHA-256 hash is persisted on the Mirror.
5. Individual clients can be listed and revoked.

A code is accepted only while one is on display; otherwise `POST /api/v1/pair`
answers 403 with `reason: "closed"` without checking it, and `bootstrap`
reports `pairingOpen: false`. Codes expire after ten minutes and are
single-use. Five wrong codes answer 429 with `reason: "locked"`,
`retryAfterSeconds` and a `Retry-After` header for 30 seconds, doubling with
each further lockout up to an hour. A wrong code is 401 `wrong-code`, and a
full client list is 409 `full`. A correct code or a new pairing window clears
the lock.

## API surface

```text
GET  /api/v1/bootstrap
GET  /api/v1/status
GET  /api/v1/health
POST /api/v1/pair
POST /api/v1/pair/window
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
are public. Full status, health, and all state changes require authentication
for LAN clients. ADB-forwarded and on-device loopback status and health remain
available for recovery and local templates.

`status.address` is the address other devices on the household network use to
reach the Mirror: its Wi-Fi address, or a wired one when there is no Wi-Fi,
and `null` when it has neither. The Wi-Fi Direct setup network does not count.

A request that fails unexpectedly answers 500 with a generic error rather
than dropping the connection, and is counted in the health report.

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

### Clock

The firmware has no usable time-zone rules, so local time is a UTC offset plus
a list of known changes. `PUT /api/v1/preferences` and `POST /api/v1/pair`
take `timeZone` (an IANA name), `utcOffsetMinutes` (the offset in force) and
optionally `utcOffsetChanges`: up to 64 `{at, utcOffsetMinutes}` entries in
time order, where `at` is the epoch millisecond a new offset starts. A request
with `utcOffsetChanges` is followed exactly, an empty list meaning no changes.
A request without it follows the table bundled with Mirror Home for that zone
when the offset is one the zone uses, and otherwise keeps the offset fixed.

`GET /api/v1/preferences` returns the offset in force, the next eight
`utcOffsetChanges`, and `clockSource`: `client`, `bundled` or `fixed`.
`status` and `dashboard/runtime` carry `utcOffsetMinutes` and
`nextUtcOffsetChange` (one entry or `null`) so a display can switch at the
exact instant. A schedule `hold` reports `untilTime`, its local end time.

### Health

`GET /api/v1/health` reports how Mirror Home itself is doing, for a person or
a monitor that cannot see the glass. Times are epoch milliseconds.

| Section | Contents |
|---|---|
| `process` | `pid`, `runId` (counts starts since Mirror Home's data was created), `startedAt`, `uptimeSeconds`, and `previousRun`: when the last run started and was last known alive, its version, and how it ended: `update`, `reboot`, `crash` or `killed`. `earlyStops` counts processes that Android stopped within ten seconds of starting before this run began. They are not reported as the previous run. One is normal after an update, because Android 6 starts a HOME app while it is still replacing it; several mean Home could not stay up. |
| `crashes` | `count` and `last`: the exception, message, thread, time, version and the first lines of the stack trace of the most recent uncaught exception. |
| `memory` | The app's `pssKb`, Java and native heap, `threads` and `openFiles` (`null` if it cannot be counted); Android's `systemAvailableKb`, `systemTotalKb` and `systemLow`; and how often Android asked the app to trim memory. |
| `storage` | `dataFreeBytes` and `dataTotalBytes` of the app's storage volume. |
| `device` | Boot time, `bootId` (different for every boot, or `null`), Android release and SDK, model, build fingerprint, `display` (pixel size, density, refresh rate), `input` (whether Android sees a touchscreen, keyboard or navigation keys; stock Android 6 shows crash dialogs only if it sees one) and the `webView` package and version. |
| `activity` | Whether the dashboard is `created`, `resumed` and `focused`; `showing` is true only when nothing is drawn over it. `pausedForSeconds` and `unfocusedForSeconds` say for how long it has not been, and `sleeping` whether the display is asleep. |
| `dashboard` | The dashboard page's load state and last failure, plus `consoleErrors`, `consoleWarnings` and `recentConsoleErrors` from its scripts. |
| `api` | `unhandledErrors` and the last one's method, path and exception. |
| `wifi` | `connected`, and when it is: `rssi`, `signalLevel` (0 to 4), `linkSpeedMbps` and `frequencyMhz`. |
| `clock` | `timeZone`, `utcOffsetMinutes`, `source`, `knownChanges`, `nextChange` and the IANA release of the bundled table. |
| `pairing` | `open`, `lockedForSeconds`, `wrongCodes` and the number of paired `clients`. |
| `otaSupervisor` | `installed`, and when it is: its version, whether it is `listening` on its port, and since when it has not been. |

`appVersion`, `versionCode`, `debuggable` and `now` complete the report. It
holds no credential, pairing code or Wi-Fi name; it does name the page the
glass is showing, including the address of a web dashboard.

The device API does not emit permissive CORS headers. Browser clients load the
control application from the Mirror itself, keeping API calls same-origin.

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
