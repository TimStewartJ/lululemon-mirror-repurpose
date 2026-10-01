# Streaming

For installation and browser pairing, start with
[Getting started](getting-started.md). This page covers playback on an already
working appliance.

## Recommended path

Mirror Home is an FCast v3 receiver on TCP port `46899`. Compatible FCast
senders can discover it through `_fcast._tcp` and send normal media URLs.

The current receiver does not claim FCast v4 screen-mirroring capability.
Version 4 requires TLS 1.3, FlatBuffers, certificate pinning, and a WebRTC
answerer that are not present in the Android 6 appliance. See
[Casting roadmap](casting-roadmap.md).

To play a file from a computer, serve it over HTTP on the LAN (for example
`python -m http.server` in its folder) and submit that URL through **Home** in
the Mirror-hosted controls or `POST /api/v1/media/play`.

## Player

The on-device player is Media3 1.9.0, pinned because API 23 is this hardware's
platform floor. Supported sources include:

- progressive HTTP(S) media
- HLS
- DASH
- RTSP

Playback is full-screen and automatically returns to the configured dashboard
when stopped or ended.

## Encoding recommendations

The APQ8016 hardware is most reliable with:

```text
Video: H.264/AVC Main or High, up to 1080p30
Audio: AAC-LC
Container: fast-start MP4, fragmented MP4, or HLS
```

Avoid AV1. Treat VP9 and high-resolution HEVC as requiring transcoding before
playback. For progressive MP4, place `moov` before `mdat` (`ffmpeg
-movflags +faststart`).

For an interim desktop-screen stream, publish H.264 as RTSP or low-latency HLS
from OBS/FFmpeg and submit that URL through **Home** in the Mirror-hosted
controls.

## Why not Chromecast

Google Cast receiver support requires Google Play Services and the proprietary
Cast receiver ecosystem, neither of which is present on this firmware. FCast
provides an open sender ecosystem and a simple documented protocol without
pretending to be a certified Chromecast.

AirPlay receiver projects currently target newer Android releases and carry
additional protocol, licensing, and DRM constraints. Miracast sink behavior is
not exposed as a stable Android application API on this build.
