# Architecture

## Components

```text
Phone / desktop browser
  | QR + LAN/Wi-Fi Direct: same-origin authenticated HTTP
  v
Mirror Home (ordinary APK, default HOME)
  | device-hosted responsive controls
  | dashboard renderer
  | Wi-Fi provisioning
  | media player
  | content-addressed background-video library
  | pairing/authentication
  | MIRROR Binder bridge
  v
Optional transient System Helper (UID 1000)
  | allowlisted privileged operations only
  v
Android framework / MIRROR hardware

Trusted development computer
  | HMAC-authenticated signed APK upload
  v
OTA Supervisor (persistent device owner)
  | PackageInstaller + health check + known-good rollback
  v
Mirror Home
```

## Mirror Home

Mirror Home owns all user-facing behavior:

- full-screen portrait dashboard
- clock, status, and configurable web dashboard
- pairing code and connection status
- embedded HTTP control plane
- Wi-Fi network enrollment
- media playback overlay and queue
- return-to-dashboard behavior
- boot/home lifecycle and screen wake management

It requests only normal or user-grantable permissions. The stock
`com.mirror.services` Binder is used where available for brightness, backlight,
name, and setup-state controls.

### Staying in front

A Mirror has no touchscreen and no keys, so nobody can dismiss a screen that
Android puts in front of the dashboard, or turn the display on again once
Android has put it to sleep. Mirror Home therefore looks every five seconds
(and from its quarter-hourly watchdog alarm, which fires even when a sleeping
Android has stopped the processor) and puts the dashboard back:

- a dashboard that another screen has covered for ten seconds is brought to
  the front again, at most every thirty seconds;
- a display that Android has put to sleep is woken, at most every ten.

The first happens after an update. The factory launcher stays installed as a
second HOME app. While Android 6 replaces Mirror Home it needs a HOME app,
and at the wrong instant it starts that launcher, whose setup screen then
opens on top of the new dashboard and stays there.

Mirror Home does this only where nobody could do it by hand: it must be the
HOME app Android would start, the device must have no touchscreen, keyboard
or navigation keys, and no computer may be using its USB port. A phone that
runs Mirror Home, or a Mirror someone is working on through ADB or scrcpy, is
left alone. On a device without input devices the dashboard's window also
turns the display on and dismisses a lock screen when it opens. The health
report's `activity.recovery` counts what was done and why; see
[Control protocol](protocol.md#health).

## Device-hosted controls

Mirror Home serves the control application and its API from the same origin on
port `8787`. No CORS access is enabled. A browser pairs with the code shown on
the physical display and stores its own revocable credential. Tokens are stored
hashed on the Mirror.

The app advertises `_http._tcp` and `_mirror-home._tcp` through Android NSD.
When neither an app-managed SSID nor a current Wi-Fi connection exists, it
attempts a WPA2 Wi-Fi Direct group. The native setup screen displays join/open
QR codes once that group is ready. See [Getting started](getting-started.md)
for the installation sequence and onboarding limitations. Where there is no
Wi-Fi but a wired interface has an address, as on an emulator, the setup
screen and `status` give that address instead and no group is started.

## Health reporting

Mirror Home records what a restart would otherwise erase: when each process
started, how the one before it ended, and the last uncaught exception with
its stack trace. It also tracks whether its activity is really in front and
which HOME app is if not, what the dashboard page logs on its console, and
requests the API failed to handle. `GET /api/v1/health` assembles these with memory, storage, clock,
pairing and OTA-supervisor state; see [Control protocol](protocol.md#health).

The recorder lives in `android/common`, a source directory compiled into both
Mirror Home and the OTA supervisor, so each app keeps its own history without
a shared library module.

## Validation

`tools/validate.py` drives a debug build on an Android 6 emulator through the
control API, WebView DevTools and screen captures, and reads a live Mirror's
health without changing it. See [Validation](validation.md).

## USB tooling

`tools/mirrorctl.py` (wrapped by `tools\mirror.ps1`) is the only desktop
component. It verifies the exact device profile over ADB, backs up factory
APKs, installs and health-checks Mirror Home with rollback, manages the
transient helper, and forwards a localhost port to the Mirror-hosted controls
for setup and recovery. It keeps no credentials.

## OTA supervisor

The OTA supervisor is deliberately separate from Mirror Home. It exposes only a
signed-update protocol on TCP `8791`, has no shell capability, and accepts only
newer Home APKs matching its own signing certificate and the exact device
fingerprint. It remains alive across Home replacement and reboot, allowing it to
health-check the loopback Home API and perform an in-place rollback.

Requests use HMAC-SHA256 with monotonic replay counters. The active token and
independent bootstrap secret remain in ignored local files. See
[LAN OTA updates](ota-updates.md).

## Media path

The primary media path is HTTP(S), HLS, DASH, or RTSP playback through pinned
Media3 1.9.0, whose minimum API is 23. Any HTTP server the Mirror can reach,
including one on a computer on the same LAN, can supply a file to play.

Background videos take a separate ingest path into app-private,
content-addressed storage. Android validates each MP4 and hardware decoder before
atomic promotion. A single Media3 player switches serially between the ambient
file and presentation media, attaching to the appropriate native surface under
or over the transparent widget WebView. See
[Background videos](background-videos.md).

Chromecast receiver compatibility is not assumed: the proprietary Cast receiver
stack depends on Google services unavailable on this firmware. Mirror Home
instead implements the open FCast v3 protocol on TCP 46899 and advertises
`_fcast._tcp` through Android NSD. FCast v4/WebRTC mirroring is intentionally deferred to the owner-controlled OS
because it requires TLS 1.3, FlatBuffers, and a WebRTC answerer.

## Privilege boundary

The optional helper is not part of the media data path. During initial
provisioning, Mirror Home calls a small authenticated local interface for
operations that ordinary Android APIs cannot perform. Preferred-HOME state and,
optionally, kiosk settings persist after removing the helper, which returns the
device to the stock system APK before reboot.

The helper authenticates Binder callers by both the exact HOME package name and
the SHA-256 digest of the HOME signing certificate supplied at build time. It
does not expose a command shell or generic settings/file APIs.

The `android/system-helper` manifest exists only to compile and test the payload.
The installed package retains the stock APK manifest and signature. Tooling
therefore verifies the exact source-APK hash and its required shared UID,
Application, receiver, and exported service contract before wrapping.

The helper is intentionally not persistent: this Android 6 image's boot-time
`dex2oat` crashes while optimizing a Janus polyglot. Installation tooling treats
it as a live-provisioning transaction and requires rollback before reboot.
