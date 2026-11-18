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
./build.sh                                           # 1. the build alone
./unit-files.py system.tar ~/silvering/src/out/target/product/msm8916_64/system \
    ~/silvering/units/<profile>/unit                 # 2. what the build lacks
UNIT=~/silvering/units/<profile>/unit ./build.sh     # 3. the image
```

`unit-files.py` compares the unit's copy with the build's output (which it
never changes) and keeps what the build cannot make, the unit's kernel
modules, and board configuration where the unit's differs; never apps,
framework jars or security policy, and never a file the transfer could not
read completely. With `UNIT` set, `build.sh` lays those files over a copy of
the build's `/system` after everything else, so they always win (the unit's
kernel modules replace the build's whole set), makes `system.img` from it in
`~/silvering/images/`, and checks that the image holds exactly that tree.