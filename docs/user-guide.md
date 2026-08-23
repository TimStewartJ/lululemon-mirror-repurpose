# User guide

## First run without a computer

When Mirror Home has no saved network, it creates a private WPA2 Wi-Fi Direct
group and displays two QR codes:

1. scan **Join Mirror Setup Wi-Fi**,
2. scan **Open setup**,
3. enter the six-digit pairing code shown on the Mirror,
4. choose the household Wi-Fi network and name the Mirror.

The browser transfers its new credential to the Mirror's LAN address in a URL
fragment. Fragments are not sent in HTTP requests and are removed from browser
history immediately after the destination page loads.

If Wi-Fi is already configured, scan the single control QR code. The control
application is hosted by the Mirror itself on port `8787`; the desktop
companion is optional.

## Pairing additional devices

Open the setup/status dashboard on the Mirror, scan its QR code, and enter the
current pairing code. Every browser receives an independent random credential.
Use **Access > Paired devices** to review or revoke one device without signing
out the others.

Pairing codes expire after ten minutes, are single-use, and lock temporarily
after repeated failures.

## Dashboards

The **Display** tab provides:

- **Native clock and setup**: always-available recovery/status screen.
- **Aurora clock**: an offline, device-local ambient clock.
- **Local photo gallery**: private images stored inside Mirror Home.
- **Custom or Home Assistant URL**: any HTTP(S) page reachable by the Mirror.

Remote dashboards automatically fall back to a local clock when their main
page returns an error or becomes unreachable. Mirror Home retries every minute.

The gallery accepts JPEG, PNG, WebP, and GIF images up to 20 MB each, with a
250 MB total library limit. Photo bytes are served only to the Mirror's
loopback interface; remote browsers can list, upload, and delete them only with
an authenticated API request.

## Clock and schedules

Pairing from a browser copies its IANA time-zone name and current UTC offset to
the Mirror. The vendor firmware lacks a complete Java time-zone database, so
opening the controls after a daylight-saving transition refreshes the offset.

The **Schedule** tab can:

- set local sleep and wake times,
- set wake brightness,
- sleep or wake immediately with a four-hour manual override,
- use an ambient-light sensor when one exists.

The IFC6309 MIRROR reports no ambient-light sensor, so this unit uses its time
schedule. Sleep stops media, shows a black overlay, and sets brightness to the
minimum. Wake restores the selected brightness.

## Media

FCast v3 senders discover the Mirror automatically on TCP `46899`. Direct
HTTP(S), HLS, DASH, and RTSP URLs can also be played from the **Media** tab.
Direct-UI playback starts muted by default.

See [Streaming](streaming.md) and [Casting roadmap](casting-roadmap.md).

## Recovery

If normal Wi-Fi is unavailable, use the **Start recovery setup network** action
while connected through USB, or restart with no managed Wi-Fi on a newly
provisioned installation. The Mirror displays Wi-Fi Direct setup QR codes.

The stock launcher and factory system APKs remain installed. See
[Recovery](recovery.md) before using the transient privileged helper.
