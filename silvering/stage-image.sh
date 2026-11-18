#!/usr/bin/env bash
# Inside the build container, after a build: make system.img from a copy of
# the build's /system with a unit's files (unit-files.py output) laid over it,
# then check that the image holds exactly the staged tree.
#
#   stage-image.sh UNIT_DIR DEST_DIR
#
# The unit's files go in last so that they win over anything the build made;
# when the unit has kernel modules, they replace the build's whole set, which
# was built for a kernel the board does not run. The build's own out/ is left
# as it was, so unit-files.py can always compare against it.
set -euo pipefail

unit=$1
dest=$2
: "${OUT:?run after lunch}"
stage=$OUT/silvering-stage
info=$OUT/obj/PACKAGING/systemimage_intermediates/system_image_info.txt
[ -d "$unit/files" ] && [ -f "$unit/symlinks.txt" ] || { echo "no unit files in $unit" >&2; exit 1; }
[ -f "$info" ] || { echo "no $info: build the systemimage goal first" >&2; exit 1; }

rm -rf "$stage"
mkdir -p "$stage" "$dest"
cp -a "$OUT/system" "$stage/system"
if [ -d "$unit/files/lib/modules" ]; then
    rm -rf "$stage/system/lib/modules"
fi
cp -a "$unit/files/." "$stage/system/"
while IFS= read -r line; do
    rel=${line%% -> *}
    target=${line#* -> }
    mkdir -p "$(dirname "$stage/system/$rel")"
    rm -rf "${stage:?}/system/$rel"
    ln -s "$target" "$stage/system/$rel"
done < "$unit/symlinks.txt"

build/tools/releasetools/build_image.py "$stage/system" "$info" "$dest/system.img" "$stage/system"

# The image must hold the staged tree, file for file.
raw=$stage/system.raw.img
if ! simg2img "$dest/system.img" "$raw" 2>/dev/null; then
    cp "$dest/system.img" "$raw"
fi
mkdir -p "$stage/check"
debugfs -R "rdump / $stage/check" "$raw" >/dev/null 2>&1
diff -r --no-dereference -x lost+found "$stage/system" "$stage/check/"* >/dev/null \
    || diff -r --no-dereference -x lost+found "$stage/system" "$stage/check" >/dev/null
rm -f "$raw"
( cd "$dest" && sha256sum system.img > system.img.sha256 )
echo "system.img: $dest ($(du -h "$dest/system.img" | cut -f1)); contents match the staged tree"
