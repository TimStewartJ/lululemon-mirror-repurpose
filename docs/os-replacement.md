# Owner-controlled OS replacement plan

## Recommendation

The long-term target should be a small mainline-Linux kiosk image rather than a
modern certified Android build. MSM8916/APQ8016 has broad upstream Linux
support, including the Adreno 306 through Freedreno, while this board has only
1 GB RAM, an 8 GB eMMC, and a legacy Android 3.10 kernel.

Android 7.1.1 remains the lowest-risk Android alternative. Inforce officially
released a 7.1.1/3.10.49 BSP for IFC6309 with LVDS support:

- [Inforce Android Nougat release](https://ir.penguinsolutions.com/news/news-details/2018/Android-Nougat-7-for-Inforce-Snapdragon-410-Based-Platforms/default.aspx)

Mainline background:

- [postmarketOS MSM8916 support](https://wiki.postmarketos.org/wiki/Qualcomm_Snapdragon_410/412_(MSM8916))
- [AOSP common-kernel compatibility](https://source.android.com/docs/core/architecture/kernel/android-common)

## Current hard blockers

- LK reports `Device unlocked: false` and `get_unlock_ability: 0`.
- Qualcomm secure boot is enabled.
- EDL/Sahara identifies the board, but no matching signed Firehose programmer is
  available for the observed OEM public-key hash.
- UID 1000 execution does not permit raw boot/recovery writes and does not
  bypass image-signature verification.
- The device has one boot/system layout, not Android A/B slots.

Do not flash generic DragonBoard/MSM8916 images. Root alone is insufficient if
the bootloader still refuses the replacement boot image.

## Non-writing boot-trust probes

The following probes were executed on 2026-08-23. No partition read, program,
patch, erase, or flash command was sent.

### EDL Firehose signature test

Sahara reported:

```text
HWID:    007060e100000000
PK hash: 35ac01e7ee8478261aea5134e07e45cb6c5621d42716c15bb10dee0c53d65759
```

The only public bkerler loader with the exact HWID was:

```text
007060e100000000_cc3153a802939b90_fhprg_peek.bin
SHA-256: cb4aa64c1e34c1d914b5aec3b403826beb74880c8ff85b0d79094fcd9ed0c4df
```

The filename's second field is the loader public-key hash prefix. It does not
match this device's fused `35ac01e7ee847826` prefix. A forced
`nop --skipstorageinit` test confirmed the behavior: PBL requested the ELF
segments, stopped responding immediately after the signed header, and never
entered Firehose. The `nop` and storage initialization were never reached.
The device remained in `05c6:9008` until main power was disconnected.

### Fastboot RAM-boot test

The official msm8916-mainline lk2nd 23.1 image was downloaded from its GitHub
release and verified:

```text
lk2nd-msm8916.img
SHA-256: 8f5f788b45b96447517ff121f20841e6bfc95a5d7c4b48fdedb855d90f593144
```

Only the non-writing command was used:

```text
fastboot boot lk2nd-msm8916.img
```

LK accepted the USB upload but rejected execution:

```text
FAILED (remote: 'bootimage: incomplete or not signed')
```

`fastboot reboot` returned cleanly to Mirror Home 1.0. These results prove that
both available entry points enforce the OEM trust chain. Repeating generic
loader or unsigned boot-image tests cannot create a boot path.

### Official BSP availability

Penguin/Inforce confirms that IFC6309 Android 7.1.1 V3.0 exists, but directs
users to its authenticated TechWeb portal. The portal currently returns HTTP
503, public archives contain no firmware package or release-note capture, and
no reputable public mirror exposes the BSP. The next meaningful probe requires
an official package or programmer with the device's `35ac01e7ee847826` key
prefix.

## Phase 0: preserve recovery

Complete before the first write:

1. Obtain the IFC6309 V3.0 BSP and release notes from Penguin/Inforce.
2. Acquire a signed Firehose for HWID `007060e100000000` and verify its signer
   against the device before sending any program command.
3. Read and hash every eMMC partition through a read-only path.
4. Preserve GPT primary/backup tables, boot, recovery, aboot, modem, persist,
   system, and userdata metadata.
5. Rehearse restoring one disposable partition and verify byte identity.

The hardware force-USB switch and USB `05c6:9008` EDL mode remain the last
recovery path.

## Phase 1: safest signed upgrade

Before pursuing an exploit, determine whether the official IFC6309 7.1.1 boot
and recovery images are accepted by this unit's boot key:

1. compare image board IDs, device trees, and certificate metadata offline,
2. use `fastboot boot` only if the bootloader accepts a non-writing test command,
3. otherwise test only after complete partition backups and a matching loader,
4. keep the stock boot and system partitions untouched until LVDS, USB, Wi-Fi,
   and ADB work from a test image.

An official signed image is the only realistic shortcut to Nougat.

## Phase 2: mainline bring-up

Start from the DragonBoard 410c/MSM8916 mainline support and create an
IFC6309-specific device tree:

- APQ8016 CPU, clocks, regulators, eMMC, USB, and thermal zones,
- LVDS bridge/panel timings and portrait orientation,
- Adreno 306/Freedreno firmware,
- Wi-Fi/Bluetooth firmware, NVRAM, and MAC handling,
- audio codec, amplifier GPIOs, microphone routing, and UCM profile,
- camera and hardware-switch GPIOs where useful.

Extract the stock DTB and panel parameters only after a verified read path is
available. Do not guess power rails or panel timings.

MSM8916 postmarketOS normally boots `lk2nd` from an Android boot partition and
places the root filesystem on userdata or SD. On this unit, lk2nd itself must
first pass the locked bootloader, so it cannot be installed until the boot-trust
blocker is solved.

## Phase 3: non-destructive test layout

1. Keep the stock eMMC boot/system/recovery images archived and initially
   unchanged.
2. Boot the experimental root filesystem from microSD where possible.
3. Use a dedicated test boot/recovery partition only after proving that the
   bootloader accepts the image.
4. Store two versioned root filesystems plus a small persistent configuration
   partition to emulate A/B rollback.
5. Make the boot selection automatically revert after repeated failed health
   checks.

## Hardware acceptance gates

Advance only when each gate has a recorded rollback:

1. serial/USB console and deterministic reboot,
2. eMMC and read-only stock-partition access,
3. stable LVDS output at the panel's native mode,
4. GPU acceleration without restart-inducing faults,
5. Wi-Fi reconnect and Bluetooth provisioning,
6. speaker/microphone with volume forced low during testing,
7. browser kiosk, Media3-equivalent playback, FCast v4, and watchdog,
8. 24-hour thermal/memory soak,
9. failed-update and power-loss rollback.

## Final Linux appliance

Use a read-only root filesystem with:

- NetworkManager first-run hotspot/captive portal,
- Cage or another single-app Wayland compositor,
- WPE WebKit or Chromium kiosk rendering,
- the official current FCast receiver stack,
- PipeWire with an IFC6309 UCM profile,
- signed image manifests and two rootfs slots,
- a hardware-switch recovery target,
- an unprivileged Mirror UI/control service.

Once stable, sign the owner-controlled boot chain and relock only if the
bootloader supports an owner key. Until then, physical recovery access remains
mandatory.
