# Recovery

Read this before installing the optional system helper.

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

If removal leaves the package unavailable, reinstall the locally backed-up
original APK, reboot, and investigate before continuing.

## Stock launcher

Never remove `com.mirror.launcher`. If Mirror Home fails:

```powershell
adb shell pm enable com.mirror.launcher
adb shell am force-stop dev.mirror.repurpose
adb shell am start -a android.intent.action.MAIN -c android.intent.category.HOME
```

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
