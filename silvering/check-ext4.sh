#!/usr/bin/env bash
# Check an unpacked ext4 image with e2fsck -fn before it is hashed.
# Passes a clean report, or one whose only finding is the clear padding at
# the end of an inode bitmap that make_ext4fs leaves (the kernel never reads
# those bits). Anything else, an unknown line or a report cut short, fails.
#
#   ./check-ext4.sh <raw ext4 image>
#   E2FSCK  the e2fsck to run (default: e2fsck on the PATH, /sbin or /usr/sbin)
set -euo pipefail

[ $# -eq 1 ] || { sed -n '2,8p' "$0" >&2; exit 2; }
e2fsck=${E2FSCK:-}
[ -n "$e2fsck" ] || e2fsck=$(command -v e2fsck || command -v /sbin/e2fsck || command -v /usr/sbin/e2fsck || true)
[ -n "$e2fsck" ] || { echo "e2fsck (e2fsprogs) is needed to check the image" >&2; exit 1; }

log=$(mktemp)
trap 'rm -f "$log"' EXIT
status=0
# The report is read line by line, so it must be in e2fsck's own words.
LC_ALL=C LANGUAGE= "$e2fsck" -fn "$1" > "$log" 2>&1 || status=$?

[ "$status" -eq 0 ] && exit 0
# Exit 4 passes only on this whole report, line by line, blank lines aside: a report cut short
# could hide a finding e2fsck makes after the inode bitmaps (the block bitmaps' padding, for one).
expected=(
    '^e2fsck [0-9][0-9.]* \('
    '^Pass 1: ' '^Pass 2: ' '^Pass 3: ' '^Pass 4: ' '^Pass 5: '
    '^Padding at end of inode bitmap is not set\. Fix\? no$'
    ': \*+ WARNING: Filesystem still has errors \*+$'
    ': [0-9]+/[0-9]+ files \([0-9.]+% non-contiguous\), [0-9]+/[0-9]+ blocks$'
)
mapfile -t lines < <(grep -v '^$' "$log" || true)
whole=$([ "$status" -eq 4 ] && [ "${#lines[@]}" -eq "${#expected[@]}" ] && echo yes || echo no)
for i in "${!expected[@]}"; do
    [[ ${lines[$i]:-} =~ ${expected[$i]} ]] || whole=no
done
[ "$whole" = yes ] && exit 0
echo "$1 does not pass e2fsck -fn (exit $status):" >&2
tail -n 20 "$log" >&2
exit 1
