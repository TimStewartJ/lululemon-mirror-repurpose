#!/usr/bin/env bash
# Build Silvering inside the pinned container, capped so that other services on
# the build host keep running.
#
#   ./build.sh [lunch target] [make goals...]
#
#   SILVERING_ROOT  sources, out and ccache (default ~/silvering); signed
#                   MirrorHome.apk and MirrorUpdater.apk are taken from its
#                   apps/ folder when present
#   CPUS            CPU cap for the container (default 6)
#   MEMORY          memory cap (default 24g)
#   JOBS            make -j (default CPUS)
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=${SILVERING_ROOT:-$HOME/silvering}
cpus=${CPUS:-6}
memory=${MEMORY:-24g}
jobs=${JOBS:-$cpus}
target=${1:-silvering-userdebug}
shift || true
goals=${*:-systemimage}
image=silvering-build:trusty

[ -d "$root/src/.repo" ] || { echo "no sources in $root/src: run sync.sh first" >&2; exit 1; }
mkdir -p "$root/ccache" "$root/logs"

product=$root/src/device/mirror/silvering
mkdir -p "$product"
rsync -a --delete --exclude apps/prebuilt/ "$here/device/" "$product/"
mkdir -p "$product/apps/prebuilt"
for apk in MirrorHome MirrorUpdater; do
    if [ -f "$root/apps/$apk.apk" ]; then
        cp "$root/apps/$apk.apk" "$product/apps/prebuilt/"
    else
        rm -f "$product/apps/prebuilt/$apk.apk"
    fi
done

docker build -q -t "$image" --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" "$here/docker" >/dev/null

log=$root/logs/build-$(date +%Y%m%d-%H%M%S).log
echo "building $target ($goals) with $cpus CPUs / $memory; log: $log"
docker run --rm --name silvering-build \
    --cpus "$cpus" --cpu-shares 256 --memory "$memory" --memory-swap "$memory" \
    -v "$root/src:/src" -v "$root/ccache:/ccache" \
    "$image" bash -c "
        set -e
        ccache -M 50G >/dev/null
        source build/envsetup.sh >/dev/null
        lunch $target
        make -j$jobs $goals
    " 2>&1 | tee "$log"
exit "${PIPESTATUS[0]}"
