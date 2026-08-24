# Recovery

Read this before installing the optional system helper. The helper is
**transient**: do not reboot while its `/data/app` update is installed.

## Normal rollback

The helper is installed as an update to a factory system APK. Removing the
update reveals the untouched copy under `/system`:

```powershell
adb shell am force-stop co.mirror.datacap
adb shell pm uninstall -k co.mirror.datacap
adb reboot
adb shell pm path co.mirror.datacap
```

The final path must be:

```text
package:/system/app/co.mirror.datacap/co.mirror.datacap.apk
```

`mirrorctl restore-helper` also restores the kiosk-related settings captured
before the first helper installation. Pass `--keep-kiosk-settings` only when
those changes are intentional; preferred-HOME selection remains independently
reversible through the companion.

If a reboot occurs before rollback, ADB remains available but boot-time
`dex2oat` may repeatedly fail while processing the compatibility wrapper. Run
`restore-helper` as soon as ADB returns, then reboot once more.

If removal leaves the package unavailable, reinstall the locally backed-up
original APK, reboot, and investigate before continuing.

## Stock launcher

Never remove `com.mirror.launcher`. If Mirror Home fails:

```powershell
adb shell am force-stop dev.mirror.repurpose
adb shell am start -a android.intent.action.MAIN -c android.intent.category.HOME
```

The companion's **Restore stock HOME** action changes the preferred activity
back to `com.mirror.launcher/.SplashActivity`. The stock package is never
disabled or removed.

## Mirror Home update rollback

`install-home` is transactional. It:

1. verifies the exact device profile,
2. builds the requested variant,
3. refuses a signing-certificate mismatch,
4. pulls the currently installed APK into the ignored backup directory,
5. installs the candidate,
6. starts HOME and verifies its reported version and resumed activity,
7. automatically reinstalls the previous APK if the health check fails.

Manual rollback uses the latest backup unless an explicit APK is supplied:

```powershell
.\tools\mirror.ps1 rollback-home
.\tools\mirror.ps1 rollback-home --backup C:\path\to\base.apk
```

The application also schedules a 15-minute watchdog that restarts its control
service and reconnects the stock Binder after process loss.

## OTA recovery

The device-owner OTA supervisor on TCP `8791` keeps the last healthy Home APK
in its private storage. Check and restore it without ADB:

```powershell
.\tools\ota.ps1 status
.\tools\ota.ps1 rollback
```

An unhealthy candidate is rolled back automatically after the 60-second
loopback health deadline. `otactl push` returns nonzero when this occurs.

If the active OTA token is lost, use the independently backed-up bootstrap secret
and a local ADB connection:

```powershell
.\tools\ota.ps1 --host MIRROR_IP recover-token --serial DEVICE_SERIAL
```

See [LAN OTA updates](ota-updates.md) before changing device-owner state.

## Fastboot

The IFC6309 LK bootloader is secure and locked:

```text
Device unlocked: false
get_unlock_ability: 0
```

Do not flash generic MSM8916 images.

## EDL/QFIL

Qualcomm EDL enumerates as USB `05c6:9008`. It can be entered from authorized
ADB or through the documented force-USB boot switch. No matching public
Firehose programmer is known for the observed OEM public-key hash.

EDL remains active until main power is removed. It is an emergency observation
and recovery path, not an installation method.
