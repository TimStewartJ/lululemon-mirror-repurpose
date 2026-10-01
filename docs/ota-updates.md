# LAN OTA updates

**Optional:** first complete [Getting started](getting-started.md), including a
release-signed Home, default HOME selection, and a successful reboot/LAN check.
USB Home updates, Wi-Fi setup, and video upload do not require device-owner
enrollment. The supervisor does not select default HOME.

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

Provision with physical USB ADB recovery available. Run these PowerShell
commands from the repository root, one stage at a time; stop on any failure.
Replace `DEVICE_SERIAL` with the intended authorized ADB identifier and
`MIRROR_IP` with its actual private LAN IPv4 address. These are placeholders,
not literal values. Do not commit real serials, credentials, or local logs.

1. Verify the device, working LAN connection, and existing release signing
   configuration. The supervisor and Home must use the **same** private key.
   Do not overwrite an existing key, supervisor bootstrap file, or active
   credential. Check for existing device-owner management before attempting
   enrollment:

   ```powershell
   .\tools\mirror.ps1 --serial DEVICE_SERIAL status
   adb -s DEVICE_SERIAL shell dumpsys device_policy
   ```

   **Expected:** profile `ifc6309-mirror-329` and no conflicting owner.
   If already enrolled, use the existing supervisor/credentials or the
   authentication recovery flow below; do not repeat ownership setup.

2. Build the signed supervisor with an independent bootstrap capability:

   ```powershell
   .\tools\ota.ps1 build-supervisor
   ```

   **Expected:** a JSON result naming the release APK and bootstrap file
   after a successful build. The first build creates
   `.secrets\mirror-ota-bootstrap.txt`; later builds reuse it. Back it up
   privately before installation. Only its hash goes into the build.

3. Install that release APK:

   ```powershell
   adb -s DEVICE_SERIAL install -r `
     .\android\ota-updater\build\outputs\apk\release\ota-updater-release.apk
   ```

   **Expected:** `Success`. Stop for certificate mismatch or installation
   failure; do not uninstall an existing recovery supervisor to work around it.

4. Explicitly enroll the supervisor as device owner:

   ```powershell
   adb -s DEVICE_SERIAL shell dpm set-device-owner `
     dev.mirror.repurpose.updater/.OtaDeviceAdminReceiver
   ```

   **Expected:** Android reports successful device-owner enrollment.
   Android 6's shell path after setup requires a sole primary user with no
   accounts, as enforced in the
   [Android 6.0.1 device-policy source](https://github.com/aosp-mirror/platform_frameworks_base/blob/android-6.0.1_r1/services/devicepolicy/java/com/android/server/devicepolicy/DevicePolicyManagerService.java).
   These conditions are not a guarantee of vendor acceptance. **Stop** if
   rejected; do not clear setup state, remove accounts/users, or factory-reset
   to force enrollment. Continue using USB updates instead.

5. Provision through the tool's temporary ADB forward, then test LAN status:

   ```powershell
   .\tools\ota.ps1 --host MIRROR_IP provision --serial DEVICE_SERIAL
   .\tools\ota.ps1 status
   ```

   **Expected:** `provisioned: true`, a privately saved active credential, and
   an authenticated status response over LAN. The host selects the saved LAN
   destination; bootstrap exchange still occurs through USB loopback. If it
   fails, retain USB and both secrets and investigate; ownership alone does
   not mean LAN updates work.

The build/provision sequence creates two ignored files:

- `.secrets\mirror-ota-bootstrap.txt`: independent one-time/recovery capability
- `.secrets\mirror-ota.json`: active LAN HMAC credential and default host

Back up both files privately and restrict access to your account; Git-ignore
is not an access control. Neither plaintext secret belongs in source control,
an APK, or a URL. After enrollment, verify status again after an intentional
reboot with the factory helper restored. Initial status does not test failed
update rollback; the known-good Home backup is created on the first update.

## Normal update workflow

Increment Mirror Home's `versionCode` and `versionName`, then:

```powershell
.\gradlew.bat :android:mirror-home:assembleRelease
.\tools\ota.ps1 status
.\tools\ota.ps1 push `
  .\android\mirror-home\build\outputs\apk\release\mirror-home-release.apk
```

Use `--host MIRROR_IP` before the command when the Mirror's DHCP address has
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
.\tools\ota.ps1 --host MIRROR_IP recover-token --serial DEVICE_SERIAL
```

The bootstrap secret is accepted only by an ADB-forwarded loopback request. The
new active token is durably saved before confirmation.

To intentionally remove device-owner status:

```powershell
.\tools\ota.ps1 deprovision --serial DEVICE_SERIAL
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

## Supervisor health

From version 1.1.0 the supervisor records its own process history, and
`.\tools\ota.ps1 status` includes it under `health`: when the supervisor
started, how its previous run ended (`update`, `reboot`, `crash` or
`killed`), its last uncaught exception with a stack trace, and its memory
and storage. A supervisor that crashes on start can therefore be diagnosed
once it runs again, instead of leaving no trace. Earlier versions report no
`health`; installing 1.1.0 over one needs USB, like any supervisor change.

Mirror Home separately checks that the supervisor accepts connections and
reports that in its own health report as `otaSupervisor`, so a stopped
supervisor is noticed before an update is attempted. See
[Control protocol](protocol.md#health).

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
