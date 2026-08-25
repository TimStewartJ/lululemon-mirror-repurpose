# Changelog

## Unreleased

## 1.9.0 - 2026-08-25

- Add notes as content, separate from the layout. **Home > Leave a note on the
  mirror** posts up to 1,000 characters (line breaks kept) that reach the glass
  within a few seconds over `GET/POST /api/v1/notes` and
  `PUT/DELETE /api/v1/notes/{id}`; the Mirror keeps up to 50 notes, newest
  first, and a `notesVersion` field in status and runtime tells clients when
  to re-fetch. The list under the composer edits and deletes notes.
- Give the Note widget a source: newest note (the new default), rotate through
  notes every twenty seconds with a short fade, all notes stacked, one pinned
  note, or the classic fixed text. Add Fit / S / M / L sizing (fixed sizes
  clip instead of shrinking) and thin / light / regular / medium weights.
  Saved layouts without a source keep showing their own text.
- Raise the note text cap from 120 to 1,000 characters and honor line breaks
  on the glass; the cap stays at 120 for other widgets' unused text field.
- Posting a note when nothing on the glass shows notes turns on the canonical
  Note widget with "Newest note"; a visible fixed-text tagline is left alone.

## 1.8.2 - 2026-08-25

- Remove the Aurora dashboard source. The Mirror layout and a web page are
  the two remaining sources; a Mirror still pointed at Aurora falls back to
  the built-in dashboard.
- Remove the editor's "Face zone" reflection overlay and its toggle.

## 1.8.1 - 2026-08-25

- Fix photo frames stretching to the frame's aspect ratio for the length of
  each crossfade on the Mirror. The WebView's Chromium 44 hands an animating
  `<img>` straight to the GPU and ignores `object-fit` (crbug.com/369020,
  fixed in Chromium 48), so the fade now runs on a wrapper layer while the
  image itself is never composited on its own.
- Remove the full-screen Photos dashboard source. Photo widgets cover the same
  ground inside the Mirror layout, including a full-bleed rotating frame. A
  Mirror still pointed at the retired page falls back to the built-in
  dashboard instead of the offline screen.

## 1.8.0 - 2026-08-24

- Remove Bluetooth LE provisioning: the GATT advertising service, its
  Bluetooth permissions, the `bleProvisioning` status field, the Bluetooth
  dashboard widget, and the companion's Web Bluetooth flow. Wi-Fi setup is the
  on-glass QR + Wi-Fi Direct flow (or USB), which works from any phone camera
  and browser. Saved layouts that still contain a Bluetooth widget load with it
  dropped rather than resetting to defaults.

## 1.7.0 - 2026-08-24

- Add a Photo widget: a framed library photo, or a slow crossfading rotation
  through the whole library, placed and resized like any widget with Fill or
  Whole fitting, alignment-based crop focus, and duplication for collages.
- Prepare cached, EXIF-oriented 480px and 1920px photo variants on the Mirror
  and use them for frames, backgrounds, the gallery, and the control
  application so phone photos display upright and the display never decodes a
  multi-megapixel original.
- Place a library photo in a frame by tapping it in the control application.

## 1.6.0 - 2026-08-24

- Redesign every surface people see in the glass: a shared renderer draws the
  built-in dashboard with a hairline clock and quiet meridiem, stroke weather
  glyphs by WMO code, an hourly strip that hides meaningless rain chances, and
  stable fit-to-box typography that never clips or jitters.
- Select type weights through the Android family aliases because the 2015
  WebView ignores numeric `font-weight`; update the clock on the exact second
  and only repaint what changed.
- Rebuild Aurora as slow curtains of deep light, the photo gallery with
  long crossfades and a legibility scrim, and the offline fallback as a calm
  clock.
- Replace the native first-run screen with a hairline clock, rounded QR card,
  and a large grouped pairing code; remove on-glass debug telemetry.
- Rebuild the control application around Home, Display, Schedule, and
  Settings with a live miniature of the mirror, switches, segmented controls,
  immediate-apply brightness and source selection, a widget chip rail, and a
  canvas that shares the mirror's renderer.
- Add authenticated photo thumbnails for the library grid and a
  reflection-first default layout with warmer white text.
- Restyle the desktop companion to match.

## 1.5.2 - 2026-08-24

- Reject incomplete enabled weather coordinates before an empty field can
  coerce to the valid `0,0` location.
- Poll long-running weather refreshes to completion in the control UI, with the
  recurring status refresh as a backstop.

## 1.5.1 - 2026-08-24

- Add Android 6 TLS 1.2 support with a tracked public ISRG Root X1 trust anchor.
- Fix weather rendering scope, immediate city-search wiring, secure-context
  geolocation guidance, and rapid location-change refresh ordering.
- Preserve every canonical widget during v2 import and reject canonical type
  changes.

## 1.5.0 - 2026-08-24

- Add device-local Open-Meteo weather configuration, city search, current and
  hourly forecast widgets, bounded HTTPS fetching, atomic caching, stale status,
  retry scheduling, and offline fallback.
- Upgrade dashboard layouts to schema v2 with automatic v1 migration, duplicate
  widget instances, stable IDs, locks, and explicit layers.
- Add editor snap grids, edge/center alignment guides, reflection safe-zone,
  undo/redo, keyboard movement and resizing, layer controls, duplicate/delete,
  and validated JSON import/export.
- Replace the reset layout with a reflection-first weather-and-clock default;
  migrated layouts keep new weather widgets hidden until weather is enabled.

## 1.4.0 - 2026-08-24

- Add a separate boot-persistent Android device-owner OTA supervisor.
- Add HMAC-authenticated LAN update tooling with independent one-time bootstrap
  credentials, monotonic replay protection, and strict upload limits.
- Require the exact device fingerprint, Home package, release certificate,
  increasing version code, and streamed APK SHA-256 before installation.
- Add local known-good APK backup, silent PackageInstaller updates, loopback
  health checks, automatic data-preserving downgrade, and manual rollback.
- Add token recovery and explicit device-owner deprovisioning through
  ADB-forwarded loopback recovery.
- Add deterministic failure-injection builds and hardware-validate install,
  reboot persistence, health failure, automatic rollback, manual rollback, and
  repeat OTA upgrade.

## 1.2.3 - 2026-08-24

- Reconcile camera monitoring immediately when permission is granted externally
  through Android Settings.

## 1.2.2 - 2026-08-24

- Start the inactivity countdown only after camera frames are healthy, including
  after delayed acquisition or automatic camera recovery.

## 1.2.1 - 2026-08-24

- Turn the physical panel backlight completely off during sleep while keeping
  Android, camera monitoring, and network controls active.
- Detect stalled preview frames and camera-service errors, fail open, and retry
  camera acquisition automatically.
- Verify installed APK bytes when legacy ADB omits its success marker, avoiding
  false update and rollback failures.
- Stop without further package changes when an ADB failure leaves the installed
  APK state unverifiable.

## 1.2.0 - 2026-08-23

- Add private, low-resolution on-device camera motion sensing without recording,
  face recognition, or image upload.
- Add schedule-aware motion wake and configurable inactivity sleep with
  fail-open camera handling, manual-override precedence, and media protection.
- Add camera capability/live-state controls and an optional presence dashboard
  widget.

## 1.1.4 - 2026-08-23

- Use true black as the built-in dashboard default and reset background.

## 1.1.0 - 2026-08-23

- Freeform built-in mirror dashboard with movable and resizable widgets.
- Responsive visual layout editor in the Mirror-hosted control application.
- Custom solid, gradient, or gallery-photo backgrounds with adjustable dimming.
- Configurable widget visibility, opacity, alignment, and custom note.
- More subtle default layout with optional Wi-Fi, media, schedule, light, FCast,
  Bluetooth, uptime, and pairing metrics.

## 1.0.0 - 2026-08-23

First owner-controlled appliance release for the IFC6309 MIRROR profile.

- Initial IFC6309/Android 6 device profile and recovery documentation.
- Full-screen Android HOME dashboard with authenticated USB/LAN API.
- Encrypted BLE Wi-Fi provisioning and pairing-token handoff.
- Optional certificate-bound UID-1000 system helper with guarded installation.
- Media3 HTTP/HLS/DASH/RTSP playback.
- FCast v3 receiver and DNS-SD advertisement.
- Local companion web application with ADB, LAN, media hosting, and controls.
- Mirror-hosted responsive phone/desktop controls with QR pairing.
- Named per-client credentials with hashed storage and independent revocation.
- Wi-Fi Direct first-run/recovery onboarding and DNS-SD control discovery.
- Aurora, offline-fallback, and private local photo-gallery dashboards.
- Browser-derived local timezone, sleep/wake schedules, and brightness automation.
- Service watchdog plus signed transactional APK update and rollback tooling.
