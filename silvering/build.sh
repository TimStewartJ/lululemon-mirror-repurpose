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
#   ALLOW_UNREAD    files the transfer could not read and the image needs:
#                   a file listing the approved paths (the image is made only
#                   if they are exactly those), or 1 for a test image
#   SILVERING_KEYS  the host's signing keys for silvering-* targets (default
#                   $SILVERING_ROOT/keys; made on the first build). Keep a
#                   backup: a unit's next system image must carry the same
#                   platform key, or its system apps no longer start
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
keys=
case "$target" in
    silvering-*) keys=${SILVERING_KEYS:-$root/keys} ;;
    *) if [ -n "${SYSTEM_TAR:-}" ]; then
           echo "SYSTEM_TAR makes a Silvering image: use a silvering-* target" >&2
           exit 1
       fi ;;
esac

[ -d "$root/src/.repo" ] || { echo "no sources in $root/src: run sync.sh first" >&2; exit 1; }
# One build at a time per root: a second one would rewrite the product tree
# under the first.
exec 9>"$root/build.lock"
flock -n 9 || { echo "another build is running in $root" >&2; exit 1; }
# The sources, images and a unit's files hold Qualcomm's and the board maker's
# files, and the keys sign the system: keep them out of this checkout.
if [ -n "$keys" ]; then
    mkdir -p "$keys"
    chmod 700 "$keys"
    # Docker takes a relative path for a named volume.
    keys=$(cd "$keys" && pwd -P)
fi
checkout=$(git -C "$here" rev-parse --show-toplevel 2>/dev/null || true)
for private in "$root" "$keys"; do
    if [ -n "$checkout" ] && [ -n "$private" ]; then
        case "$(cd "$private" && pwd -P)/" in
            "$(cd "$checkout" && pwd -P)/"*) echo "$private is inside the checkout $checkout" >&2; exit 1 ;;
        esac
    fi
done
mkdir -p "$root/ccache" "$root/logs" "$root/images"
docker build -q -t "$image" --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" "$here/docker" >/dev/null

# The host's keys, made once with openssl as AOSP's make_key does (RSA 2048,
# exponent 65537, no password). Android's security policy finds the default
# key as testkey in the same folder, so that name leads to releasekey.
key_names="releasekey platform shared media"
if [ -n "$keys" ]; then
    have=0
    for name in $key_names; do
        if [ -e "$keys/$name.pk8" ] || [ -e "$keys/$name.x509.pem" ]; then have=$((have + 1)); fi
    done
    if [ "$have" -eq 0 ]; then
        echo "making this host's Silvering keys in $keys (back them up)"
        docker run --rm -v "$keys:/keys" "$image" bash -c "
            set -e
            umask 077
            for name in $key_names; do
                openssl genrsa -f4 2048 2>/dev/null > /tmp/key.pem
                openssl req -new -x509 -sha256 -days 10000 -key /tmp/key.pem \
                    -subj \"/O=Silvering/OU=$(hostname)/CN=\$name\" -out /keys/\$name.x509.pem
                openssl pkcs8 -in /tmp/key.pem -topk8 -outform DER -nocrypt -out /keys/\$name.pk8
                rm /tmp/key.pem
            done"
    fi
    for name in $key_names; do
        [ -f "$keys/$name.pk8" ] && [ -f "$keys/$name.x509.pem" ] || {
            echo "$keys lacks $name.pk8 or $name.x509.pem: restore the keys from their backup" >&2
            exit 1
        }
    done
    # A private key that does not belong to its certificate signs apps that
    # Android refuses: check each pair before building.
    docker run --rm -v "$keys:/keys:ro" "$image" bash -c "
        set -e
        for name in $key_names; do
            key=\$(openssl pkcs8 -inform DER -nocrypt -in /keys/\$name.pk8 | openssl rsa -noout -modulus)
            cert=\$(openssl x509 -noout -modulus -in /keys/\$name.x509.pem)
            [ \"\$key\" = \"\$cert\" ] || { echo \"\$name.pk8 does not belong to \$name.x509.pem\" >&2; exit 1; }
        done"
    for ext in x509.pem pk8; do
        alias=$keys/testkey.$ext
        if [ -L "$alias" ] && [ "$(readlink "$alias")" = "releasekey.$ext" ]; then continue; fi
        if [ -e "$alias" ] || [ -L "$alias" ]; then
            echo "$alias is not a link to releasekey.$ext: move it away" >&2
            exit 1
        fi
        ln -s "releasekey.$ext" "$alias"
    done
fi

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
# Silvering's board settings (BoardConfigSilvering.mk) reach the build as
# ANDROID_BUILDSPEC. make does not track compiler flags, so objects compiled
# under other board settings would be linked again as they are: when the
# settings change, build the whole product again (ccache keeps that short).
# The same holds for the keys: not every output that carries a certificate
# (the security policy's mac_permissions.xml) depends on the key files.
buildspec=
board=none
case "$target" in
    silvering-*)
        buildspec=/src/device/mirror/silvering/BoardConfigSilvering.mk
        board=$(cat "$product/BoardConfigSilvering.mk" "$keys"/{releasekey,platform,shared,media}.x509.pem \
            | sha256sum | cut -d' ' -f1) ;;
esac
# The stamp belongs to the product folder lunch picks ($OUT), which other
# targets may share (msm8916_64 records "none"); it is checked and written
# in the container, right after any clean, so a failed build is not cleaned
# again.
board_check="board_stamp=/src/out/.silvering-board-\$(basename \"\$OUT\")
            if [ -d \"\$OUT\" ] && [ \"\$(cat \"\$board_stamp\" 2>/dev/null)\" != $board ]; then
                echo \"board settings changed: building \$OUT again\"
                rm -rf \"\$OUT\"
            fi
            mkdir -p /src/out && echo $board > \"\$board_stamp\""
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

# The build number (in the fingerprint and ro.build.version.incremental) is
# the recipe commit instead of the build's date, so a unit says which recipe
# built its system. The OTA supervisor accepts any build number here
# (android/ota-updater SupportedFirmware): [0-9A-Za-z._-] only. Android keeps
# at most 91 bytes of a property, and the rest of the fingerprint
# (mirror/silvering/msm8916_64:6.0.1/MMB29M/:userdebug/dev-keys) takes 60.
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
key_mount=()
if [ -n "$keys" ]; then key_mount=(-v "$keys:/keys:ro"); fi
in_container() {
    docker run --rm --name silvering-build \
        --cpus "$cpus" --cpu-shares 256 --memory "$memory" --memory-swap "$memory" \
        -v "$root/src:/src" -v "$root/ccache:/ccache" -v "$here:/recipe:ro" \
        ${key_mount[@]+"${key_mount[@]}"} ${extra[@]+"${extra[@]}"} \
        "$image" bash -c "
            set -e
            ccache -M 50G >/dev/null
            export BUILD_NUMBER=$build_number
            ${buildspec:+export ANDROID_BUILDSPEC=$buildspec}
            ${keys:+export SILVERING_KEYS=/keys}
            source build/envsetup.sh >/dev/null
            lunch $target
            $1"
}

log=$root/logs/build-$(date +%Y%m%d-%H%M%S).log
exec > >(tee -a "$log") 2>&1
echo "building $target ($goals, build number $build_number) with $cpus CPUs / $memory; log: $log"
in_container "
            $board_check
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
    case "${ALLOW_UNREAD:-}" in
        "") ;;
        1) allow=(--allow-unread) ;;
        *) [ -f "$ALLOW_UNREAD" ] || { echo "ALLOW_UNREAD=$ALLOW_UNREAD is neither 1 nor a file" >&2; exit 1; }
           allow=(--expect-unread "$(cd "$(dirname "$ALLOW_UNREAD")" && pwd)/$(basename "$ALLOW_UNREAD")") ;;
    esac
    python3 "$here/unit-files.py" ${allow[@]+"${allow[@]}"} "$system_tar" "$product_out/system" "$overlay"
    extra=(-v "$overlay:/unit:ro" -v "$root/images:/images")
    in_container "bash /recipe/stage-image.sh /unit /images/$target-$(date +%Y%m%d-%H%M%S)"
fi