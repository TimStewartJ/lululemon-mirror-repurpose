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
- Heat, read through Mirror Home 2.3.0-dev.31 on 2026-11-24 with
  `tools\install.py thermal --mirror`: `msm_thermal` is built in with
  `enabled` = `Y`, CPUs `0-3`, and seven readable thermal zones (five TSENS
  sensors at 40-52 °C while idle, `pm8916_tz` with a 145 °C critical trip,
  `battery`). The app may not reach the device tree at all (neither
  `/proc/device-tree` nor `/sys/firmware/devicetree/base`, not even the
  node by name), so the device-tree half of the thermal gate, like the
  kernel log, can only be read over USB.

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
   supervisor **1.4.1** or later, built with the bootstrap secret this
   computer keeps (`otactl.py build-supervisor`). Older supervisors do not
   accept a Silvering fingerprint signed with the host's keys (`dev-keys`;
   before 1.4.0, any but the factory's), so OTA and rollback would fail on
   Silvering. The image's `build.prop` fingerprint is
   `mirror/silvering/msm8916_64:6.0.1/MMB29M/<recipe commit>:userdebug/dev-keys`,
   and the build log shows `stage-image.sh`'s signer check (`verified signers
   of N apps`: every app verified, none with an AOSP public key, the host's
   platform key in the framework and security policy). The host's keys in `~/silvering/keys` are backed up before the
   write: every later system image for this unit needs the same platform key.
2. The flashing computer's `fastboot` is platform-tools **35.0.1-11580240**
   (`fastboot --version`; **Stop** on any other version). Its `flash` sends the
   image straight to the partition without erasing first (`do_flash` in that
   release's `fastboot.cpp`); older clients erase ext4 partitions first.
3. The userdata image is built during the session, once fastboot has given the
   partition size, with the flashing computer's own public key
   (`%USERPROFILE%\.android\adbkey.pub`, the half of the key that this `adb`
   uses) and the unit's own labels. Give the size with `0x`, as
   `install.py plan` prints it (CAF's bootloader may send it in hex without
   `0x`, which `userdata.sh` would read as decimal); the script writes
   `userdata.img.sha256` beside the image:

   ```sh
   FILE_CONTEXTS=~/silvering/units/<profile>/ramdisk/file_contexts \
       ./userdata.sh <partition-size:userdata, 0x...> adbkey.pub userdata.img
   ```

4. Copy `system.img` and, in the session, the new `userdata.img` to the
   flashing computer, each with its sha256 file, into an empty folder, and
   check **both** hashes there before the first write:
   `python tools\install.py plan --images <folder>` hashes each image and
   reads it through the same open file, walks every chunk of the sparse file
   (a cut-off copy is refused), checks that each is a whole ext4 image with a
   consistent superblock (`system` may carry the build's dm-verity metadata
   and hash tree after it, `userdata` nothing), and refuses a folder that
   holds anything else, so no other image can be written by mistake. That
   the file systems are sound was shown when they were built, before their
   hashes were written (`stage-image.sh` reads every file of `system.img`
   back with debugfs; `userdata.sh` runs `check-ext4.sh`, e2fsck -fn); the
   hashes tie these copies to those images.

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
   `cat` does not show numbers). `python tools\install.py thermal --serial
   <serial>` reads all of this (and the kernel log) and checks it as below.
   It never answers a plain pass yet: which kernel messages prove the limits
   registered is not known for this board, so its best answer is `review`
   (exit 5: every automatic check passed; read the kernel lines it lists).
   `--mirror CONFIG` reads the same over Wi-Fi through Mirror Home
   2.3.0-dev.30 or later, without the kernel log, so its best answer is
   `incomplete` (exit 4); a Stop is exit 3. Go on only if `enabled` is `Y` (which alone
   is not proof: the driver sets it before registering its frequency limits
   and keeps it if that fails), the kernel log shows the limits registered
   with no error, and the `qcom,msm-thermal` node sets either a chip reset
   that needs no daemon (`qcom,therm-reset-temp`) or frequency throttling:
   `qcom,limit-temp`, a positive `qcom,freq-step`, and a
   `qcom,freq-control-mask` that covers the CPUs (the driver throttles no CPU
   whose bit is clear, so an absent or zero mask means no throttling). Either
   way the node must also carry what the driver's probe reads first,
   `qcom,sensor-id`, `qcom,poll-ms` (10-1000 ms), `qcom,limit-temp`,
   `qcom,temp-hysteresis` and `qcom,freq-step`: without any one of them the
   CAF driver gives up before setting up the reset or the throttling. Note
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
   `python tools\install.py plan --images <folder> --serial <serial>` asks
   the bootloader exactly these two questions and nothing else, checks the
   fastboot client's version and the images (Before the cable, step 4), and
   prints the `userdata.sh` command with the size in hex.
4. **userdata image** from the size just read; copy and check it (Before the
   cable, steps 3 and 4). Both images are on the flashing computer with
   matching hashes before anything is written: `install.py plan` answers
   `ready` (exit 0) only then, with a userdata file system that leaves the
   partition's last 16 KiB free, and lists the two writes below with the
   full path of the fastboot whose version it checked.
5. **Write, one command at a time, each only after the command as a whole has
   ended with `Finished` and no `FAILED`:**

   ```sh
   fastboot -s <serial> flash system system.img
   ```

   - Refused by the bootloader's policy before any data is written (CAF LK
     answers exactly `FAILED (remote: 'Partition flashing is not allowed')`
     or `FAILED (remote: 'Critical partition flashing is not allowed')`),
     and no earlier attempt left an installation incomplete (no earlier
     flash was cut off or failed once it had begun writing, and `system` was
     not written without a successful `userdata` after it): the unit is
     unchanged; `fastboot reboot` and **Stop**. The same holds when fastboot
     stops before it prints its first `Writing 'system'` (a failed
     `Sending`, a file it cannot load): sending only fills the bootloader's
     memory. If an earlier attempt left an installation incomplete, a
     refusal changes nothing about it: stay in fastboot.
   - Any other failure, a cut-off transfer, or an answer that is not clear:
     `system` is in an unknown state, because the bootloader writes the image
     chunk by chunk. Stay in fastboot, do not reboot, and flash a corrected
     `system`.

   Then, only after `system` succeeded:

   ```sh
   fastboot -s <serial> flash userdata userdata.img
   ```

   If this fails for any reason, the unit is no longer unchanged: stay in
   fastboot and flash `system` and `userdata` again (or corrected ones); do
   not reboot until both have succeeded. Then `fastboot -s <serial> reboot`.

   `python tools\install.py flash --images <folder> --serial <serial>` does
   this step: it runs the plan again and goes on only on `ready` with this
   unit the only one `fastboot devices` lists, shows the two writes with each
   image's sha256, lists what it cannot check itself (the last gate over ADB,
   the thermal review, the way back by hand, the approved unread list), and
   writes nothing until a person at the terminal types exactly `write system
   and userdata` (without a terminal it never asks). It then copies both
   images into a new folder beside the images folder, hashing each copy and
   reading it back, and fastboot writes from those copies, so the bytes sent
   are the bytes checked. Before each write it checks again that the unit is
   still the only fastboot unit and the copies are unchanged. It reads each
   answer from fastboot's own words (every piece sent and written,
   `Finished`, no `FAILED` outside the bootloader's INFO lines; a refusal
   only as one of the two exact answers above to the first write) and
   restarts the unit only after both writes succeeded. fastboot runs in a
   process group of its own, so Ctrl-C never stops a write under way: the
   stage lets the step finish and stops before the next one.

   Every command sent to the unit and every answer goes to the unit's
   journal on this computer, `~/.mirror-install/flash-<hash of the
   serial>.jsonl` in the home folder of the person running it (under
   `sudo`, the person who ran `sudo`), whichever folder the images come
   from; run every attempt on a unit from the same account. Each line is
   synced, and a new journal's name too, before anything is sent; this
   unit's serial is written as `<unit>`, others as `<other>`. Each run first
   replays the journal, so its advice and exit code account for what every
   earlier run left on the unit; if a line cannot be read (one cut off when
   a run was killed), the stage never calls the unit unchanged. A second run
   for the same unit is refused while one is working, and every step waits
   for no other fastboot program to be running on the computer (one left by
   a run that was killed, say). Ctrl-C is honoured after the checks before
   each step, so a step is either not announced at all or runs to its end.
   Exit 0: written and restarting; 1: the journal could not be used or
   another run holds the unit (nothing sent); 3: nothing is written and the
   journal records no unfinished write; 6: a partition may hold part of an
   image, or only `system` is written (keep the unit in fastboot and run the
   stage again: it writes `system`, then `userdata`); 7: both written, but
   the restart was not seen (check the cable, `fastboot devices` and `adb
   devices` before any power action); 8: written and restarting, but the
   journal could not be completed.

   Never `flash boot`, `aboot`, `recovery`, `erase`, `format`, `-w` or `oem`.
   Write down each command and its answer. A unit whose `boot` has been
   overwritten stays in fastboot: its locked bootloader starts only a boot
   image signed for the board, which this project neither has nor hosts (an
   owner who wrote `boot` while trying other builds has reported exactly
   that). The installer builds no write but these two (`flash_arguments` in
   `tools/install.py`) and the restart after them.
6. **First boot.** `adb -s <serial> wait-for-device`, `adb -s <serial>
   get-state` answers `device` (not `unauthorized`), and wait until
   `adb -s <serial> shell getprop sys.boot_completed` is `1` and
   `adb -s <serial> shell pm path dev.mirror.repurpose.updater` names the
   supervisor. Keep `adb logcat -b all -d` and `adb shell dmesg` from this
   boot.

   **Then, before anything else, check what was written:**
   `python tools\install.py verify --images <folder> --serial <serial>`.
   Nothing between this computer and the partition checks the bytes end to
   end (fastboot sends them, the bootloader writes them), and the project's
   flashing computer has flipped single bits in copies of `system.img`
   (2026-11-26: 5 of 45 copies in memory). The board's boot image keeps
   `ro.debuggable=0` and its own SELinux policy, so adb's shell can read
   neither the partition nor the files that policy keeps from it; the stage
   compares the mounted `/system` with `system.img` entry by entry instead:
   inode, type and mode, owner, links, size, modification time, symlink
   target and SELinux label, and every regular file's sha1, hashed on the
   unit so only digests cross the cable. It reads only, through `adb
   exec-out`, and needs ADB, not a finished boot. Every answer must account
   for the same names (find, and toolbox `ls -a` a second way), and the
   kernel log must show no ext4 error on `/system`'s device during the walks
   (one logged before them is shown for review), since find and ls take a
   read error while listing a folder for its end. Exit 0: every entry is as
   written; no unit reaches it today, because no tool on it prints file
   capabilities, so 5 is the best answer. 5 (review): no difference, but
   what is listed was not compared: go on only if the entries are what the
   factory policy keeps from adb's shell (daemons such as `wpa_supplicant`,
   folders such as `lost+found`) and not anything Mirror Home or the
   supervisor needs. A read that fails with `Structure needs cleaning` or
   `Not a data message`, or with an I/O error, is a difference; one that
   fails for any other reason but `Permission denied` concludes nothing. 3
   (Stop) means stop the session, not necessarily write again: `/system`
   differs in two readings that rest on the same evidence. Do what the
   stage advises. It advises writing again (`adb -s <serial> reboot
   bootloader`, `install.py flash` with the same folder, verify again) only
   when content or metadata differ, an entry is missing or extra, or ext4
   reports damage itself (`Structure needs cleaning`, `Not a data
   message`), and this computer's journal for the unit records the
   `system.img` and `userdata.img` in this folder as its last complete
   installation. A named I/O error, a read that stops without a reason, or
   an ext4 error in the kernel log, alone, calls for no write: failing
   storage gives the same. Otherwise write nothing until verify has been
   run with the folder that was written to the unit. 4: nothing was
   compared (no unit runs Android with ADB authorized; in fastboot, follow
   what `flash` said last: its exits 0, 7 and 8 mean both partitions are
   written). 1: nothing
   concluded (an answer could not be accounted for, a read failed for a
   reason unrelated to the partition, or two readings disagreed): run it
   again. `userdata` is not checked this way: Android writes to it from the
   first boot.
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
If ADB comes up but the boot never completes, run `install.py verify` first:
a `system` that differs from its image is the first thing to rule out.
