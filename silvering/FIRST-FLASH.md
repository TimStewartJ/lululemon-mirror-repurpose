# First flash of a unit (maintainer runbook)

How a unit goes from its factory system to Silvering in one sitting with a
USB cable. Written for the project's own Mirror (profile `msm8916-g52cbf28`);
the installer (M5) will script the same steps. **Nothing here is run without
the owner's go-ahead to flash.** Every **Stop** below means: write down what was
seen, leave the unit as it is, and do not look for a way around it (no
`fastboot oem`, no unlock, no other partition).

Only `system` and `userdata` are written. `boot`, `aboot`, `recovery` and every
other partition stay as the factory left them. `stage-image.sh` leaves the
build's recovery installer (`install-recovery.sh`, `recovery-from-boot.p`) out
of `system.img`, so nothing on the new system writes `recovery` either.

## Known before the session

Read over Wi-Fi through Mirror Home 2.3.0-dev.28 on the project's unit
(`/api/v1/device/inventory`, `/api/v1/device/files?root=/`):

- `/system` is mounted straight from `by-name/system` and `/data` straight from
  `by-name/userdata`; `ro.secure=1`, `ro.debuggable=0`, `ro.adb.secure=1`,
  `ro.crypto.state=unencrypted`, no `partition.system.verified`. The boot image
  does not check `/system` with dm-verity, and has not encrypted `/data`.
- The ramdisk's `verity_key` is the same as the one the build signs with, and
  its `file_contexts`, `seapp_contexts`, `property_contexts` and
  `service_contexts` match the CAF release Silvering is built from (file
  contexts: six extra MIRROR lines). SELinux stays enforcing under the factory
  policy.
- The ramdisk starts `/init.mirror.rc` (unreadable), presumably for MirrorOS
  services that Silvering does not have; init logs and continues.
- `/system/build.prop`, factory and Silvering alike, asks for the USB functions
  `diag,serial_smd,rmnet_bam,adb`; the unit now runs `mtp,adb`, which this
  computer's ADB already works with. `userdata.sh` keeps `mtp,adb`.
- Everything in `userdata` is lost: Mirror Home's pairings, layout, board,
  photos, background films and Android's Wi-Fi networks.

## Not ready yet (each one is a Stop until done)

1. **OTA on Silvering.** The OTA supervisor accepts updates only on the factory
   fingerprint (`SUPPORTED_FINGERPRINT`), and Silvering's differs. Until a
   supervisor that knows Silvering's fingerprint is in the image, the OTA and
   rollback checks cannot pass.
2. **The files apps cannot read** (listed under `unread` in the image's
   `unit.json`): there is no step yet that adds files pulled over ADB, with
   their owners, modes and labels, to the unit's copy. Without them the image
   lacks Qualcomm daemons (Wi-Fi, modem, thermal and others).
3. **Restore path.** Before the first write, show that fastboot is reachable by
   hand (hold VOL- while powering on) and that `fastboot reboot` returns to
   Android. If the bootloader refuses the writes (a locked bootloader may), the
   unit is unchanged; that ends the session.

## Before the cable

1. On lazrik, the image without unread files: `SYSTEM_TAR=... ./build.sh`
   (no `ALLOW_UNREAD`). Copy `system.img` and `system.img.sha256` to the
   flashing computer and check the hash there.
2. Build the userdata image, with the flashing computer's own public key
   (`%USERPROFILE%\.android\adbkey.pub`, the half of the key that this `adb`
   uses) and the unit's own labels; the size comes from fastboot below, so this
   runs during the session:

   ```sh
   FILE_CONTEXTS=~/silvering/units/<profile>/ramdisk/file_contexts \
       ./userdata.sh <partition-size:userdata> adbkey.pub userdata.img
   ```

3. Use one pinned `fastboot` (platform-tools, version noted) whose `flash`
   does not erase first; check with `fastboot --version`.

## In the session

1. **Cable in, factory Android running.** `adb devices` lists exactly one unit
   as `device`; note its serial and use `-s <serial>` for every command.
2. **Last gate, over ADB:**

   ```sh
   adb -s <serial> shell cat /fstab.qcom
   adb -s <serial> shell getprop ro.vold.forceencryption
   ```

   Go on only if the `/system` line is `ext4 ro,...` with flags `wait` and no
   `verify`, the `/data` line has `encryptable=footer` (not `forceencrypt`),
   and `ro.vold.forceencryption` is empty or `0`. Anything else, or a file
   that cannot be read: **Stop**.
3. **Fastboot by hand.** Power off, hold VOL- while powering on;
   `fastboot devices` shows the unit. Then:

   ```sh
   fastboot -s <serial> getvar partition-size:system
   fastboot -s <serial> getvar partition-size:userdata
   ```

   **Stop** if `system.img` unsparsed (`simg2img`, or the `Blocks` line of
   `system_image_info.txt`) is larger than the system partition.
4. **userdata image** from the size just read (step 2 of Before the cable).
5. **Write, one command at a time, each only if the one before said OKAY:**

   ```sh
   fastboot -s <serial> flash system system.img
   ```

   If this fails, stay in fastboot and **Stop**: the unit still starts its
   factory system with `fastboot reboot`. Then:

   ```sh
   fastboot -s <serial> flash userdata userdata.img
   fastboot -s <serial> reboot
   ```

   Never `flash boot`, `aboot`, `recovery`, `erase`, `format`, `-w` or `oem`.
   Write down each command and its answer.
6. **First boot.** `adb -s <serial> devices` shows `device` (not
   `unauthorized`). Keep `adb logcat -b all -d` and `adb shell dmesg` from this
   boot.
7. **Device owner:** `adb shell dpm set-device-owner
   dev.mirror.repurpose.updater/.OtaDeviceAdminReceiver` (as in
   [OTA updates](../docs/ota-updates.md) step 4).
8. **Pair and Wi-Fi.** Pair a client through `adb forward` to Mirror Home,
   give it the Wi-Fi network through Mirror Home's setup, and confirm the
   unit's LAN address. Only then `ota.ps1 provision` (OTA updates step 5) and
   `ota.ps1 status` over the LAN.
9. **Checks** before the cable comes out: cold boot (power off and on) with ADB
   and Wi-Fi back by themselves, pairing, sleep and wake, audio, an OTA update
   of Mirror Home and a forced rollback (`ota.ps1 push` of a health-failure
   build). `adb tcpip 5555` gives Wi-Fi ADB only until the next restart; a
   lasting way in is still to be chosen.

## If it does not start

`aboot` is untouched: hold VOL- at power-on, `fastboot getvar all`, and flash
a corrected `system` (or a fresh `userdata`). Keep the boot log for the fix.
