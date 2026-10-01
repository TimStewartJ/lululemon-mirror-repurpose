# Recovery

Read this before the first write in [Getting started](getting-started.md).
Keep authorized USB access, verified factory APK backups, and the release key
available. Factory APK backups are not partition or user-data backups. Do not
factory-reset, clear app data, or uninstall Home to fix a signing error.

The optional helper is **transient**: do not reboot while its `/data/app`
update is installed. No helper is needed for ordinary Home APK updates.

Examples assume only the intended Android device is connected; otherwise use
`adb -s DEVICE_SERIAL ...` and
`.\tools\mirror.ps1 --serial DEVICE_SERIAL ...` with your actual identifier.
Keep that identifier out of Git and shared logs.

## Helper restoration before reboot

The helper is installed as an update to a factory system APK. Prefer the
guarded tool to remove it and verify the factory path:

```powershell
.\tools\mirror.ps1 restore-helper
adb shell pm path co.mirror.datacap
```

Before considering a reboot, the path must be:

```text
package:/system/app/co.mirror.datacap/co.mirror.datacap.apk
```

`restore-helper` also restores the three kiosk-related settings captured
before the first helper installation. Pass `--keep-kiosk-settings` only when
those changes are intentional. **Neither variant resets preferred HOME.**
It can be reversed with Android's HOME selection UI or with the helper's
separate stock-HOME operation while the helper is installed and connected.

If the tool cannot finish but authorized ADB works, removing the update
manually exposes the untouched `/system` APK:

```powershell
adb shell am force-stop co.mirror.datacap
adb shell pm uninstall -k co.mirror.datacap
adb shell pm path co.mirror.datacap
```

Require uninstall `Success` and the exact factory path above. This manual
fallback does not restore saved kiosk settings. Do not reboot while the path
is absent or still under `/data/app`. Resolve restoration first.

In the project's observed premature-reboot failure, ADB remained available but
boot-time `dex2oat` repeatedly failed while processing the compatibility wrapper.
Do not assume ADB will always recover. If it returns, run `restore-helper`,
verify the factory path, and only then reboot once more.

If removal leaves the package unavailable, stop: investigate the verified local
backup and package state rather than blindly reinstalling or rebooting. An
APK restored under `/data/app` is not proof of factory restoration.

## Stock launcher

Never remove `com.mirror.launcher`. If Mirror Home fails:

```powershell
adb shell am force-stop dev.mirror.repurpose
adb shell am start -n com.mirror.launcher/.SplashActivity
```

This explicitly opens stock HOME for recovery; it does not change the default.
A generic HOME intent would relaunch Mirror Home if it is still preferred.
Stop Mirror Home first, as above: while it runs as the preferred HOME app it
brings its dashboard back in front of any other screen after ten seconds.
It leaves other screens alone while a computer is using the Mirror's USB
port, so Android's settings stay open during work over ADB or scrcpy.
To change the preference, try the visible Android HOME settings UI:

```powershell
adb shell am start -a android.settings.HOME_SETTINGS
```

Choose the stock launcher if that vendor screen is usable. Otherwise the
[helper transaction](getting-started.md#optional-transient-helper) can set
stock HOME: use `POST /api/v1/system/home` with `{"enabled":false}` and the
paired client's bearer header. This requires a working Mirror Home API and a
connected helper; it is not available merely because the stock APK exists.
Restore the factory helper afterward. If Home cannot serve its API, first
recover Home with a same-key APK or use an accessible Android Settings UI.

Before reboot, check the factory helper path, then request generic HOME and
visually verify the stock launcher opens without a chooser:

```powershell
adb shell am start -a android.intent.action.MAIN -c android.intent.category.HOME
```

Do not clear Home data as a substitute for resetting the preferred activity.

## Mirror Home update rollback

`install-home` is transactional. It:

1. verifies the exact device profile,
2. builds the requested variant,
3. refuses a signing-certificate mismatch,
4. pulls the currently installed APK into the ignored backup directory,
5. installs the candidate,
6. explicitly starts Mirror Home and checks its reported version and activity,
7. attempts to reinstall the previous APK if the health check fails.

It does not select default HOME or verify a cold boot. On a failed first install,
there is no previous Home APK; it attempts to remove the candidate. If device
state cannot be verified after an ADB error, the tool reports unknown state
rather than blindly rolling back. Stop on that error and inspect the device.
An APK backup does not restore deleted settings or media; Home disables Android
application backup.

Manual rollback uses the latest backup unless an explicit APK is supplied:

```powershell
.\tools\mirror.ps1 rollback-home
.\tools\mirror.ps1 rollback-home --backup C:\path\to\base.apk
```

The application also schedules a 15-minute watchdog that restarts its control
service and reconnects the stock Binder after process loss.

## OTA recovery

If separately enrolled, the device-owner OTA supervisor on TCP `8791` keeps
the pre-update healthy Home APK in private storage. A fresh enrollment has
no known-good backup until an update prepares one. With saved OTA credentials,
check and restore an available backup without ADB:

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

## Wi-Fi and pairing recovery

Use the [USB reference](provisioning.md#usb-access-and-recovery) to reach the
installed Home over `http://127.0.0.1:18787/`. A previously paired browser still
needs its own credential; ADB authorization is not browser pairing. Use
**Settings > Setup network > Start** if needed. A failed saved network does
not automatically trigger Wi-Fi Direct, and missing setup QR/code UI must be
investigated rather than bypassed. See
[browser Wi-Fi configuration](provisioning.md#browser-wi-fi-configuration).

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
