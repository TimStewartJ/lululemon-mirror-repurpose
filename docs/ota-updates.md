# LAN OTA updates

Mirror OTA Supervisor is a small, separate Android device-owner application.
It remains alive while Mirror Home is replaced, so a broken HOME release cannot
remove the update or rollback channel.

```text
otactl on a trusted computer
  | HMAC-SHA256 authenticated HTTP, TCP 8791
  v
Mirror OTA Supervisor (device owner)
  | package/profile/certificate/version/hash validation
  | local known-good APK backup
  v
Android PackageInstaller
  | silent signed install
  v
Mirror Home health check on loopback TCP 8787
```

The supervisor accepts only `dev.mirror.repurpose` APKs signed by the same
release certificate as the supervisor. It is also pinned to the exact supported
firmware fingerprint. The Android release private key never resides on the
Mirror.

## One-time provisioning

Provision while physical or temporary wireless ADB recovery is still available:

```powershell
.\tools\ota.ps1 build-supervisor

adb -s bebf077 install -r `
  .\android\ota-updater\build\outputs\apk\release\ota-updater-release.apk

adb -s bebf077 shell dpm set-device-owner `
  dev.mirror.repurpose.updater/.OtaDeviceAdminReceiver

.\tools\ota.ps1 --host 10.0.0.196 provision --serial bebf077
```

Android 6 permits shell provisioning after setup only when the primary user is
the sole user and has no accounts. Stop if `dpm set-device-owner` rejects the
device; do not clear setup state or remove accounts to force enrollment.

Provisioning creates two ignored files:

- `.secrets/mirror-ota-bootstrap.txt`: independent one-time/recovery capability
- `.secrets/mirror-ota.json`: active LAN HMAC credential and default host

Back up both files privately. Neither belongs in source control, an APK, or a
URL. A fresh supervisor build embeds only the SHA-256 hash of the bootstrap
secret.

## Normal update workflow

Increment Mirror Home's `versionCode` and `versionName`, then:

```powershell
.\gradlew.bat :android:mirror-home:assembleRelease
.\tools\ota.ps1 status
.\tools\ota.ps1 push `
  .\android\mirror-home\build\outputs\apk\release\mirror-home-release.apk
```

Use `--host PRIVATE_IPV4` before the command when the Mirror's DHCP address has
changed. A DHCP reservation is recommended.

`push` exits successfully only after the expected Home version serves a healthy
loopback status response. It exits nonzero when installation fails, recovery is
required, or the candidate is automatically rolled back.

## Validation and rollback

Before installation the supervisor:

1. authenticates the request with HMAC-SHA256,
2. permanently consumes its monotonic request counter,
3. enforces a 32 MB upload limit and exact Content-Length,
4. verifies the streamed SHA-256,
5. parses the APK and requires the exact Home package,
6. compares the APK certificate with the supervisor release certificate,
7. requires an increasing version code,
8. copies and re-validates the installed Home APK as the known-good release.

After PackageInstaller succeeds, the supervisor starts Mirror Home and requires
both the expected package version and `/api/v1/status` response. A failed health
check triggers an in-place, data-preserving downgrade using Android 6's guarded
rollback install flag.

Manual rollback is:

```powershell
.\tools\ota.ps1 rollback
```

During `recovery_required`, a newer forward-fix APK may still be uploaded, but
the original known-good backup is never replaced.

## Authentication recovery

If the active OTA token file is lost but the bootstrap secret remains, reconnect
ADB locally and rotate the token:

```powershell
.\tools\ota.ps1 --host 10.0.0.196 recover-token --serial bebf077
```

The bootstrap secret is accepted only by an ADB-forwarded loopback request. The
new active token is durably saved before confirmation.

To intentionally remove device-owner status:

```powershell
.\tools\ota.ps1 deprovision --serial bebf077
```

Do not deprovision during an update. Removing the supervisor afterward also
removes unattended OTA capability.

## Security boundary

Port `8791` is an update endpoint, not a shell:

- no command execution or arbitrary package names,
- no bearer secret transmitted on requests,
- HMAC covers method, path, body hash, counter, and nonce,
- exact counters are deduplicated and old counters are permanently rejected,
- unauthenticated and replayed requests receive HTTP 401,
- transport disclosure cannot alter a signed APK.

HTTP does not provide confidentiality, so keep the Mirror on a trusted LAN and
never forward port `8791` through a router.

The supervisor deliberately cannot update itself over LAN. It is a small,
stable recovery root; changing it requires a same-certificate APK installed
through physical ADB. This prevents a defective supervisor update from removing
the only rollback authority.

## Failure-injection build

Maintainers can build a signed Home APK that intentionally withholds its health
API:

```powershell
.\gradlew.bat :android:mirror-home:assembleRelease `
  -PmirrorHomeVersionCode=NEXT_CODE `
  -PmirrorHomeVersionName=health-failure-test `
  -PmirrorOtaHealthFailureTest=true
```

Use only for a recorded rollback test. Never distribute it as a normal release.
