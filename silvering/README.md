# Silvering image recipe

Silvering is this project's rebuilt Android 6.0.1 for MIRROR units, with
Mirror Home built in. It writes only `system` and `userdata` and keeps the
board's own signed boot image and kernel (see [board profiles](../profiles/README.md)).

**Work in progress.** These files fetch the sources and build them in a pinned
container. No Silvering image has been built or accepted on a unit yet.

## Where the sources come from

The factory MIRROR image (`IFC-6309-2.0-MIR`, MirrorOS 1.5.1) was built from
Qualcomm's public CAF release **LA.BR.1.2.7-01010-8x16.0** (Android 6.0.1,
security patch 2016-02-01): that tag is embedded in the libraries of its
`/system`. [`manifest/default.xml`](manifest/default.xml) is that release's
manifest from CodeLinaro, which now hosts the former CodeAurora repositories,
with every project pinned to a commit. Building the same release means the
board's own Qualcomm libraries fit the rebuilt framework.

Qualcomm's proprietary parts (`vendor/qcom/proprietary`) are not public and
this project never hosts them. They come from the owner's own unit at build
time.

## Requirements

A Linux host with Docker, git, curl and Python 3.6 or newer, about 60 GB free
for the sources and 60 GB more for a build.

## Fetch the sources

```sh
./sync.sh                     # into ~/silvering/src; SILVERING_ROOT changes it
```

Every project is fetched shallow at its pinned commit (about 25 GB).

## Build

```sh
./build.sh                    # silvering-userdebug, goal systemimage
./build.sh <lunch target> <make goals...>
```

Signed `MirrorHome.apk` and `MirrorUpdater.apk` in `~/silvering/apps/` are
built into the image; `msm8916_64-userdebug` builds Qualcomm's plain product
for comparison.

The build runs in an Ubuntu 14.04 container (OpenJDK 7, make 3.81, Python 2.7,
as Android 6 requires), capped by default at 6 CPUs and 24 GB of memory with a
low CPU weight, so other services on the host keep running. `CPUS`, `MEMORY`
and `JOBS` change the caps. Logs go to `~/silvering/logs/`.

## The board's own files

The image needs the board's own files: Qualcomm's libraries, daemons and
firmware, the kernel modules for the board's kernel, and its audio, Wi-Fi and
input configuration. They come from a copy of the unit's `/system` (Mirror
Home's device-files transfer writes it as a tar) and stay private.

```sh
SYSTEM_TAR=~/silvering/units/<profile>/system.tar ./build.sh
```

After the build, `unit-files.py` compares the unit's copy with exactly that
build's output (which it never changes) and keeps what the build cannot make,
the unit's kernel modules (the whole set, which must have been read
completely), and board configuration where the unit's differs; never apps,
framework jars or security policy, and never a file the transfer could not
read completely. Files the transfer could not read at all are listed in
`unit.json`; the build stops on them unless `ALLOW_UNREAD=1`. `stage-image.sh`
then lays the unit's files over a copy of the build's `/system`, gives them
the owners and modes they have on the unit, makes `system.img` in
`~/silvering/images/`, and checks that the image holds exactly that tree with
those owners and modes. The overlay is kept in `~/silvering/overlays/`.

## userdata and the first flash

`userdata.sh <partition size> <adbkey.pub>` makes the userdata image a unit is
first flashed with: an empty ext4 file system the size of the partition (less
the 16 KiB footer the board's fstab reserves) holding only the owner's ADB
key, labelled and owned as the board's init makes `/data/misc/adb`. The
session itself is in [FIRST-FLASH.md](FIRST-FLASH.md).