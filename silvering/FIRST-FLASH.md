# First flash of a unit (maintainer runbook)

How a unit goes from its factory system to Silvering in one sitting with a
USB cable. Written for the project's own Mirror (profile `msm8916-g52cbf28`);
the installer (M5) will script the same steps. **Nothing here is run without
the owner's go-ahead to flash.**

Only `system` and `userdata` are written. `boot`, `aboot`, `recovery` and every
other partition stay as the factory left them, so fastboot keeps working and a
bad image can be replaced by flashing again.

## Known before the session

Read over Wi-Fi through Mirror Home (`/api/v1/device/inventory`, and
`/api/v1/device/files?root=/` from 2.3.0-dev.28):

- `/system` is mounted straight from `by-name/system`, `/data` straight from
  `by-name/userdata`: the boot image does not check `/system` with dm-verity
  and has not encrypted `/data`. The same CAF release's `fstab.qcom` reads
  `wait` for `/system` and `encryptable=footer` for `/data`.
- SELinux is enforcing under the factory policy, which stays (it is in the boot
  image).
- Data on the unit is lost: Mirror Home's pairings, layout, board, photos,
  background films and Android's Wi-Fi networks live in `userdata`.

## Before the cable

1. On lazrik, the image: `SYSTEM_TAR=... ALLOW_UNREAD=1 ./build.sh` (about 75
   minutes from clean). Note its `system.img` path and sha256.
2. Note what the owner wants back afterwards (layout, board, films); the
   companion will need pairing again.

## In the session

1. **Cable in, factory Android running.** `adb devices` lists the unit as
   `device` (this computer's key is already allowed on the project's unit).
2. **Last gate, read over ADB:**

   ```sh
   adb shell cat /fstab.qcom
   adb shell getprop ro.secure; adb shell getprop ro.debuggable
   ```

   Stop if the `/system` line carries `verify`: Silvering's verity data is not
   signed with the board's key.
3. **The files apps cannot read.** Pull every path listed under `unread` in
   the overlay's `unit.json` (`adb pull`, as the shell user), add them to the
   unit's copy and make the image again with `stage-image.sh` (minutes, no
   build). *The step that adds pulled files to the unit's copy, with their
   owners and modes from `adb shell ls -lZ`, is still to be written.*
4. **Fastboot.** `adb reboot bootloader` (or hold VOL- while powering on).
   `fastboot getvar partition-size:system` and
   `fastboot getvar partition-size:userdata`. Stop if `system.img`, unsparsed,
   is larger than the system partition.
5. **userdata image**, with this computer's ADB key so that ADB answers from
   the first boot (the boot image's adbd asks for approval, and a MIRROR has no
   touch screen to give it):

   ```sh
   ./userdata.sh <partition-size:userdata> adbkey.pub
   ```

6. **Flash and start:**

   ```sh
   fastboot flash system system.img
   fastboot flash userdata userdata-<time>.img
   fastboot reboot
   ```

   Never `flash boot`, `aboot`, `recovery`, `erase` or `oem`. Write down each
   command and its answer.
7. **First boot.** `adb devices` shows `device`. Then the OTA supervisor is
   made device owner and given its LAN credential as in
   [OTA updates](../docs/ota-updates.md) steps 4 and 5
   (`dpm set-device-owner dev.mirror.repurpose.updater/.OtaDeviceAdminReceiver`,
   `ota.ps1 provision`).
8. **Wi-Fi** through Mirror Home's setup over the ADB forward, then pair a
   client.
9. **Checks** before the cable comes out: cold boot (power off and on), Wi-Fi
   after the restart, pairing, sleep and wake, audio, an OTA update of Mirror
   Home and a forced rollback (`ota.ps1 push` of a health-failure build), and
   Wi-Fi ADB for later work (`adb tcpip 5555`, then `adb connect`).

## If it does not start

fastboot is untouched: hold VOL- at power-on, read `fastboot getvar all`,
and flash a corrected `system` (or a fresh `userdata`). Keep the boot log
(`adb logcat -b all -d`, `adb shell dmesg` when ADB answers) for the fix.
