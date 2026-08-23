# Mirror Repurpose

An owner-controlled software stack for repurposing the original Qualcomm-based
MIRROR fitness display as a configurable smart mirror, dashboard, and
home-network media renderer.

The project intentionally separates:

- **Mirror Home**: a normal Android HOME application for the dashboard, pairing,
  Wi-Fi provisioning, device controls, and media playback.
- **System Helper**: an optional, narrowly scoped UID-1000 helper installed
  through a device-specific signature-verification flaw. It is not required for
  normal dashboard operation.
- **Companion**: a local web application for USB setup, LAN administration,
  Bluetooth provisioning, automation, and media streaming.

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

Development is active. The current repository checkpoint provides a buildable
Android bootstrap, exact device profile, architecture, and recovery contract.
Feature checkpoints are tracked in Git and validated on physical hardware.

## Development

Requirements:

- Windows, macOS, or Linux
- JDK 17
- Android SDK Platform 34 and Build Tools 34
- Node.js 22 or newer
- ADB authorization on the owned Mirror

Create `local.properties` without committing it:

```properties
sdk.dir=C:\\Users\\you\\AppData\\Local\\Android\\Sdk
```

Build:

```powershell
.\gradlew.bat :android:mirror-home:assembleDebug
```

The eventual one-command workflow is exposed through `tools\mirror.ps1`.

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
