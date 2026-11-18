#!/usr/bin/env bash
# Inside the build container, after `make systemimage`: make system.img from a
# copy of the build's /system with a unit's files (unit-files.py output) laid
# over it, check that the image holds exactly that tree, and only then put it
# in DEST_DIR.
#
#   stage-image.sh UNIT_DIR DEST_DIR
#
# The build's own out/ is left as it was, so unit-files.py can always compare
# against the build alone.
set -euo pipefail

unit=$1
dest=$2
here=$(cd "$(dirname "$0")" && pwd)
: "${OUT:?run after lunch}"
stage=$OUT/silvering-stage
info=$OUT/obj/PACKAGING/systemimage_intermediates/system_image_info.txt
[ -f "$info" ] || { echo "no $info: build the systemimage goal first" >&2; exit 1; }

rm -rf "$stage"
mkdir -p "$stage/image" "$stage/check"
python "$here/stage.py" "$unit" "$OUT/system" "$stage/system"
build/tools/releasetools/build_image.py "$stage/system" "$info" "$stage/image/system.img" "$stage/system"

# Read the image back and compare it with the staged tree. debugfs cannot dump
# the root folder itself, so each top-level entry is dumped on its own.
raw=$stage/image/system.raw.img
simg2img "$stage/image/system.img" "$raw" 2>/dev/null || cp "$stage/image/system.img" "$raw"
for entry in $(ls -A "$stage/system"); do
    echo "rdump /$entry $stage/check"
done > "$stage/rdump.cmds"
debugfs -f "$stage/rdump.cmds" "$raw" > "$stage/rdump.log" 2>&1 || true
rm -f "$raw"
if ! diff -r --no-dereference -x lost+found "$stage/system" "$stage/check" > "$stage/check.diff" 2>&1; then
    echo "system.img does not hold the staged tree:" >&2
    head -n 20 "$stage/check.diff" >&2
    tail -n 5 "$stage/rdump.log" >&2
    exit 1
fi

mkdir -p "$dest"
mv "$stage/image/system.img" "$dest/system.img"
( cd "$dest" && sha256sum system.img > system.img.sha256 )
echo "system.img: $dest ($(du -h "$dest/system.img" | cut -f1)); contents match the staged tree"
