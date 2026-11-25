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
padding='Padding at end of inode bitmap is not set. Fix? no'
unexpected=$(grep -vE \
    -e '^$' \
    -e '^e2fsck [0-9][0-9.]* \(' \
    -e '^Pass [1-5]: ' \
    -e '^Padding at end of inode bitmap is not set\. Fix\? no$' \
    -e ': \*+ WARNING: Filesystem still has errors \*+$' \
    -e ': [0-9]+/[0-9]+ files \([0-9.]+% non-contiguous\), [0-9]+/[0-9]+ blocks$' \
    "$log" || true)
passes=$(grep -cE '^Pass [1-5]: ' "$log" || true)
if [ "$status" -eq 4 ] && [ -z "$unexpected" ] && [ "$passes" -eq 5 ] && grep -qxF "$padding" "$log"; then
    exit 0
fi
echo "$1 does not pass e2fsck -fn (exit $status):" >&2
tail -n 20 "$log" >&2
exit 1
