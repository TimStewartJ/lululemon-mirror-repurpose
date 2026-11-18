#!/usr/bin/env bash
# Build Silvering inside the pinned container, capped so that other services on
# the build host keep running.
#
#   ./build.sh [lunch target] [make goals...]
#
#   SILVERING_ROOT  sources, out and ccache (default ~/silvering); signed
#                   MirrorHome.apk and MirrorUpdater.apk are taken from its
#                   apps/ folder when present
#   UNIT            a unit-files.py output folder: lay that unit's files over
#                   the build and write the image to $SILVERING_ROOT/images/
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
if [ $# -gt 0 ]; then shift; fi
goals=${*:-systemimage}
image=silvering-build:trusty

[ -d "$root/src/.repo" ] || { echo "no sources in $root/src: run sync.sh first" >&2; exit 1; }
# One build at a time per root: a second one would rewrite the product tree
# under the first.
exec 9>"$root/build.lock"
flock -n 9 || { echo "another build is running in $root" >&2; exit 1; }
# The sources, images and a unit's files hold Qualcomm's and the board maker's
# files: keep them out of this checkout.
checkout=$(git -C "$here" rev-parse --show-toplevel 2>/dev/null || true)
for private in "$root" "${UNIT:-}"; do
    if [ -n "$checkout" ] && [ -n "$private" ]; then
        case "$(cd "$private" && pwd -P)/" in
            "$(cd "$checkout" && pwd -P)/"*) echo "$private is inside the checkout $checkout" >&2; exit 1 ;;
        esac
    fi
done
mkdir -p "$root/ccache" "$root/logs" "$root/images"

product=$root/src/device/mirror/silvering
mkdir -p "$product"
rsync -a --delete --exclude apps/prebuilt/ "$here/device/" "$product/"
rm -rf "$product/apps/prebuilt"
mkdir -p "$product/apps/prebuilt"
for apk in MirrorHome MirrorUpdater; do
    if [ -f "$root/apps/$apk.apk" ]; then cp "$root/apps/$apk.apk" "$product/apps/prebuilt/"; fi
done
# Android 6 does not unpack native libraries of an app that ships in /system,
# so Mirror Home's arm64 libraries are installed beside it as files.
if [ -f "$product/apps/prebuilt/MirrorHome.apk" ]; then
    python3 - "$product/apps/prebuilt" <<'EOF'
import os, sys, zipfile
folder = sys.argv[1]
os.makedirs(os.path.join(folder, "MirrorHome-lib"))
with zipfile.ZipFile(os.path.join(folder, "MirrorHome.apk")) as apk:
    for name in apk.namelist():
        if name.startswith("lib/arm64-v8a/") and name.endswith(".so"):
            with open(os.path.join(folder, "MirrorHome-lib", os.path.basename(name)), "wb") as f:
                f.write(apk.read(name))
EOF
fi

# A module whose input went away (an APK left out, a library dropped) leaves
# its installed files in out/; when the product's inputs change, clear the
# installed tree so that it holds only what this build makes.
inputs=$( (cd "$product" && find . -type f -print0 | sort -z | xargs -0 sha256sum) | sha256sum | cut -d' ' -f1)
stamp=$root/src/out/.silvering-inputs-$target
clean=
if [ "$(cat "$stamp" 2>/dev/null)" != "$inputs" ]; then
    clean="make installclean"
fi
# Until this build succeeds, out/ may hold files of other inputs.
rm -f "$stamp"

unit_mount=()
stage=
if [ -n "${UNIT:-}" ]; then
    [ -f "$UNIT/unit.json" ] || { echo "UNIT=$UNIT has no unit.json: run unit-files.py first" >&2; exit 1; }
    case " $goals " in *" systemimage "*) ;; *) goals="$goals systemimage" ;; esac
    dest=/images/$target-$(date +%Y%m%d-%H%M%S)
    unit_mount=(-v "$(cd "$UNIT" && pwd):/unit:ro" -v "$root/images:/images")
    stage="bash /recipe/stage-image.sh /unit $dest"
fi

docker build -q -t "$image" --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" "$here/docker" >/dev/null

log=$root/logs/build-$(date +%Y%m%d-%H%M%S).log
echo "building $target ($goals) with $cpus CPUs / $memory; log: $log"
set +e
docker run --rm --name silvering-build \
    --cpus "$cpus" --cpu-shares 256 --memory "$memory" --memory-swap "$memory" \
    -v "$root/src:/src" -v "$root/ccache:/ccache" -v "$here:/recipe:ro" "${unit_mount[@]}" \
    "$image" bash -c "
        set -e
        ccache -M 50G >/dev/null
        source build/envsetup.sh >/dev/null
        lunch $target
        $clean
        make -j$jobs $goals
        $stage
    " 2>&1 | tee "$log"
status=${PIPESTATUS[0]}
set -e
if [ "$status" -eq 0 ]; then
    echo "$inputs" > "$stamp"
fi
exit "$status"
