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
