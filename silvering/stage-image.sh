#!/usr/bin/env bash
# Inside the build container, after `make systemimage fs_config`: make
# system.img from a copy of the build's /system with a unit's files
# (unit-files.py output) laid over it, check that the image holds exactly that
# tree with the unit's owners and modes, and only then put it in DEST_DIR.
#
#   stage-image.sh UNIT_DIR DEST_DIR
#
# The build's own out/ is left as it was.
set -euo pipefail

unit=$1
dest=$2
here=$(cd "$(dirname "$0")" && pwd)
export DEBUGFS_PAGER=__none__
: "${OUT:?run after lunch}"
stage=$OUT/silvering-stage
info=$OUT/obj/PACKAGING/systemimage_intermediates/system_image_info.txt
[ -f "$info" ] || { echo "no $info: build the systemimage goal first" >&2; exit 1; }

rm -rf "$stage"
mkdir -p "$stage/image" "$stage/check"
python "$here/stage.py" stage "$unit" "$OUT/system" "$stage/system"

# Owners and modes: fs_config's defaults for the whole tree, with the unit's
# own for its files.
( cd "$stage" && find system -mindepth 1 \( -type d -printf '%p/\n' \) -o -printf '%p\n' ) | sort \
    | { echo "system/"; cat; } | fs_config -C -D "$OUT" > "$stage/fs_config.defaults"
python "$here/stage.py" fs-config "$unit" < "$stage/fs_config.defaults" > "$stage/fs_config.txt"
cp "$info" "$stage/image_info.txt"
echo "fs_config=$stage/fs_config.txt" >> "$stage/image_info.txt"
build/tools/releasetools/build_image.py "$stage/system" "$stage/image_info.txt" \
    "$stage/image/system.img" "$stage/system"

# Read the image back. debugfs cannot dump the root folder itself, so the
# root's entries are listed from the image and each is dumped on its own.
raw=$stage/image/system.raw.img
simg2img "$stage/image/system.img" "$raw" 2>/dev/null || cp "$stage/image/system.img" "$raw"
debugfs -R "ls -p /" "$raw" 2>/dev/null | awk -F/ 'NF >= 7 && $6 != "." && $6 != ".." && $6 != "lost+found" { print $6 }' \
    | sort > "$stage/image-root.txt"
( cd "$stage/system" && ls -A ) | sort > "$stage/stage-root.txt"
if ! cmp -s "$stage/image-root.txt" "$stage/stage-root.txt"; then
    echo "system.img's top level differs from the staged tree:" >&2
    diff "$stage/stage-root.txt" "$stage/image-root.txt" >&2 || true
    exit 1
fi
while IFS= read -r entry; do
    printf 'rdump "/%s" "%s"\n' "$entry" "$stage/check"
done < "$stage/image-root.txt" > "$stage/rdump.cmds"
debugfs -f "$stage/rdump.cmds" "$raw" > "$stage/rdump.log" 2>&1 || true
if ! diff -r --no-dereference -x lost+found "$stage/system" "$stage/check" > "$stage/check.diff" 2>&1; then
    echo "system.img does not hold the staged tree:" >&2
    head -n 20 "$stage/check.diff" >&2
    tail -n 5 "$stage/rdump.log" >&2
    exit 1
fi
python -c 'import json, sys; [sys.stdout.write("stat \"/%s\"\n" % p) for p in sorted(json.load(open(sys.argv[1]))["meta"])]' \
    "$unit/unit.json" > "$stage/stat.cmds"
debugfs -f "$stage/stat.cmds" "$raw" 2>/dev/null | python "$here/stage.py" check-meta "$unit"
rm -f "$raw"

mkdir -p "$dest"
mv "$stage/image/system.img" "$dest/system.img"
cp "$unit/unit.json" "$dest/unit.json"
( cd "$dest" && sha256sum system.img > system.img.sha256 )
echo "system.img: $dest ($(du -h "$dest/system.img" | cut -f1)); contents, owners and modes checked"
