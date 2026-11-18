#!/usr/bin/env python3
"""Take the board's own files from a copy of a unit's /system.

Compares the unit's /system (the tar that Mirror Home's device-files transfer
writes) with a Silvering build's system output and writes
vendor/mirror/unit/: the files to add to the image and unit.mk, which copies
them in. Nothing it writes may be committed or published: these are the
board maker's and Qualcomm's files.

Taken from the unit:
  - every file the build does not make itself (Qualcomm's proprietary
    libraries, daemons and firmware), except apps and framework jars, which
    are compiled against the factory framework;
  - kernel modules, always: they must match the unit's kernel, not one built
    from source;
  - hardware configuration under etc/ and usr/ where the unit's differs
    (audio routing, Wi-Fi calibration, key layouts).
The build's own copy wins for every other file both have.

  unit-files.py SYSTEM_TAR BUILT_SYSTEM_DIR OUT_DIR [--report FILE]
"""
import argparse
import json
import os
import shutil
import sys
import tarfile

# Never taken from the unit, even when the build lacks them.
SKIP_PREFIXES = (
    "app/", "priv-app/", "framework/", "vendor/app/", "vendor/framework/",
    "media/", "fonts/", "usr/share/zoneinfo/",
)
SKIP_FILES = {
    "build.prop", "recovery-from-boot.p", "bin/install-recovery.sh",
    "etc/recovery-resource.dat", "etc/security/otacerts.zip",
}
# Always taken from the unit when it has them.
ALWAYS_PREFIXES = ("lib/modules/",)
# Taken from the unit when its copy differs from the build's.
CONFIG_PREFIXES = ("etc/", "usr/", "vendor/etc/")


def classify(path, built):
    """Return (take, reason) for one regular file of the unit's /system."""
    if path in SKIP_FILES or path.startswith(SKIP_PREFIXES):
        return False, "skipped"
    if path.startswith(ALWAYS_PREFIXES):
        return True, "kernel-module"
    if not built:
        return True, "missing"
    if path.startswith(CONFIG_PREFIXES):
        return True, "config"
    return False, "built"


def same_content(member, tar, built_file):
    if member.size != os.path.getsize(built_file):
        return False
    with tar.extractfile(member) as a, open(built_file, "rb") as b:
        while True:
            x, y = a.read(1 << 16), b.read(1 << 16)
            if x != y:
                return False
            if not x:
                return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system_tar")
    ap.add_argument("built_system")
    ap.add_argument("out_dir")
    ap.add_argument("--report", help="write a JSON summary here")
    args = ap.parse_args()

    files_dir = os.path.join(args.out_dir, "files")
    if os.path.isdir(files_dir):
        shutil.rmtree(files_dir)
    os.makedirs(files_dir)

    taken, symlinks, counts = [], [], {}
    with tarfile.open(args.system_tar) as tar:
        for m in tar:
            name = m.name.lstrip("./")
            if not name.startswith("system/"):
                continue
            rel = name[len("system/"):]
            if not rel or m.isdir():
                continue
            built = os.path.join(args.built_system, rel)
            if m.issym():
                if not os.path.lexists(built) and not rel.startswith(SKIP_PREFIXES):
                    symlinks.append((rel, m.linkname))
                continue
            if not m.isfile():
                continue
            take, reason = classify(rel, os.path.isfile(built))
            if take and reason == "config" and same_content(m, tar, built):
                take, reason = False, "same"
            counts[reason] = counts.get(reason, 0) + 1
            if not take:
                continue
            dest = os.path.join(files_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with tar.extractfile(m) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(dest, 0o644)
            taken.append((rel, reason))

    lines = ["# Written by silvering/unit-files.py. Do not commit or publish.",
             "LOCAL_PATH := vendor/mirror/unit", "PRODUCT_COPY_FILES += \\"]
    lines += [f"    $(LOCAL_PATH)/files/{rel}:system/{rel} \\" for rel, _ in sorted(taken)]
    lines.append("")
    with open(os.path.join(args.out_dir, "unit.mk"), "w") as f:
        f.write("\n".join(lines))
    with open(os.path.join(args.out_dir, "symlinks.txt"), "w") as f:
        f.writelines(f"{rel} -> {target}\n" for rel, target in sorted(symlinks))

    summary = {"taken": len(taken), "counts": counts, "symlinks": len(symlinks),
               "byReason": {r: sorted(p for p, why in taken if why == r)
                            for r in ("kernel-module", "config", "missing")}}
    if args.report:
        with open(args.report, "w") as f:
            json.dump(summary, f, indent=2)
    print(json.dumps({k: summary[k] for k in ("taken", "counts", "symlinks")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
