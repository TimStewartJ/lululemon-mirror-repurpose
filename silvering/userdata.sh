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
#   partition size  in bytes: 0x... as `install.py plan` gives it, or decimal.
#                   CAF's bootloader may send the size in hex without 0x,
#                   which this script would read as decimal: add the 0x.
#   adb public key  the owner's computer's adbkey.pub (~/.android/adbkey.pub)
#   output image    default $SILVERING_ROOT/images/userdata-<time>.img
#   FILE_CONTEXTS   SELinux labels (default: the build's root/file_contexts;
#                   the unit's own, from its ramdisk, is better where read:
#                   ~/silvering/units/<profile>/ramdisk/file_contexts)
#   USB_CONFIG      USB functions for the first boot: mtp,adb (the default,
#                   what a MIRROR that ADB already works with uses) or empty
#                   (leave it to build.prop, which on msm8916 asks for
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

[ $# -ge 2 ] || { sed -n '2,29p' "$0" >&2; exit 2; }
if [[ $1 =~ ^0[xX][0-9a-fA-F]{1,16}$ ]]; then
    size=$(( 16#${1:2} ))
elif [[ $1 =~ ^[1-9][0-9]{0,18}$ ]]; then
    size=$(( 10#$1 ))
else
    echo "partition size must be a number of bytes (decimal or 0x hex), not $1" >&2; exit 2
fi
key=$2
# A MIRROR's userdata partition is about 5 GiB; outside this range it is not one.
[ "$size" -ge $((1024 * 1024 * 1024)) ] && [ "$size" -le $((64 * 1024 * 1024 * 1024)) ] \
    || { echo "partition size $size is not between 1 and 64 GiB" >&2; exit 1; }
length=$(( (size - footer) / 4096 * 4096 ))

out_marker=$root/src/out/.silvering-product-out-$target
[ -f "$out_marker" ] || { echo "no build of $target in $root/src: run build.sh first" >&2; exit 1; }
product_out=$root/src/$(sed 's|^/src/||' "$out_marker")
host_bin=$root/src/out/host/linux-x86/bin
[ -x "$host_bin/make_ext4fs" ] || { echo "no make_ext4fs in $host_bin" >&2; exit 1; }
[ -x "$host_bin/simg2img" ] || { echo "no simg2img in $host_bin" >&2; exit 1; }
e2fsck=$(command -v e2fsck || command -v /sbin/e2fsck || command -v /usr/sbin/e2fsck) \
    || { echo "e2fsck (e2fsprogs) is needed to check the image" >&2; exit 1; }
contexts=${FILE_CONTEXTS:-$product_out/root/file_contexts}
[ -f "$contexts" ] || { echo "no file_contexts at $contexts" >&2; exit 1; }
grep -q '^/data/misc/adb(' "$contexts" || { echo "$contexts has no label for /data/misc/adb" >&2; exit 1; }
usb_config=${USB_CONFIG-mtp,adb}
# The board's init.qcom.usb.rc matches whole compositions; mtp,adb is the one
# seen working with ADB on a MIRROR. Empty leaves build.prop's default.
case "$usb_config" in
    ""|mtp,adb) ;;
    *) echo "USB_CONFIG=$usb_config: only mtp,adb (or empty) is known to bring ADB up" >&2; exit 1 ;;
esac

out=${3:-$root/images/userdata-$(date +%Y%m%d-%H%M%S).img}
mkdir -p "$(dirname "$out")"
out=$(cd "$(dirname "$out")" && pwd)/$(basename "$out")
[ ! -e "$out" ] && [ ! -e "$out.sha256" ] || { echo "$out or its .sha256 already exists" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/stage/misc/adb" "$work/out"
# adbd (Android 6, adb_auth_client.cpp) reads each line up to the first space
# or tab as base64 of a 2048-bit RSAPublicKey: len 64, n0inv, n[64], rr[64],
# exponent, little-endian. Check it is one, and write it back in that form.
python3 - "$key" "$work/stage/misc/adb/adb_keys" <<'EOF'
import base64, binascii, re, struct, sys

def refuse(why):
    sys.exit(f"{sys.argv[1]} is not an ADB public key (adbkey.pub): {why}")

try:
    with open(sys.argv[1], "rb") as f:
        text = f.read().decode("ascii")
except (OSError, UnicodeDecodeError) as e:
    refuse(str(e))
lines = [line for line in text.splitlines() if line.strip()]
if len(lines) != 1:
    refuse(f"{len(lines)} lines, not one")
fields = lines[0].strip().split(None, 1)
try:
    blob = base64.b64decode(fields[0], validate=True)
except (binascii.Error, ValueError):
    refuse("not base64")
if len(blob) != 524:
    refuse(f"{len(blob)} bytes, not 524")
words = struct.unpack("<131I", blob[:524])
size, n0inv = words[0], words[1]
n = int.from_bytes(blob[8:264], "little")
rr = int.from_bytes(blob[264:520], "little")
exponent = words[130]
if size != 64:
    refuse(f"length {size}, not 64 words")
if n.bit_length() != 2048 or n % 2 == 0:
    refuse("the modulus is not an odd 2048-bit number")
if (n0inv * n) % (1 << 32) != (1 << 32) - 1:
    refuse("n0inv does not match the modulus")
if rr != pow(2, 4096, n):
    refuse("rr does not match the modulus")
if exponent not in (3, 65537):
    refuse(f"exponent {exponent}")
name = re.sub(r"[^\x21-\x7e ]", "", fields[1]).strip() if len(fields) > 1 else ""
with open(sys.argv[2], "w") as out:
    out.write(base64.b64encode(blob).decode("ascii") + " " + (name or "owner") + "\n")
EOF
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

# Read the image back before it is hashed: the hash is what later vouches for it.
docker run --rm -v "$root/src/out/host:/host:ro" -v "$work:/work" "$image" \
    /host/linux-x86/bin/simg2img /work/out/userdata.img /work/out/userdata.raw
"$e2fsck" -fn "$work/out/userdata.raw" > "$work/e2fsck.log" 2>&1 \
    || { echo "the new userdata image does not pass e2fsck -fn:" >&2; tail -n 20 "$work/e2fsck.log" >&2; exit 1; }
rm -f "$work/out/userdata.raw"

mv "$work/out/userdata.img" "$out"
( cd "$(dirname "$out")" && sha256sum "$(basename "$out")" > "$(basename "$out").sha256" )
echo "userdata: $out ($length bytes of a $size-byte partition; sparse; sha256 in $(basename "$out").sha256)"
