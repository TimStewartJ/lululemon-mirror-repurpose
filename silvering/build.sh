#!/usr/bin/env bash
# Build Silvering inside the pinned container, capped so that other services on
# the build host keep running.
#
#   ./build.sh [lunch target] [make goals...]
#
#   SILVERING_ROOT  sources, out and ccache (default ~/silvering); signed
#                   MirrorHome.apk and MirrorUpdater.apk are taken from its
#                   apps/ folder when present
#   SYSTEM_TAR      a copy of a unit's /system (Mirror Home's device-files
#                   transfer): take that unit's files against this build and
#                   write system.img to $SILVERING_ROOT/images/
#   ALLOW_UNREAD    1: build although the transfer could not read some files
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
for private in "$root"; do
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

system_tar=${SYSTEM_TAR:-}
if [ -n "$system_tar" ]; then
    [ -f "$system_tar" ] || { echo "SYSTEM_TAR=$system_tar does not exist" >&2; exit 1; }
    system_tar=$(cd "$(dirname "$system_tar")" && pwd)/$(basename "$system_tar")
    for goal in systemimage fs_config; do
        case " $goals " in *" $goal "*) ;; *) goals="$goals $goal" ;; esac
    done
fi

docker build -q -t "$image" --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" "$here/docker" >/dev/null

# The build number (in the fingerprint and ro.build.version.incremental) is
# the recipe commit instead of the build's date, so a unit says which recipe
# built its system. The OTA supervisor accepts any build number here
# (android/ota-updater SupportedFirmware): [0-9A-Za-z._-] only. Android keeps
# at most 91 bytes of a property, and the rest of the fingerprint
# (mirror/silvering/msm8916_64:6.0.1/MMB29M/:userdebug/test-keys) takes 61.
build_number=$(git -C "$here" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)
if [ "$build_number" != unknown ] &&
        [ -n "$(git -C "$here" status --porcelain --untracked-files=normal -- . 2>/dev/null)" ]; then
    build_number=$build_number.modified
fi
if [ ${#build_number} -gt 30 ] || ! [[ $build_number =~ ^[0-9A-Za-z._-]+$ ]]; then
    echo "build number '$build_number' does not fit the fingerprint" >&2
    exit 1
fi

extra=()
in_container() {
    docker run --rm --name silvering-build \
        --cpus "$cpus" --cpu-shares 256 --memory "$memory" --memory-swap "$memory" \
        -v "$root/src:/src" -v "$root/ccache:/ccache" -v "$here:/recipe:ro" ${extra[@]+"${extra[@]}"} \
        "$image" bash -c "
            set -e
            ccache -M 50G >/dev/null
            export BUILD_NUMBER=$build_number
            source build/envsetup.sh >/dev/null
            lunch $target
            $1"
}

log=$root/logs/build-$(date +%Y%m%d-%H%M%S).log
exec > >(tee -a "$log") 2>&1
echo "building $target ($goals, build number $build_number) with $cpus CPUs / $memory; log: $log"
in_container "
            $clean
            # build.prop depends on the build's makefiles only, not on the
            # build number: make it again so that it carries this one.
            rm -f \$OUT/obj/ETC/system_build_prop_intermediates/build.prop
            make -j$jobs $goals
            echo \$OUT > /src/out/.silvering-product-out-$target"
echo "$inputs" > "$stamp"

# With the unit's /system: take its files against exactly this build, then
# make the image from both.
if [ -n "$system_tar" ]; then
    product_out=$root/src/$(sed 's|^/src/||' "$root/src/out/.silvering-product-out-$target")
    overlay=$root/overlays/$target
    allow=()
    if [ "${ALLOW_UNREAD:-}" = 1 ]; then allow=(--allow-unread); fi
    python3 "$here/unit-files.py" ${allow[@]+"${allow[@]}"} "$system_tar" "$product_out/system" "$overlay"
    extra=(-v "$overlay:/unit:ro" -v "$root/images:/images")
    in_container "bash /recipe/stage-image.sh /unit /images/$target-$(date +%Y%m%d-%H%M%S)"
fi