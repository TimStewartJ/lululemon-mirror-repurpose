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

1. **The files apps cannot read.** Mirror Home's transfer could not read 107
   files of the project's unit (`lstat` fails with `EACCES`). They cannot be
   read over USB either. In the unit's own SELinux policy (its ramdisk's
   `sepolicy`, a `user` build) neither `adbd`, which serves `adb pull`, nor
   `shell`, which runs `adb shell`, has `getattr` or `read` on any of their
   45 labels, and nor do `runas`, `dumpstate` or `system_server`; only `init`
   and `debuggerd` may read them, and neither hands out a file. (Checked
   with the CAF tree's `sepolicy-check`, which does find those rules for
   `shell` on `qmuxd_exec`, `netmgrd_exec` and `diag_exec` in Silvering's
   userdebug policy, and for `adbd` on `system_file` in the unit's.) `aboot`
   is locked under secure boot, so no unsigned image can be RAM-booted to
   read them as root, and no signed Firehose programmer is known for EDL
   ([OS replacement](../docs/os-replacement.md)). Of the 107, the build
   makes 41 itself (AOSP daemons, and `wpa_supplicant`, `hostapd` and
   `hostapd_cli` from Silvering's Wi-Fi board settings) and leaves one out
   (`install-recovery.sh`); `unit-files.py` lists them under
   `unreadNotNeeded`. The other **65 are not in the image** (`unread` in
   `unit.json`; `unreadBuildCopy` would list board configuration the image
   carries in the build's version instead). The unit loses them for good once
   `system` is written. On 2026-11-21 the factory image's service states
   (`init.svc.*`, read through Mirror Home 2.3.0-dev.29) showed 19 of them
   running: `audiod`, `cnd`, `cnss-daemon`, `dpmd`, `fidodaemon`,
   `imsqmidaemon`, `loc_launcher`, `mapd`, `per_mgr` (`pm-service`), `perfd`,
   `ppd` (`mm-pp-daemon`), `ptt_socket_app`, `qcamerasvr`
   (`mm-qcamera-daemon`), `qcomsysd`, `qseecomd`, `rfs_access`, `rmt_storage`,
   `tftp_server` and `time_daemon`. Four were `restarting` in each of eight
   readings over two and a half minutes, so they do not stay up on the
   factory image either: `thermal-engine`, `adsprpcd`, `ims_rtp_daemon` and
   `imsdatadaemon`. The rest were not observed as services (a program that
   another program or a one-off init command starts has no service state).
   init skips a service whose program is missing. What their absence costs
   (the modem's storage servers, TrustZone's `qseecomd`, the clock's
   `time_daemon`) is not known until Silvering runs. The owner approves the
   list before the write, and the image is built with it (`ALLOW_UNREAD=<the
   approved list>`), so the files the transfer reported unread are, apart
   from those the build makes, exactly the approved ones: absent from the
   image (`unread`) or carried in the build's version (`unreadBuildCopy`).
2. **Restore path.** Before the first write, show that fastboot is reachable by
   hand (hold VOL- while powering on) and that `fastboot reboot` returns to
   Android. If the bootloader refuses a write before writing anything (a locked
   bootloader may), the unit is unchanged; that ends the session.

## Before the cable

1. On lazrik, the image: `SYSTEM_TAR=... ALLOW_UNREAD=<approved list>
   ./build.sh` (one path per line; the build refuses if the unread files
   the image needs are not exactly those), with
   `~/silvering/apps/MirrorUpdater.apk` at OTA
   supervisor **1.4.0** or later, built with the bootstrap secret this
   computer keeps (`otactl.py build-supervisor`). Older supervisors accept
   updates only on the factory fingerprint, so OTA and rollback would fail on
   Silvering. The image's `build.prop` fingerprint is
   `mirror/silvering/msm8916_64:6.0.1/MMB29M/<recipe commit>:...`.
2. The flashing computer's `fastboot` is platform-tools **35.0.1-11580240**
   (`fastboot --version`; **Stop** on any other version). Its `flash` sends the
   image straight to the partition without erasing first (`do_flash` in that
   release's `fastboot.cpp`); older clients erase ext4 partitions first.
3. The userdata image is built during the session, once fastboot has given the
   partition size, with the flashing computer's own public key
   (`%USERPROFILE%\.android\adbkey.pub`, the half of the key that this `adb`
   uses) and the unit's own labels:

   ```sh
   FILE_CONTEXTS=~/silvering/units/<profile>/ramdisk/file_contexts \
       ./userdata.sh <partition-size:userdata> adbkey.pub userdata.img
   ```

4. Copy `system.img` and, in the session, the new `userdata.img` to the
   flashing computer, each with its sha256, into an empty folder, and check
   **both** hashes there before the first write. Their unpacked sizes come
   from the images themselves (`simg2img`, or the sparse header's
   `total_blks × blk_sz`).

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

   **Thermal protection (Stop until shown).** Silvering has no
   `thermal-engine`. The factory's does not stay up (above), but it may set
   limits before it exits, so that is no proof the unit is safe without it.
   Before any write, show over ADB that the kernel protects the chip on its
   own:

   ```sh
   adb -s <serial> shell cat /sys/module/msm_thermal/parameters/enabled
   adb -s <serial> shell ls /proc/device-tree/soc/qcom,msm-thermal
   adb -s <serial> shell od -An -tx1 /proc/device-tree/soc/qcom,msm-thermal/<property>
   adb -s <serial> shell dmesg | grep -i -E "msm_thermal|tsens"
   ```

   Device-tree values are big-endian 32-bit cells (`od` shows their bytes;
   `cat` does not show numbers). Go on only if `enabled` is `Y` (which alone
   is not proof: the driver sets it before registering its frequency limits
   and keeps it if that fails), the kernel log shows the limits registered
   with no error, and the `qcom,msm-thermal` node sets either a chip reset
   that needs no daemon (`qcom,therm-reset-temp`) or frequency throttling:
   `qcom,limit-temp`, a positive `qcom,freq-step`, and a
   `qcom,freq-control-mask` that covers the CPUs (the driver throttles no CPU
   whose bit is clear, so an absent or zero mask means no throttling). Note
   each value. Anything missing, unreadable or failed: **Stop**. After the first boot, while the owner is
   at the unit, read every `/sys/class/thermal/thermal_zone*/temp` every ten
   seconds through ten minutes of Mirror Home playing a film; power the unit
   off if any zone passes 90 °C (a zone reports degrees or thousandths of a
   degree) or if a reading cannot be taken.
3. **Fastboot by hand.** Power off, hold VOL- while powering on;
   `fastboot devices` shows the unit. Then:

   ```sh
   fastboot -s <serial> getvar partition-size:system
   fastboot -s <serial> getvar partition-size:userdata
   ```

   **Stop** if `system.img`, unpacked, is larger than the system partition.
4. **userdata image** from the size just read; copy and check it (Before the
   cable, steps 3 and 4). Both images are on the flashing computer with
   matching hashes before anything is written.
5. **Write, one command at a time, each only after the command as a whole has
   ended with `Finished` and no `FAILED`:**

   ```sh
   fastboot -s <serial> flash system system.img
   ```

   - Refused by the bootloader's policy before any data is written (CAF LK
     answers `FAILED (remote: 'Partition flashing is not allowed')` and
     similar `not allowed` messages), and no earlier flash succeeded: the unit
     is unchanged; `fastboot reboot` and **Stop**.
   - Any other failure, a cut-off transfer, or an answer that is not clear:
     `system` is in an unknown state, because the bootloader writes the image
     chunk by chunk. Stay in fastboot, do not reboot, and flash a corrected
     `system`.

   Then, only after `system` succeeded:

   ```sh
   fastboot -s <serial> flash userdata userdata.img
   ```

   If this fails for any reason, the unit is no longer unchanged: stay in
   fastboot and flash `userdata` again (or a corrected one); do not reboot
   until it has succeeded. Then `fastboot -s <serial> reboot`.

   Never `flash boot`, `aboot`, `recovery`, `erase`, `format`, `-w` or `oem`.
   Write down each command and its answer.
6. **First boot.** `adb -s <serial> wait-for-device`, `adb -s <serial>
   get-state` answers `device` (not `unauthorized`), and wait until
   `adb -s <serial> shell getprop sys.boot_completed` is `1` and
   `adb -s <serial> shell pm path dev.mirror.repurpose.updater` names the
   supervisor. Keep `adb logcat -b all -d` and `adb shell dmesg` from this
   boot.
7. **Device owner:** `adb -s <serial> shell dpm set-device-owner
   dev.mirror.repurpose.updater/.OtaDeviceAdminReceiver` (as in
   [OTA updates](../docs/ota-updates.md) step 4).
8. **Pair and Wi-Fi.** Mirror Home in `/system` was not installed with
   `adb install -g`, so give it the one permission its Wi-Fi setup needs:
   `adb -s <serial> shell pm grant dev.mirror.repurpose
   android.permission.ACCESS_FINE_LOCATION`. Pair a client through
   `adb -s <serial> forward` to Mirror Home, give it the Wi-Fi network through
   its setup, and confirm the unit's LAN address. Only then `ota.ps1
   provision` (OTA updates step 5) and `ota.ps1 status` over the LAN.
9. **Checks** before the cable comes out: cold boot (power off and on) with ADB
   and Wi-Fi back by themselves, pairing, sleep and wake, audio, an OTA update
   of Mirror Home and a forced rollback (`ota.ps1 push` of a health-failure
   build). `adb tcpip 5555` gives Wi-Fi ADB only until the next restart; a
   lasting way in is still to be chosen.

## If it does not start

`aboot` is untouched: hold VOL- at power-on, `fastboot getvar all`, and flash
a corrected `system` (or a fresh `userdata`). Keep the boot log for the fix.
