# Lululemon Mirror Repurpose

An owner-controlled, software-only revival of the discontinued **Lululemon
MIRROR** fitness mirror (sold as *MIRROR* by Mirror.co from 2018, under
lululemon after its 2020 acquisition, and as the *lululemon Studio Mirror* from
late 2022 until it was discontinued in 2023). It turns the original
Qualcomm-based unit into a configurable smart mirror, dashboard, and
home-network media renderer without replacing the display electronics: the
Mirror Home app is installed over ADB and selected as Android's default HOME,
while the factory launcher remains installed. Updates can use USB or an
optionally enrolled LAN OTA supervisor.

## Start here

**[Getting started: stock Mirror to working appliance](docs/getting-started.md)**
is the authoritative installation sequence: prerequisites, no-touchscreen ADB
authorization, exact firmware checks, backups, release signing, default HOME,
Wi-Fi, reboot verification, and browser video upload. Start there before
running installation commands. It documents safe stops and optional helper/OTA
branches; it is not a claim of clean-room stock-device validation.

Already installed? Use the [User guide](docs/user-guide.md).

[GitHub Releases](https://github.com/TimStewartJ/lululemon-mirror-repurpose/releases)
provide versioned source and optional project-signed Home APKs with checksums and
build provenance. Read [signing compatibility](docs/getting-started.md#published-apks-and-signing-compatibility)
before using a downloaded APK. Owner-signed source builds remain the recommended
path for owner-controlled updates and optional OTA supervisor enrollment.

This is an independent project with no affiliation to, or endorsement by,
lululemon athletica, Mirror, or Curiouser Products. MIRROR and lululemon are
trademarks of their respective owners and are used here only to identify the
hardware this software runs on.

## Which Mirror is this for?

The Qualcomm APQ8016/MSM8916 generation running Android 6.0.1, build
`IFC-6309-2.0-MIR`. With ADB connected, `adb shell getprop ro.build.fingerprint`
should report
`mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/329:user/release-keys`. Later
NXP i.MX8-based units are not supported; see
[Supported devices](docs/supported-devices.md). If you would rather replace the
electronics than the software, the
[olm3ca/mirror](https://github.com/olm3ca/mirror) community project documents
a TV-mainboard conversion.

The project intentionally separates:

- **Mirror Home**: a normal Android HOME application for the dashboard, pairing,
  Wi-Fi provisioning, device controls, and media playback.
- **System Helper**: an optional, narrowly scoped, transient UID-1000 helper
  installed through a device-specific signature-verification flaw. It applies
  persistent kiosk/default-HOME settings and must be restored to the factory APK
  before reboot. It is not required for normal dashboard operation.
- **OTA Supervisor**: an optional boot-persistent Android device-owner app that accepts
  only authenticated, release-signed Mirror Home APKs, health-checks them, and
  restores the local known-good release after failure.
- **Device-hosted controls**: a responsive local web application served by
  Mirror Home itself for phone/desktop pairing, dashboards, schedules, photos,
  Wi-Fi, media, private background-video libraries, and client revocation.
- **Companion**: an optional desktop application for ADB recovery, USB media,
  and development workflows.

## Supported hardware

The initial supported profile is deliberately exact:

```text
Product:        mirror
Device:         msm8916_64
SoC:            Qualcomm APQ8016/MSM8916
Android:        6.0.1 (API 23)
Build:          IFC-6309-2.0-MIR / 329
Fingerprint:    mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/329:user/release-keys
```

New hardware profiles must be added explicitly. Installation scripts refuse to
apply privileged artifacts when the build fingerprint or source APK hash does
not match.

## Safety model

- Proprietary MIRROR APKs and firmware are never distributed.
- The installation guide requires hash-verified factory APK backups before
  installation. Home updates separately back up the installed Home APK;
  neither operation is a full firmware or application-data backup.
- Wi-Fi passwords, pairing tokens, certificates, and device serials are never
  committed.
- The stock launcher remains recoverable through ADB.
- Privileged updates are removable; uninstalling the update restores the
  factory system APK.
- Qualcomm EDL and the hardware force-USB switch are recovery mechanisms, not
  routine installation paths.

The [getting-started guide](docs/getting-started.md) includes the required
[recovery precautions](docs/recovery.md) before installation.

## Project status

Mirror Home is a device-validated standalone appliance. It serves its own
phone/desktop control UI, displays QR onboarding, attempts a Wi-Fi Direct setup
network when neither a managed SSID nor an active Wi-Fi connection exists,
supports named revocable clients,
device-local clock/photo dashboards, custom and Home Assistant URLs, offline
fallback, timezone-aware sleep/wake schedules, private on-device motion presence
sensing, cached local weather, precision dashboard editing, signed LAN OTA
updates with automatic rollback, content-addressed background videos stored
outside the APK, and watchdog recovery. Media3
supports HTTP(S), HLS, DASH, and RTSP, while FCast v3 provides open LAN casting.
The desktop companion remains available but is no longer required for daily
operation.

## Development

The [prerequisites](docs/getting-started.md#1-confirm-scope-and-prepare-the-computer)
distinguish the Android build/USB tools from optional Node.js companion tools.
Use JDK 17, SDK Platform 35, Build Tools 35.0.0, and the checked-in Gradle
wrapper. Python 3.9+ runs the repository tooling; Node.js 22+ is needed only
for companion development or the full repository validation gate.

Build a debug APK for development (this does not install or select HOME):

```powershell
.\gradlew.bat :android:mirror-home:assembleDebug
```

Use the [installation sequence](docs/getting-started.md) rather than treating
the CLI commands as an unattended setup script. `install-home` defaults to a
signed release and does not select default HOME. Do not switch a deployed app
between debug and release keys.

References: [User guide](docs/user-guide.md),
[Streaming](docs/streaming.md),
[Provisioning](docs/provisioning.md) (including
[no-touchscreen ADB authorization](docs/provisioning.md#initial-adb-authorization-without-a-touchscreen)),
[Automation](docs/automation.md),
[Background videos](docs/background-videos.md),
[LAN OTA updates](docs/ota-updates.md), [Casting roadmap](docs/casting-roadmap.md), and
[OS replacement](docs/os-replacement.md).

Status and USB forwarding, after authorization and profile checks:

```powershell
.\tools\mirror.ps1 status
.\tools\mirror.ps1 forward
```

The [optional helper transaction](docs/getting-started.md#optional-transient-helper)
must include explicit HOME/kiosk API actions and verified factory restoration
before reboot. `restore-helper` restores captured kiosk settings unless
`--keep-kiosk-settings` is passed; neither form reverses preferred HOME.

### Release signing

Follow [Sign and install Mirror Home](docs/getting-started.md#4-sign-and-install-mirror-home)
for first-time key creation and configuration. Preserve existing signing
material; keep it private and backed up. Future in-place updates must use the
same certificate. The optional OTA supervisor must use that certificate too.

Run every build, test, lint, and dependency-audit lane with:

```powershell
python .\tools\check.py
```

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
