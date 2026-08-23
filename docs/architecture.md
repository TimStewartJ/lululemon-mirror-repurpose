# Architecture

## Components

```text
Phone / desktop browser
  | QR + LAN/Wi-Fi Direct: same-origin authenticated HTTP
  | BLE: encrypted provisioning GATT service
  v
Mirror Home (ordinary APK, default HOME)
  | device-hosted responsive controls
  | dashboard renderer
  | Wi-Fi provisioning
  | media player
  | pairing/authentication
  | MIRROR Binder bridge
  v
Optional transient System Helper (UID 1000)
  | allowlisted privileged operations only
  v
Android framework / MIRROR hardware
```

## Mirror Home

Mirror Home owns all user-facing behavior:

- full-screen portrait dashboard
- clock, status, and configurable web dashboard
- pairing code and connection status
- embedded HTTP control plane
- BLE provisioning service
- Wi-Fi network enrollment
- media playback overlay and queue
- return-to-dashboard behavior
- boot/home lifecycle and screen wake management

It requests only normal or user-grantable permissions. The stock
`com.mirror.services` Binder is used where available for brightness, backlight,
name, and setup-state controls.

## Device-hosted controls

Mirror Home serves the control application and its API from the same origin on
port `8787`. No CORS access is enabled. A browser pairs with the code shown on
the physical display and stores its own revocable credential. Tokens are stored
hashed on the Mirror.

The app advertises `_http._tcp` and `_mirror-home._tcp` through Android NSD.
When no managed Wi-Fi exists, it creates a WPA2 Wi-Fi Direct group and displays
join/open QR codes.

## Optional companion

The companion is a local Node.js service and static browser UI:

- discovers ADB devices and validates the exact profile
- forwards a localhost port to Mirror Home over USB
- proxies browser requests server-side so the device API does not require CORS
- opens the same UI over LAN after Wi-Fi provisioning
- uses Web Bluetooth for initial provisioning where supported
- hosts local media files with range requests
- sends playback, dashboard, and automation commands

The companion does not persist Wi-Fi passphrases and is not required for daily
control.

BLE characteristics require an encrypted bond. The on-screen pairing code then
authorizes token issuance and Wi-Fi enrollment at the application layer.

## Media path

The primary media path is HTTP(S), HLS, DASH, or RTSP playback through pinned
Media3 1.9.0, whose minimum API is 23. The companion can expose a selected local
file through a range-capable HTTP endpoint and instruct the Mirror to play its
LAN URL.

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
