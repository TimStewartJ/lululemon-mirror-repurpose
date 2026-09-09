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
Use **Settings > Paired devices** to review or revoke one device without
signing out the others.

Pairing codes expire after ten minutes, are single-use, and lock temporarily
after repeated failures.

## The control application

The control application has four sections:

- **Home**: a live miniature of what the mirror is showing right now, the
  brightness slider (applies when released), wake/sleep, what is playing, and a
  way to play a media link.
- **Display**: what the mirror shows, the layout editor, weather, and photos.
  It also manages private background videos stored separately from the app.
- **Schedule**: sleep/wake times, wake brightness, and presence sensing.
- **Settings**: name, clock, Wi-Fi, the recovery setup network, paired
  devices, and version/address details.

## Dashboards

**Display > What the mirror shows** offers two sources; selecting one applies
it immediately:

- **Mirror**: the built-in layout with a hairline clock, weather, and any
  widgets you arrange. Photos live here too: place one or more Photo widgets,
  up to a full-bleed frame that rotates through the library.
- **Web page**: any HTTP(S) page reachable by the Mirror, such as a Home
  Assistant dashboard.

The built-in surfaces are designed for two-way mirror glass: true black
backgrounds, thin white type, and stroke weather glyphs that stay legible
through the reflection.

The built-in dashboard can show current weather, today’s high/low and rain
chance, plus an optional hourly strip. Search for a city/postal code or enter
coordinates manually. The coordinates are stored privately and sent over HTTPS
to Open-Meteo to request forecasts every 30 minutes. Forecast results are cached
privately on the Mirror; cached values remain available during an Internet
outage and are labeled stale after 90 minutes. Browser-based location detection
is available only from a secure HTTPS or localhost control origin; normal LAN
HTTP users should use city search.

The visual editor supports a configurable snap grid, edge/center guides,
undo/redo, widget locks, layers, keyboard movement,
duplicate instances, and validated JSON import/export. Canonical widgets remain
available and can be hidden; duplicated instances can be deleted.

The **Photo** widget places a framed photo anywhere in the layout. Choose one
library photo or let the frame rotate through the whole library every twenty
seconds with a slow crossfade; choose **Fill** to crop to the frame or
**Whole** to letterbox; alignment sets the crop focus. Duplicate frames for a
collage. Tapping a photo in the library places it in a frame. Photo frames,
backgrounds, and the control application's library grid use scaled,
EXIF-oriented variants prepared on the Mirror so phone pictures display upright
and the display never decodes a multi-megapixel original.

### Notes

**Home > Leave a note on the mirror** posts a note (up to 1,000 characters,
line breaks kept) that appears on the glass within a few seconds without
touching the layout. The list beneath the composer shows every note on the
Mirror with edit and delete controls; the Mirror keeps up to 50 notes, newest
first. Notes are content rather than layout, so any paired phone can post one,
and other paired browsers see the change at their next status refresh.

The **Note** widget decides how notes appear. Its **Shows** menu offers:

- **Newest note** — the most recent note (the default),
- **Rotate through notes** — cycle through all notes every twenty seconds with
  a short fade,
- **All notes** — stack every note in the widget,
- **Always: …** — pin one specific note,
- **Fixed text** — the classic inline tagline typed into the widget itself.

**Size** chooses **Fit** (shrink the text until it fits the box) or a fixed
small, medium, or large size that clips from the top instead of shrinking;
**Weight** picks thin, light, regular, or medium type. Duplicate the widget to
show, for example, a pinned house rule beside the rotating family notes. If
nothing on the glass is showing notes when you post one, the Mirror turns on
the canonical Note widget with **Newest note** so the note is seen; a visible
fixed-text tagline is left alone.

Remote dashboards automatically fall back to a local clock when their main
page returns an error or becomes unreachable. Mirror Home retries every minute.

### Visual layout editor

Open **Display > Arrange the mirror** from any paired phone or desktop. The
canvas uses exactly the same renderer as the mirror, so what you see is what the
glass shows:

- drag a widget to move it; drag its corner handle to resize it,
- tap a widget chip to select it, or its eye to show or hide it,
- change visibility, lock, alignment, opacity, layer, and exact geometry,
- choose a solid color, gradient, or library photo background,
- choose an uploaded H.264 video background, Fill/Whole fitting, and dimming,
- adjust photo dimming and the text/detail colors,
- choose what each Note widget shows and how large and heavy its type is,
- select **Save & show on mirror** to apply the layout immediately.

The **…** menu exports or imports a layout file and offers **Reset to
default**, which restores the reflection-first composition without changing
Wi-Fi, pairings, photos, or schedules. First-run and disconnected states
continue to use the native setup screen regardless of the saved layout.

The photo library accepts JPEG, PNG, WebP, and GIF images up to 20 MB each,
with a 250 MB total library limit. Photo bytes are served only to the Mirror's
loopback interface; remote browsers can list, upload, and delete them only with
an authenticated API request.

### Background videos

**Display > Background videos** accepts H.264 MP4 files up to 256 MiB. Uploads
are validated against the Mirror's hardware decoder and stored privately outside
the APK, so app updates remain small. The library keeps up to 12 videos and
768 MiB while reserving at least 512 MiB of free device storage.

Choose **Use** on a library card or select **Video** in the background editor.
The previous active video remains available through **Use previous video**.
Active videos cannot be deleted. Audio tracks are ignored, display sleep pauses
the loop, and full-screen casting temporarily takes over the same decoder before
the background resumes.

See [Background videos](background-videos.md) for storage, CLI, validation, and
recovery details.

## Clock and schedules

Pairing from a browser copies its IANA time-zone name and current UTC offset to
the Mirror. The vendor firmware lacks a complete Java time-zone database, so
opening the controls after a daylight-saving transition refreshes the offset.

The **Schedule** tab can:

- set local sleep and wake times,
- set wake brightness,
- sleep or wake immediately with a four-hour manual override,
- use an ambient-light sensor when one exists,
- wake when the camera sees movement and sleep after a configurable period
  without movement.

The schedule acts as quiet hours for motion sensing: movement wakes the display
only inside the configured wake window. With the schedule disabled, motion
sensing operates all day. Manual sleep/wake overrides remain authoritative for
four hours, and active media playback is never stopped merely because the room
is still.

The IFC6309 MIRROR reports no ambient-light sensor, but its built-in camera can
run a 160x120 local motion monitor. It compares luminance changes, immediately
discards every frame, and performs no recording, face recognition, or network
upload. If camera permission or the camera itself is unavailable, presence
automation fails open and leaves the display awake inside its schedule.

Sleep stops media when entered by schedule or manual action, shows a black
overlay, and turns the physical panel backlight completely off while Android,
the camera monitor, and network controls remain active. Wake restores the
selected brightness.

## Media

FCast v3 senders discover the Mirror automatically on TCP `46899`. Direct
HTTP(S), HLS, DASH, and RTSP URLs can also be played from the **Media** tab.
Direct-UI playback starts muted by default.

See [Streaming](streaming.md) and [Casting roadmap](casting-roadmap.md).

## Software updates

Mirror Home can be updated from a trusted computer over the LAN after one-time
OTA supervisor provisioning:

```powershell
.\tools\ota.ps1 status
.\tools\ota.ps1 push PATH_TO_SIGNED_MIRROR_HOME_APK
```

Only a newer release signed by the configured Mirror release certificate is
accepted. The supervisor saves the current APK, installs the candidate, and
restores the saved release automatically if the Home API does not become
healthy. See [LAN OTA updates](ota-updates.md).

## Recovery

If normal Wi-Fi is unavailable, use the **Start recovery setup network** action
while connected through USB, or restart with no managed Wi-Fi on a newly
provisioned installation. The Mirror displays Wi-Fi Direct setup QR codes.

The stock launcher and factory system APKs remain installed. See
[Recovery](recovery.md) before using the transient privileged helper.
