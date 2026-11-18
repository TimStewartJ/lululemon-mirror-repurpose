#!/usr/bin/env bash
# Fetch the Silvering sources: every project of manifest/default.xml at its
# pinned commit, shallow (one commit each). Runs on the build host, not in the
# container, because the repo tool needs Python 3.6 or newer.
#
#   SILVERING_ROOT  where everything goes (default ~/silvering)
#   JOBS            parallel fetches (default 4)
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=${SILVERING_ROOT:-$HOME/silvering}
jobs=${JOBS:-4}
src=$root/src
manifest_repo=$root/manifest

mkdir -p "$root/bin" "$src"
if [ ! -x "$root/bin/repo" ]; then
    curl -fsSL https://storage.googleapis.com/git-repo-downloads/repo -o "$root/bin/repo"
    chmod +x "$root/bin/repo"
fi
export PATH=$root/bin:$PATH

# repo wants the manifest in a git repository; keep a local one in step with
# this checkout.
if [ ! -d "$manifest_repo/.git" ]; then
    git init -q -b main "$manifest_repo"
fi
cp "$here/manifest/default.xml" "$manifest_repo/default.xml"
git -C "$manifest_repo" add default.xml
if ! git -C "$manifest_repo" diff --cached --quiet; then
    git -C "$manifest_repo" -c user.name=silvering -c user.email=silvering@localhost \
        commit -q -m "Silvering manifest"
fi

cd "$src"
if [ ! -d .repo ]; then
    repo init -q -u "file://$manifest_repo" -b main -m default.xml \
        --depth=1 --no-clone-bundle
fi
repo sync -q -c -j"$jobs" --no-tags --no-clone-bundle --force-sync
echo "sources ready: $src ($(du -sh "$src" | cut -f1))"
