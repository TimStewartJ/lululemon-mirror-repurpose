# Supported devices

Start with [Getting started](getting-started.md) for installation. A matching
chassis or SoC is not enough; authorized ADB must confirm the exact firmware.

## `ifc6309-mirror-329`

| Field | Value |
|---|---|
| Product | `mirror` |
| Device | `msm8916_64` |
| Board platform | `msm8916` |
| Android | `6.0.1` / API 23 |
| Build ID | `IFC-6309-2.0-MIR` |
| Build number | `329` |
| Fingerprint | `mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/329:user/release-keys` |
| Security patch string | `2016-02-01` |
| Display | 1920×1080 physical, 1080×1920 logical portrait |
| RAM / eMMC | 1 GB / 8 GB |

The machine-readable profile is
[`tools\device-profiles\ifc6309-mirror-329.json`](../tools/device-profiles/ifc6309-mirror-329.json).
`.\tools\mirror.ps1 status` compares product, device, board platform, Android
release, build ID, and full fingerprint. Backup/helper operations additionally
check the three factory APK hashes. Do not weaken these checks to install on
a mismatch. Security patch, display, and memory rows are descriptive, not
additional fields checked by `status`.

Do not assume compatibility with later NXP i.MX8-based MIRROR revisions. Those
devices use a different SoC, bootloader, Android release, and trust chain.

## Other units: identify and board profiles

The firmware profile above is for installing on the factory Android. For any
other unit, `python tools\identify.py` reads it without changing anything and
writes a device report that names the board profile it matches by kernel
release. Profiles of the board and kernel combinations owners have reported
are in [`profiles/`](../profiles/README.md); none has an accepted Silvering
image yet.

## Without the factory service

On the factory Android, Mirror Home sets the backlight and the Mirror's name
through the factory service (`com.mirror.services`). Where that service is
missing (a rebuilt Android, the emulator) or does not answer (reported on
MirrorOS 1.16.0), Mirror Home uses Android's own brightness setting if it may
write it, and otherwise holds the level in its own window while the dashboard
is in front; it then keeps the name itself. Give it Android's setting with
`adb shell appops set dev.mirror.repurpose WRITE_SETTINGS allow`. Status and
health say which applies under `device` / `device.controls`: `vendorService`
(`connected`, `unanswered`, `absent`), `brightness` (`vendor`, `system`,
`window`) and `name` (`vendor`, `local`). **Settings > About > Mirror
services** shows the first.

The current Wi-Fi provisioning implementation intentionally uses Android 6's
`WifiConfiguration` APIs and rejects Android 10 or newer, where ordinary apps
can no longer manage saved networks through that API.
