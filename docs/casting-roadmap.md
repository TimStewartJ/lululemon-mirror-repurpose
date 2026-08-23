# Casting roadmap

## Current receiver

Mirror Home implements FCast v3 on TCP `46899`. Version 3 carries URL-based
media playback and control as length-prefixed JSON messages. It does not define
screen mirroring or transport encryption.

This receiver deliberately advertises `v=3`; claiming capabilities that are
not implemented would make current senders fail in less predictable ways.

## Why the sender disables screen mirroring

FCast screen mirroring is a version 4 feature. The official protocol requires:

- a v4 FlatBuffers control implementation,
- an in-place TLS 1.3 upgrade after the plaintext version handshake,
- a receiver-generated certificate and advertised SPKI fingerprint,
- QR-based fingerprint pinning to resist discovery spoofing,
- a WebRTC answerer with non-trickle ICE,
- SDP offer/answer exchange through `StartMirroringSession` and
  `MirroringSessionDescription`,
- an advertised `media.mirroring` receiver capability.

The reference sender uses GStreamer WebRTC and host-only ICE candidates. Mirror
Home has none of those receiver components, so the disabled control is correct.

Sources:

- [FCast protocol v4](https://gitlab.com/futo-org/fcast/-/raw/master/docs/docs/protocol/v4.md)
- [FCast mirroring signaller](https://gitlab.com/futo-org/fcast/-/raw/master/crates/mirroring-core/src/fsignaller.rs)

## Feasibility on this firmware

Android 6's platform TLS stack does not provide the required TLS 1.3 receiver
path. Media3 is a playback engine, not a WebRTC receiver. A conforming port
would therefore need to bundle:

1. a modern TLS provider,
2. FlatBuffers-generated v4 messages,
3. a WebRTC stack and VP8/H.264 decoder integration,
4. certificate generation and fingerprint QR enrollment,
5. extensive latency, memory, and GPU testing on a 1 GB APQ8016.

That is possible only as a substantial native subsystem and is not a safe
incremental change to the current API-23 appliance.

## Achievable progression

1. Keep v3 URL playback stable and advertise only proven capabilities.
2. Use H.264 RTSP or low-latency HLS from OBS/FFmpeg when desktop-screen
   streaming is needed; Mirror Home already accepts those URLs.
3. Add a DLNA/UPnP renderer only if a maintained API-23-compatible library can
   be isolated and fuzzed. DLNA is useful for media, not low-latency mirroring.
4. Implement FCast v4 in the owner-controlled Linux image, where current
   GStreamer, TLS 1.3, and the official Rust protocol libraries are available.

AirPlay mirroring adds reverse-engineered pairing, codec, and FairPlay/DRM
constraints. Google Cast receiving remains proprietary. The official IFC6309
Nougat BSP advertises Miracast support, but the vendor statement does not prove
that it includes sink mode; that must be verified from the BSP before relying
on it.

## Security

FCast v3 is plaintext and has no standard sender authentication. It should be
used only on a trusted LAN. Version 4 encrypts and authenticates the receiver
through pinned TLS, but its documented handshake still does not provide the
same named-client authorization model as Mirror Home's control API.
