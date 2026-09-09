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

The current Wi-Fi provisioning implementation intentionally uses Android 6's
`WifiConfiguration` APIs and rejects Android 10 or newer, where ordinary apps
can no longer manage saved networks through that API.
