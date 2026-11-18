#!/usr/bin/env bash
# Make the userdata image a unit is first flashed with: an empty ext4 file
# system the size of the unit's userdata partition, holding only the owner's
# ADB key.
#
# The board's adbd lives in its own boot image, which Silvering keeps, and it
# asks before it answers a computer; a MIRROR has no touch screen to say yes
# on. adbd also reads /data/misc/adb/adb_keys, so a key placed there lets the
# owner's computer in from the first boot.
#
#   ./userdata.sh <partition size> <adb public key> [output image]
#
#   partition size  in bytes, as `fastboot getvar partition-size:userdata`
#                   prints it (0x... is fine)
#   adb public key  the owner's computer's adbkey.pub (~/.android/adbkey.pub)
#   output image    default $SILVERING_ROOT/images/userdata-<time>.img
#   FILE_CONTEXTS   SELinux labels (default: the build's root/file_contexts;
#                   the unit's own, from its ramdisk, is better where read:
#                   ~/silvering/units/<profile>/ramdisk/file_contexts)
#   USB_CONFIG      USB functions for the first boot (default mtp,adb, what
#                   a MIRROR that ADB already works with uses; empty: leave
#                   it to build.prop, which on msm8916 asks for
#                   diag,serial_smd,rmnet_bam,adb)
#   TARGET          lunch target whose host tools to use (default
#                   silvering-userdebug)
#
# The board's fstab marks userdata encryptable=footer: the last 16 KiB of the
# partition stay outside the file system for that footer.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=${SILVERING_ROOT:-$HOME/silvering}
target=${TARGET:-silvering-userdebug}
image=silvering-build:trusty
footer=16384

[ $# -ge 2 ] || { sed -n '2,28p' "$0" >&2; exit 2; }
case "$1" in
    0x*|0X*|[0-9]*) ;;
    *) echo "partition size must be a number of bytes, not $1" >&2; exit 2 ;;
esac
size=$(( $1 ))
key=$2
# Smaller than this is not a MIRROR's userdata partition (about 5 GiB).
[ "$size" -ge $((1024 * 1024 * 1024)) ] || { echo "partition size $size is under 1 GiB" >&2; exit 1; }
length=$(( (size - footer) / 4096 * 4096 ))

# An ADB public key is one line: base64 of a 524-byte RSA key, then a name.
python3 - "$key" <<'EOF'
import base64, binascii, sys
try:
    with open(sys.argv[1], "rb") as f:
        lines = f.read().decode("ascii").strip().splitlines()
    assert len(lines) == 1
    assert len(base64.b64decode(lines[0].split(" ")[0], validate=True)) == 524
except (OSError, UnicodeDecodeError, AssertionError, binascii.Error, ValueError):
    sys.exit(f"{sys.argv[1]} is not an ADB public key (adbkey.pub)")
EOF

out_marker=$root/src/out/.silvering-product-out-$target
[ -f "$out_marker" ] || { echo "no build of $target in $root/src: run build.sh first" >&2; exit 1; }
product_out=$root/src/$(sed 's|^/src/||' "$out_marker")
host_bin=$root/src/out/host/linux-x86/bin
[ -x "$host_bin/make_ext4fs" ] || { echo "no make_ext4fs in $host_bin" >&2; exit 1; }
contexts=${FILE_CONTEXTS:-$product_out/root/file_contexts}
[ -f "$contexts" ] || { echo "no file_contexts at $contexts" >&2; exit 1; }
grep -q '^/data/misc/adb(' "$contexts" || { echo "$contexts has no label for /data/misc/adb" >&2; exit 1; }
usb_config=${USB_CONFIG-mtp,adb}
case "$usb_config" in
    "") ;;
    *[!a-z0-9_,]*|*adb*[!a-z0-9_,]*) echo "USB_CONFIG=$usb_config is not a list of USB functions" >&2; exit 1 ;;
    *adb*) ;;
    *) echo "USB_CONFIG=$usb_config has no adb: ADB would not answer" >&2; exit 1 ;;
esac

out=${3:-$root/images/userdata-$(date +%Y%m%d-%H%M%S).img}
mkdir -p "$(dirname "$out")"
out=$(cd "$(dirname "$out")" && pwd)/$(basename "$out")
[ ! -e "$out" ] || { echo "$out already exists" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/stage/misc/adb" "$work/out"
tr -d '\r' < "$key" | sed -e '$a\' > "$work/stage/misc/adb/adb_keys"
cp "$contexts" "$work/file_contexts"
# Owners and modes as the board's init.rc makes them (system 1000, shell
# 2000, misc 9998).
cat > "$work/fs_config" <<'EOF'
data/lost+found 0 0 0700
data/misc 1000 9998 01771
data/misc/adb 1000 2000 02750
data/misc/adb/adb_keys 1000 2000 0640
EOF
# A persistent property overrides build.prop. Android 6 takes only files
# owned by root, open to no one else, holding the value without a newline.
if [ -n "$usb_config" ]; then
    mkdir -p "$work/stage/property"
    printf '%s' "$usb_config" > "$work/stage/property/persist.sys.usb.config"
    cat >> "$work/fs_config" <<'EOF'
data/property 0 0 0700
data/property/persist.sys.usb.config 0 0 0600
EOF
fi

docker build -q -t "$image" --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" "$here/docker" >/dev/null
docker run --rm -v "$root/src/out/host:/host:ro" -v "$work:/work" "$image" \
    /host/linux-x86/bin/make_ext4fs -s -T 0 -l "$length" -a data \
        -S /work/file_contexts -C /work/fs_config /work/out/userdata.img /work/stage

mv "$work/out/userdata.img" "$out"
echo "userdata: $out ($length bytes of a $size-byte partition; sparse)"
