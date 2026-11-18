#!/usr/bin/env python3
"""Take the board's own files from a copy of a unit's /system.

Compares the unit's /system (the tar that Mirror Home's device-files transfer
writes, with its MIRROR-FILES.json) with a Silvering build's system output,
which stays untouched, and writes OUT_DIR:

  files/         the unit's files to lay over the build's /system
  unit.json      the links to make there, and what was taken and why and
                 what was left out

build.sh stages these over a copy of the build output with stage.py, after
everything the build makes, so they always win; the unit's kernel modules
replace the build's whole set (so that set must have been read completely).
Nothing here may be committed or published: these are the board maker's and
Qualcomm's files, and the output folder may not be inside a git checkout.

Taken from the unit:
  - every file the build does not make (Qualcomm's proprietary libraries,
    daemons and firmware), except apps, framework jars and compiled code,
    which are built against the factory framework;
  - kernel modules, always: they must match the unit's kernel;
  - board configuration (audio routing, Wi-Fi, firmware, key layouts, ...)
    where the unit's copy differs.
The build's own copy wins for every other file both have. Files the transfer
could not read completely are never taken.

  unit-files.py SYSTEM_TAR BUILT_SYSTEM_DIR OUT_DIR
"""
import argparse
import hashlib
import json
import os
import posixpath
import shutil
import sys
import tarfile
import tempfile

SKIP_PREFIXES = (
    "app/", "priv-app/", "framework/", "vendor/app/", "vendor/framework/",
    "vendor/overlay/", "media/", "fonts/", "usr/share/zoneinfo/", "etc/security/",
    "etc/selinux/",
)
SKIP_SUFFIXES = (".apk", ".jar", ".odex", ".oat", ".art", ".vdex", ".dex")
SKIP_FILES = {
    "build.prop", "recovery-from-boot.p", "bin/install-recovery.sh",
    "etc/recovery-resource.dat",
}
MODULE_PREFIX = "lib/modules/"
# Board configuration: the unit's copy wins when it differs.
CONFIG_PREFIXES = (
    "etc/wifi/", "etc/firmware/", "etc/sensors/", "etc/acdbdata/",
    "etc/mixer_paths", "etc/audio_platform_info", "etc/audio_policy.conf",
    "etc/audio_effects.conf", "etc/media_profiles", "etc/media_codecs",
    "etc/thermal-engine", "etc/gps.conf", "etc/izat.conf",
    "usr/keylayout/", "usr/keychars/", "usr/idc/", "vendor/etc/",
)


class Refused(Exception):
    pass


def classify(path, built):
    """Return (take, reason) for one regular file of the unit's /system."""
    if path in SKIP_FILES or path.startswith(SKIP_PREFIXES) or path.endswith(SKIP_SUFFIXES):
        return False, "skipped"
    if path.startswith(MODULE_PREFIX):
        return True, "kernel-module"
    if not built:
        return True, "missing"
    if path.startswith(CONFIG_PREFIXES):
        return True, "config"
    return False, "built"


def system_path(name):
    """The path under /system of a tar member, or None if it is outside it."""
    if name.startswith("./"):
        name = name[2:]
    if not name.startswith("system/"):
        return None
    rel = name[len("system/"):].rstrip("/")
    if rel and (rel.startswith("/") or posixpath.normpath(rel) != rel
                or ".." in rel.split("/")):
        raise Refused(f"unsafe path in the archive: {name!r}")
    return rel


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


def read_transfer_report(tar):
    try:
        member = tar.getmember("MIRROR-FILES.json")
    except KeyError:
        raise Refused("no MIRROR-FILES.json: not a Mirror Home device-files transfer")
    report = json.load(tar.extractfile(member))
    if report.get("schema") != "mirror-files/1" or report.get("root") != "/system":
        raise Refused("MIRROR-FILES.json is not a mirror-files/1 transfer of /system")
    damaged, unread = set(), []
    for entry in report.get("skipped", []):
        path = entry.get("path", "")
        if path != "/system/lost+found":
            unread.append([path, entry.get("reason", "")])
        modules = "/system/" + MODULE_PREFIX
        if (path + "/").startswith(modules) or modules.startswith(path.rstrip("/") + "/"):
            # The unit's modules replace the build's whole set, so that set
            # must be complete.
            raise Refused(f"the unit's kernel modules were not all read: {path} "
                          f"({entry.get('reason', '')})")
        if "padded" in entry.get("reason", "") and path.startswith("/system/"):
            damaged.add(path[len("/system/"):])
    return damaged, unread


def extract(tar, built_system, files_dir):
    damaged, unread = read_transfer_report(tar)
    taken, symlinks, counts = [], [], {}
    for m in tar:
        rel = system_path(m.name)
        if not rel or m.isdir():
            continue
        built = os.path.join(built_system, rel)
        if m.issym():
            if rel.startswith(MODULE_PREFIX):
                symlinks.append((rel, m.linkname))  # the whole module tree is replaced
                continue
            if rel.startswith(SKIP_PREFIXES) or rel.endswith(SKIP_SUFFIXES):
                continue
            if os.path.lexists(built) and not os.path.islink(built):
                continue  # the build makes a real file here; it wins
            if not (os.path.islink(built) and os.readlink(built) == m.linkname):
                symlinks.append((rel, m.linkname))
            continue
        if not m.isfile() or rel in damaged:
            continue
        take, reason = classify(rel, os.path.isfile(built) and not os.path.islink(built))
        if take and reason == "config" and same_content(m, tar, built):
            take, reason = False, "same"
        counts[reason] = counts.get(reason, 0) + 1
        if not take:
            continue
        dest = os.path.join(files_dir, *rel.split("/"))
        if not os.path.realpath(dest).startswith(os.path.realpath(files_dir) + os.sep):
            raise Refused(f"path leaves the output folder: {rel!r}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with tar.extractfile(m) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
        os.chmod(dest, 0o644)
        taken.append((rel, reason, [m.mode & 0o7777, m.uid, m.gid]))
    if not any(why == "kernel-module" and p.endswith(".ko") for p, why, _ in taken):
        raise Refused("no kernel modules (.ko) in the unit's /system")
    return taken, symlinks, counts, unread


def inside_git_checkout(path):
    path = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system_tar")
    ap.add_argument("built_system")
    ap.add_argument("out_dir")
    ap.add_argument("--allow-unread", action="store_true",
                    help="build although the transfer could not read some files")
    args = ap.parse_args(argv)

    def refuse(message):
        print(f"unit-files: {message}", file=sys.stderr)
        return 1

    if inside_git_checkout(args.out_dir):
        return refuse(f"{args.out_dir} is inside a git checkout; keep the unit's files out of it")
    if not os.path.isfile(os.path.join(args.built_system, "build.prop")):
        return refuse(f"{args.built_system} is not a built /system (no build.prop)")
    try:
        os.makedirs(args.out_dir, exist_ok=True)
        work = tempfile.mkdtemp(prefix="files.new-", dir=args.out_dir)
    except OSError as e:
        return refuse(str(e))
    try:
        with tarfile.open(args.system_tar) as tar:
            taken, symlinks, counts, unread = extract(tar, args.built_system, work)
        if unread and not args.allow_unread:
            raise Refused(f"the transfer could not read {len(unread)} files, for example "
                          f"{unread[0][0]} ({unread[0][1]}); they will be missing from the "
                          "image. Pass --allow-unread to build without them.")
    except (Refused, OSError, tarfile.TarError, ValueError) as e:
        shutil.rmtree(work, ignore_errors=True)
        return refuse(str(e))

    files_dir = os.path.join(args.out_dir, "files")
    if os.path.isdir(files_dir):
        shutil.rmtree(files_dir)
    os.rename(work, files_dir)
    with open(os.path.join(args.built_system, "build.prop"), "rb") as f:
        build_prop = hashlib.sha256(f.read()).hexdigest()
    summary = {"schema": "silvering-unit/2",
               "baseline": {"buildProp": build_prop},
               "taken": len(taken), "counts": counts, "unread": unread,
               "modules": "replace",
               "meta": {rel: meta for rel, _, meta in taken},
               "links": [[rel, target] for rel, target in sorted(symlinks)],
               "byReason": {r: sorted(p for p, why, _ in taken if why == r)
                            for r in ("kernel-module", "config", "missing")}}
    with open(os.path.join(args.out_dir, "unit.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps({"taken": len(taken), "counts": counts,
                      "links": len(symlinks), "unread": len(unread)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
