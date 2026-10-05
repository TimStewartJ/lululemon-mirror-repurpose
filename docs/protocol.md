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
GET  /api/v1/wifi/scan-guard
PUT  /api/v1/wifi/scan-guard
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
GET  /api/v1/board/guide
GET  /api/v1/board
GET  /api/v1/board/items
POST /api/v1/board/items
GET  /api/v1/board/items/{id}
PUT  /api/v1/board/items/{id}
PATCH /api/v1/board/items/{id}
DELETE /api/v1/board/items/{id}
DELETE /api/v1/board/items?...
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
GET  /api/v1/voice
PUT  /api/v1/voice
PUT  /api/v1/voice/model
DELETE /api/v1/voice/model
GET  /api/v1/assistant
PUT  /api/v1/assistant
POST /api/v1/assistant/ask
POST /api/v1/assistant/say
GET  /api/v1/screenshot[?width=180..1080]
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

Only bootstrap, pairing, the board's guide, static controls, and loopback-only
dashboard resources are public. Full status, health, and all state changes require authentication
for LAN clients. ADB-forwarded and on-device loopback status and health remain
available for recovery and local templates.

`status.address` is the address other devices on the household network use to
reach the Mirror: its Wi-Fi address, or a wired one when there is no Wi-Fi,
and `null` when it has neither. The Wi-Fi Direct setup network does not count.

A request that fails unexpectedly answers 500 with a generic error rather
than dropping the connection, and is counted in the health report.

`PUT /api/v1/wifi/scan-guard` takes `{enabled}` and decides whether Android
may scan for other Wi-Fi networks while it is connected to one; see
[Staying up for weeks](user-guide.md#staying-up-for-weeks). It answers, as
`GET` does, with `enabled`, `supported` (only Android 6 has the switch),
`state` (`off`, `applied`, `waiting` while Wi-Fi has no network and the
switch is handed back to Android, or `error` with the reason in `detail`),
`scanningWhileConnected` as Android reports it (`null` if it cannot be
asked), `appliedAt`, `applied` (how often Mirror Home had to set the switch
since it started; Android forgets it when it restarts), `checks`,
`checkedAt`, and `scans`: how many scans Android has finished while connected
since Mirror Home started (`whileConnected`), how many of them from a minute
after the guard was applied (`sinceApplied`, which stays 0 where the guard
works), and when the last one was (`lastAt`).
Turning it on where `supported` is false answers 409. `status.wifi.scanGuard`
carries `enabled`, `supported`, `state` and `detail`.

`GET /api/v1/weather/locations?q=` looks a place up for the weather and
answers `{results: [{label, latitude, longitude, timezone}]}` with up to five
places, best known first. The service behind it searches by a town's name
alone, so `q` is tried as it stands and then with its last one to three words
as a state, province or country, written out or abbreviated ("Portland
Maine", "Portland, ME", "San Jose Costa Rica"); with a comma, what follows it
is the region. When towns of that name exist but none lies in the region,
`results` is empty and `elsewhere` lists them in the same form, so that a
client can say which there are.

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

### Board

The board holds what programs post for people to see: notes, to-dos and
reminders, each with a kind, a title, and optionally a body, a due time, a
priority and an expiry. [The board](board.md) is its guide; the Mirror serves
the same as JSON at `GET /api/v1/board/guide`, the one board route that needs
no credential. `GET /api/v1/board` answers what the glass lists now and is
readable from loopback so the glass can fetch it. Every other route requires
authentication.

An item without an expiry is removed 24 hours after it was last written, or
after it is due if that is later. Times are epoch milliseconds in answers; a
request may also give ISO 8601 with a UTC offset. A refused request answers
`{error, field, guide}`. `status` and `dashboard/runtime` carry
`boardVersion`, which changes with every mutation and when an item expires.
`PATCH` is used here and nowhere else in the API. The board reads a request
body as UTF-8 JSON whatever its `Content-Type`.

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

### Voice

Spoken commands are recognised on the Mirror; see [Voice commands](voice.md).
`GET /api/v1/voice` reports where that stands. Times are epoch milliseconds.

| Field | Contents |
|---|---|
| `enabled` | The switch. It is off until an owner turns it on. |
| `state`, `detail` | `off`, `no-model` (switched on, but no speech model is installed), `no-permission` (Android has not given Mirror Home the microphone), `paused` (the recogniser has stopped while Android installs an app), `starting`, `loading`, `listening` or `error`; and a sentence about it for the controls. |
| `wakeWord`, `commands` | What can be said: each command's `id`, the `caption` the glass shows when it is carried out, and every sentence in `say`. |
| `model`, `maxModelBytes` | `null`, or the installed model's `name`, unpacked `bytes`, number of `files`, the `sha256` of the archive it came in and `installedAt`; and the largest archive that is accepted. |
| `permissionGranted` | Whether Mirror Home may use the microphone. |
| `process` | The recogniser's `pid` and `pssKb`, measured at most every half minute, and `restarts`: how often it stopped by itself since Mirror Home started. |
| `recogniser` | `modelLoadMs`; `cpuShare`, the share of one core it used over the last five seconds; `behindMs`, how late sound reached it at worst in that time; and `listenedSeconds`. |
| `microphone` | `levelDb` and `peakDb` of the last five seconds, in dB below full scale, and `silent` when every sample was zero. |
| `counts` | Since Mirror Home started: `sentences` (stretches of speech the recogniser ended), `wakeWords` (the name alone), `commands`, `notUnderstood`, `unsure` (commands the recogniser had doubts about, which are not carried out), and `asked` (requests passed to the [assistant](#assistant)). |
| `lastCommand`, `recent` | The last command's `id`, its time `at` and what the glass has `shown`; and up to twenty sentences that were addressed to the Mirror, each with `heard` (in which every word that is not a command's word reads `[unk]`), `confidence`, `outcome` (`command`, `wake`, `not-understood` or `unsure`), `command` and, for a command, what the glass has `shown`. Speech that was not addressed to it is counted and not kept. |
| `testHooks` | True in a debug build only; see below. |

`PUT /api/v1/voice` takes `{"enabled": true}` or `false` and answers with the
same report. `status` carries `voice` with `enabled`, `state` and `detail`.

`PUT /api/v1/voice/model` installs a speech model in place of the one
before. The body is a zip archive of a Vosk model with
`Content-Type: application/zip`, at most 96 MB, and optionally its SHA-256
in `X-Content-SHA256`. The archive is unpacked below Mirror Home's own
storage, to at most 256 MB and 64 files, and must hold `am/final.mdl`,
`conf/mfcc.conf`, `conf/model.conf`, `graph/HCLr.fst` and `graph/Gr.fst`,
in one folder or none. The answer is `201` with the report, `400` with the
reason when the upload is not such an archive or does not match its
checksum, `409` when storage is short, and `415` for another content type.
`DELETE /api/v1/voice/model` removes the model.

A debug build has two more routes, for the
[emulator suite](validation.md#how-the-suite-speaks): `POST
/api/v1/voice/test/clip` takes a 16 kHz mono WAV file that the recogniser
hears in place of the microphone, and `POST /api/v1/voice/test/sentence`
takes `{text, confidence}` as if the recogniser had heard it. Both answer
`202`, or `409` unless voice is listening. A release build answers `404`.

### Assistant

What is said to the Mirror beyond its own commands goes to a companion on
the home network; see [The assistant](assistant.md), which also describes
what the Mirror asks of a companion. `GET /api/v1/assistant` reports where
that stands. Times are epoch milliseconds.

| Field | Contents |
|---|---|
| `enabled` | The switch. It is off until an owner turns it on. |
| `address`, `keySet` | Where the companion is, as `http://host:port`; and whether the Mirror has a key to send it. The key itself is never reported. |
| `state`, `detail` | `off`, `unconfigured` (switched on, but the address or the key is missing), `connecting`, `connected`, `trouble` (the companion answers and reports that a part of it is not ready) or `unreachable` (no answer, a late one, or a key that is not accepted); and a sentence about it for the controls. |
| `model` | What the companion says answers its requests. |
| `mascot`, `mascots` | The character that the Mirror answers as, by its id, or `none`; and the characters there are, each with `id` and `name`. |
| `place`, `places` | Where on the glass the answers stand, as `{height, side}`; and the `heights` there are, from the top down (`top`, `upper`, `middle`, `lower`, `bottom`), and the `sides` (`left`, `center`, `right`). A Mirror starts with `bottom` and `center`. |
| `busy`, `lastAnswerAt` | Whether a request is waiting for its answer, and when the companion last answered anything. |
| `counts` | Since Mirror Home started: `requests`, of which `ignored` (the companion took them for talk) and `failures` (no answer). |
| `recent` | Up to twelve requests: the time `at`, the `source` (`voice`, `controls`, or `shortcut` for a greeting that the Mirror recognised itself), what the companion `heard`, its `reply` and how many `rows` it had under it, whether it was `ignored`, what the companion `did`, how many `millis` the answer took, and the `error` if there was none. |

`PUT /api/v1/assistant` takes any of `enabled`, `address`, `key`, `mascot`
and `place`, and answers with the same report. A `mascot` that is not `none`
or one of `mascots` is refused with `400`; a newly chosen one shows its name
on the glass for a few seconds. `place` is an object with `height`, `side`
or both; what is left out stays, a height or side that there is none of is
refused with `400`, and the glass shows a line at a new place. A request
that is refused changes nothing, also where another part of it was in
order. An address may be given without `http://`; it
ends after its port. A key is up to 256 characters without spaces. An empty
address or key removes it. `400` says what is wrong with either. `status`
carries `assistant` with `enabled` and `state`.

`POST /api/v1/assistant/ask` takes `{"text": "..."}`, 1 to 500 characters,
passes it to the companion as a typed request, shows the answer on the
glass, and answers with what the companion answered. It waits for that, for
up to 55 seconds. `503` with the reason means that there was no answer, or
nobody to ask.

`POST /api/v1/assistant/say` and `GET /api/v1/screenshot` are for the
companion: a line on the glass, with rows under it if it has several parts,
and a picture of the glass. They are described in
[The assistant](assistant.md#the-companion-asks-the-mirror).

### Health

`GET /api/v1/health` reports how Mirror Home itself is doing, for a person or
a monitor that cannot see the glass. Times are epoch milliseconds.

| Section | Contents |
|---|---|
| `process` | `pid`, `runId` (counts starts since Mirror Home's data was created), `startedAt`, `uptimeSeconds`, and `previousRun`: when the last run started and was last known alive, its version, and how it ended: `update`, `reboot`, `crash` or `killed`. `earlyStops` counts processes that Android stopped within ten seconds of starting before this run began. They are not reported as the previous run. One is normal after an update, because Android 6 starts a HOME app while it is still replacing it; several mean Home could not stay up. |
| `crashes` | `count` and `last`: the exception, message, thread, time, version and the first lines of the stack trace of the most recent uncaught exception. |
| `memory` | The app's `pssKb`, Java and native heap, `threads`, `openFiles` (`null` if it cannot be counted) and `oomScoreAdj`, how readily the kernel would end Mirror Home (0 while it is on the display); Android's `systemAvailableKb`, `systemTotalKb` and `systemLow`; and how often Android asked the app to trim memory. `kernel` holds what the kernel itself says, in kilobytes: `freeKb`, `cachedKb`, `swapTotalKb` and `swapFreeKb` (each `null` if it says nothing). Android's figure counts memory that the kernel cannot use for everything, so a Mirror can be short of memory while `systemLow` is false; little `cachedKb` together with a nearly used-up swap is the sign. `largest` lists the five processes that hold most, in memory (`rssKb`) and in swap (`swapKb`) together, by `name`, among those Android lets an app see: on a Mirror those are apps, and not the factory software's daemons. |
| `storage` | `dataFreeBytes` and `dataTotalBytes` of the app's storage volume. |
| `device` | Boot time, `bootId` (different for every boot, or `null`), Android release and SDK, model, build fingerprint, `display` (pixel size, density, refresh rate, and whether the panel is `on`), `input` (whether Android sees a touchscreen, keyboard or navigation keys; stock Android 6 shows crash dialogs only if it sees one), `power` and the `webView` package and version. `power` is what decides whether Android turns the display off: whether it is `interactive` (awake), `keyguardLocked`, `screenOffTimeoutSeconds`, the `stayOnWhilePluggedIn` setting, how Android believes it is `plugged` (0 is not at all, which is what a Mirror reports), and whether its USB port is `usbConnected` and `usbConfigured` by a computer. |
| `activity` | Whether the dashboard is `created`, `resumed`, `focused` and `visible`; `showing` is true only when nothing is drawn over it. `pausedForSeconds` and `unfocusedForSeconds` say for how long it has not been, `pauses` and `stops` count how often it was covered and wholly hidden, and `sleeping` says whether Mirror Home has darkened the display. `selectedHome` says whether Mirror Home is the HOME app Android would start. `front` lists the screens Android will name to an ordinary app, front first: Mirror Home's own and other HOME apps'. `recovery` is what Mirror Home did to stay in front (see [Architecture](architecture.md#staying-in-front)): `attended`, `wakeUps`, `relaunches`, the time, reason (`asleep` or `covered`) and `front` of the last one, and `otherHomeEnds`, how often it asked Android to end the idle processes of other HOME apps. |
| `dashboard` | The dashboard page's load state and last failure, plus `consoleErrors`, `consoleWarnings` and `recentConsoleErrors` from its scripts. |
| `api` | `unhandledErrors` and the last one's method, path and exception. |
| `wifi` | `connected`, and when it is: `rssi`, `signalLevel` (0 to 4), `linkSpeedMbps` and `frequencyMhz`. `scanGuard` is the whole answer of `GET /api/v1/wifi/scan-guard`. |
| `clock` | `timeZone`, `utcOffsetMinutes`, `source`, `knownChanges`, `nextChange` and the IANA release of the bundled table. |
| `pairing` | `open`, `lockedForSeconds`, `wrongCodes` and the number of paired `clients`. |
| `otaSupervisor` | `installed`, and when it is: its version, whether it is `listening` on its port, and since when it has not been. Mirror Home looks every five minutes and whenever this report is read. `hold` says whether Mirror Home keeps the supervisor from being ended when memory is short: `state` is `held`, `waiting` (Android is starting it), `unsupported` (older than 1.3.0) or `refused` (signed with another key), with `since`, how often Mirror Home took hold (`binds`) and lost it (`losses`), and while held the supervisor's `pid` and its `oomScoreAdj` as the supervisor itself reads it: 58 when held, 294 or more when not. See [LAN OTA updates](ota-updates.md#kept-running-by-mirror-home). |
| `restart` | `advised`, and the `reason` as a sentence when it is. A restart is advised when an installed OTA supervisor has not answered for half an hour, or when three quarters of the swap are in use on a Mirror that has been up for three days or more. Once advised it stays so until Mirror Home starts again. `status` carries the same object. |
| `voice` | The whole [voice report](#voice). |
| `assistant` | The [assistant's report](#assistant) without `recent`: what people asked is not part of a health report. |

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
GET  /api/v1/permissions           HMAC; Home's allowlisted runtime permissions
POST /api/v1/permissions           HMAC + verified JSON body + explicit confirmation
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
From supervisor 1.3.0, `status` carries `heldByHome`.

From supervisor 1.2.0, a permission change accepts only this shape:

```json
{
  "packageName": "dev.mirror.repurpose",
  "permission": "android.permission.RECORD_AUDIO",
  "granted": true,
  "confirm": "CHANGE_RUNTIME_PERMISSION"
}
```

Unknown fields are rejected. The permission must be `RECORD_AUDIO`,
`CAMERA`, `ACCESS_FINE_LOCATION` or `ACCESS_COARSE_LOCATION`, with the
`android.permission.` prefix, and declared by the installed same-certificate
Home app. `granted: false` explicitly denies it. The JSON body is limited to
1024 bytes; its actual SHA-256 must match the authenticated hash. No global
permission policy is changed.
