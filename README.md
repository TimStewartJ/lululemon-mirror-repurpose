# Mirror Repurpose

An owner-controlled software stack for repurposing the original Qualcomm-based
MIRROR fitness display as a configurable smart mirror, dashboard, and
home-network media renderer.

The project intentionally separates:

- **Mirror Home**: a normal Android HOME application for the dashboard, pairing,
  Wi-Fi provisioning, device controls, and media playback.
- **System Helper**: an optional, narrowly scoped, transient UID-1000 helper
  installed through a device-specific signature-verification flaw. It applies
  persistent kiosk/default-HOME settings and must be restored to the factory APK
  before reboot. It is not required for normal dashboard operation.
- **Device-hosted controls**: a responsive local web application served by
  Mirror Home itself for phone/desktop pairing, dashboards, schedules, photos,
  Wi-Fi, media, and client revocation.
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
- Device APKs are pulled locally and backed up before any update is installed.
- Wi-Fi passwords, pairing tokens, certificates, and device serials are never
  committed.
- The stock launcher remains recoverable through ADB.
- Privileged updates are removable; uninstalling the update restores the
  factory system APK.
- Qualcomm EDL and the hardware force-USB switch are recovery mechanisms, not
  routine installation paths.

See [Recovery](docs/recovery.md) before installing anything.

## Project status

Mirror Home is a device-validated standalone appliance. It serves its own
phone/desktop control UI, displays QR onboarding, creates a Wi-Fi Direct setup
network when no managed Wi-Fi exists, supports named revocable clients,
device-local clock/photo dashboards, custom and Home Assistant URLs, offline
fallback, timezone-aware sleep/wake schedules, private on-device motion presence
sensing, and watchdog recovery. Media3
supports HTTP(S), HLS, DASH, and RTSP, while FCast v3 provides open LAN casting.
The desktop companion remains available but is no longer required for daily
operation.

## Development

Requirements:

- Windows, macOS, or Linux
- JDK 17 or newer
- Android SDK Platform 35 and current Android Build Tools
- Node.js 22 or newer
- Python 3.9 or newer
- ADB authorization on the owned Mirror

Create `local.properties` without committing it:

```properties
sdk.dir=C:\\Users\\you\\AppData\\Local\\Android\\Sdk
```

Build:

```powershell
.\gradlew.bat :android:mirror-home:assembleDebug
```

The one-command build, installation, rollback, and recovery workflow is exposed
through `tools\mirror.ps1`.

Start with the [User guide](docs/user-guide.md). See
[Streaming](docs/streaming.md) for sender compatibility and recommended
media formats and [Provisioning](docs/provisioning.md) for USB, BLE, and LAN
setup. See [Automation](docs/automation.md) for REST examples,
[Casting roadmap](docs/casting-roadmap.md), and
[OS replacement](docs/os-replacement.md).

Common development commands:

```powershell
.\tools\mirror.ps1 status
.\tools\mirror.ps1 backup
.\tools\mirror.ps1 install-home
.\tools\mirror.ps1 rollback-home
.\tools\mirror.ps1 install-helper
.\tools\mirror.ps1 forward
.\tools\mirror.ps1 restore-helper
```

`install-helper` refuses unknown firmware and APK hashes, backs up the factory
APKs, binds the helper to the current Mirror Home signing certificate, verifies
the stock manifest contract, and checks that the helper process starts. Run
`restore-helper` before rebooting: Android 6's boot-time dex optimizer cannot
safely process the compatibility wrapper.

Use `restore-helper --keep-kiosk-settings` after intentionally applying the
preferred HOME/kiosk configuration; omit the flag for a full settings rollback.

### Release signing

Debug builds are suitable while developing, but a deployed appliance should use
a private, backed-up release key. Generate one with the JDK `keytool`, copy
`keystore.properties.example` to the ignored `keystore.properties`, and replace
all placeholder values:

```powershell
New-Item -ItemType Directory -Force .secrets
keytool -genkeypair -v -keystore .secrets\mirror-home.jks `
  -alias mirror-home -keyalg RSA -keysize 4096 -validity 10000
Copy-Item keystore.properties.example keystore.properties
.\tools\mirror.ps1 install-home --variant release
```

Keep the keystore and its passwords private and backed up. Android accepts
future in-place updates only when they are signed by the same key. The transient
helper reads the certificate from the app actually installed on the Mirror, so
the same workflow supports both debug and release installations.

Run every build, test, lint, and dependency-audit lane with:

```powershell
python tools/check.py
```

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
