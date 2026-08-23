# Architecture

## Components

```text
Browser companion
  | USB: adb forward
  | LAN: authenticated HTTP/WebSocket
  | BLE: provisioning GATT service
  v
Mirror Home (ordinary APK, default HOME)
  | dashboard renderer
  | Wi-Fi provisioning
  | media player
  | pairing/authentication
  | MIRROR Binder bridge
  v
Optional System Helper (UID 1000)
  | allowlisted privileged operations only
  v
Android framework / MIRROR hardware
```

## Mirror Home

Mirror Home owns all user-facing behavior:

- full-screen portrait dashboard
- clock, status, and configurable web dashboard
- pairing code and connection status
- embedded HTTP/WebSocket control plane
- BLE provisioning service
- Wi-Fi network enrollment
- media playback overlay and queue
- return-to-dashboard behavior
- boot/home lifecycle and screen wake management

It requests only normal or user-grantable permissions. The stock
`com.mirror.services` Binder is used where available for brightness, backlight,
name, and setup-state controls.

## Companion

The companion is a local Node.js service and static browser UI:

- discovers ADB devices and validates the exact profile
- forwards a localhost port to Mirror Home over USB
- opens the same UI over LAN after Wi-Fi provisioning
- uses Web Bluetooth for initial provisioning where supported
- hosts local media files with range requests
- sends playback, dashboard, schedule, and automation commands

The companion does not persist Wi-Fi passphrases by default.

## Media path

The primary media path is HTTP(S) or RTSP playback through a native Android
player. The companion can expose a selected local file through a range-capable
HTTP endpoint and instruct the Mirror to play its LAN URL.

Chromecast receiver compatibility is not assumed: the proprietary Cast receiver
stack depends on Google services unavailable on this firmware. DLNA discovery
and browser convenience integrations can be layered over the same playback API.

## Privilege boundary

The optional helper is not part of the media data path. Mirror Home calls a
small authenticated local interface for operations that ordinary Android APIs
cannot perform. Removing the helper returns the device to the stock system APK.
