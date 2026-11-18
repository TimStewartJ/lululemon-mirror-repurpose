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
./build.sh                    # msm8916_64-userdebug, goal systemimage
./build.sh <lunch target> <make goals...>
```

The build runs in an Ubuntu 14.04 container (OpenJDK 7, make 3.81, Python 2.7,
as Android 6 requires), capped by default at 6 CPUs and 24 GB of memory with a
low CPU weight, so other services on the host keep running. `CPUS`, `MEMORY`
and `JOBS` change the caps. Logs go to `~/silvering/logs/`.
