# Streaming

## Recommended path

Mirror Home is an FCast v3 receiver on TCP port `46899`. Compatible FCast
senders can discover it through `_fcast._tcp` and send normal media URLs.

The companion can also host a local file and call Mirror Home's authenticated
media API directly. This works over USB during setup and over Wi-Fi once the
Mirror has joined the LAN.

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

Avoid AV1. Treat VP9 and high-resolution HEVC as requiring companion-side
transcoding. For progressive MP4, place `moov` before `mdat` (`ffmpeg
-movflags +faststart`).

## Why not Chromecast

Google Cast receiver support requires Google Play Services and the proprietary
Cast receiver ecosystem, neither of which is present on this firmware. FCast
provides an open sender ecosystem and a simple documented protocol without
pretending to be a certified Chromecast.

AirPlay receiver projects currently target newer Android releases and carry
additional protocol, licensing, and DRM constraints. Miracast sink behavior is
not exposed as a stable Android application API on this build.
