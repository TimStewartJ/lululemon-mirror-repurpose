#!/usr/bin/env python
"""Put a unit's files (unit-files.py output) into a Silvering system image.

  stage.py stage UNIT_DIR BUILT_SYSTEM STAGE_SYSTEM
      Copy BUILT_SYSTEM to STAGE_SYSTEM (links kept as links), remove the
      build's kernel modules when the unit replaces them, then put each of the
      unit's files and links in place. Every destination entry is replaced
      itself, never written through, and no path may lead through a link.
  stage.py fs-config UNIT_DIR < DEFAULTS > FS_CONFIG
      Take fs_config's default owners and modes for the staged tree and give
      the unit's files the owners and modes they have on the unit.
  stage.py check-meta UNIT_DIR < DEBUGFS_STAT_OUTPUT
      Check that the image gives the unit's files those owners and modes.

Runs in the build container, so it keeps to Python 2.7.
"""
from __future__ import print_function

import hashlib
import json
import os
import posixpath
import re
import shutil
import sys


def fail(message):
    print("stage: " + message, file=sys.stderr)
    sys.exit(1)


def check_rel(rel):
    if (not rel or rel.startswith("/") or posixpath.normpath(rel) != rel
            or ".." in rel.split("/")):
        fail("unsafe path %r" % rel)


def make_parents(stage, rel, create=True):
    current = stage
    for part in rel.split("/")[:-1]:
        current = os.path.join(current, part)
        if os.path.islink(current):
            fail("%s leads through a link" % rel)
        if not os.path.isdir(current):
            if os.path.lexists(current):
                fail("%s needs a folder where the build has a file" % rel)
            if not create:
                return False
            os.mkdir(current)
    return True


def clear(dest):
    if os.path.islink(dest) or os.path.isfile(dest):
        os.remove(dest)
    elif os.path.isdir(dest):
        shutil.rmtree(dest)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def load_unit(unit):
    with open(os.path.join(unit, "unit.json")) as f:
        summary = json.load(f)
    if summary.get("schema") != "silvering-unit/2":
        fail("%s/unit.json is not unit-files.py output" % unit)
    return summary


def unit_files(unit, summary):
    """The unit's payload, which must be exactly what unit.json lists."""
    files = os.path.join(unit, "files")
    declared = set(summary.get("meta", {}))
    present = set()
    if not os.path.isdir(files) or os.path.islink(files):
        fail("%s has no files/ folder" % unit)
    for folder, dirs, names in os.walk(files):
        for name in dirs + names:
            path = os.path.join(folder, name)
            if os.path.islink(path):
                fail("unexpected link in the unit's files: %s" % path)
            if name in names and not os.path.isfile(path):
                fail("not a regular file: %s" % path)
        for name in names:
            present.add(os.path.relpath(os.path.join(folder, name), files).replace(os.sep, "/"))
    if present != declared:
        fail("the unit's files do not match unit.json: missing %s, unexpected %s"
             % (sorted(declared - present)[:5], sorted(present - declared)[:5]))
    return files, present


def stage(unit, built, stage_dir):
    summary = load_unit(unit)
    build_prop = os.path.join(built, "build.prop")
    if not os.path.isfile(build_prop):
        fail("%s is not a built /system" % built)
    if summary.get("baseline", {}).get("buildProp") != sha256_file(build_prop):
        fail("unit.json was made against another build; run unit-files.py on this one")
    files, present = unit_files(unit, summary)
    replace_modules = summary.get("modules") == "replace"
    if replace_modules and not any(
            p.startswith("lib/modules/") and p.endswith(".ko") for p in present):
        fail("unit.json replaces the kernel modules but has none")

    if os.path.lexists(stage_dir):
        shutil.rmtree(stage_dir)
    shutil.copytree(built, stage_dir, symlinks=True)
    if replace_modules and make_parents(stage_dir, "lib/modules/x", create=False):
        clear(os.path.join(stage_dir, "lib", "modules"))

    for rel in sorted(present):
        check_rel(rel)
        make_parents(stage_dir, rel)
        dest = os.path.join(stage_dir, rel)
        clear(dest)
        shutil.copyfile(os.path.join(files, *rel.split("/")), dest)
        os.chmod(dest, 0o644)

    links = summary.get("links", [])
    for rel, target in links:
        check_rel(rel)
        if not target or "\n" in target or "\0" in target:
            fail("bad link target for %s" % rel)
        make_parents(stage_dir, rel)
        dest = os.path.join(stage_dir, rel)
        clear(dest)
        os.symlink(target, dest)
    print("staged %d files and %d links over %s" % (len(present), len(links), built))


def fs_config(unit):
    meta = load_unit(unit).get("meta", {})
    seen = set()
    for line in sys.stdin:
        fields = line.split()
        if len(fields) >= 4 and fields[0].startswith("system/"):
            rel = fields[0][len("system/"):]
            if rel in meta:
                mode, uid, gid = meta[rel]
                fields[1:4] = [str(uid), str(gid), "%o" % mode]
                seen.add(rel)
        print(" ".join(fields))
    missing = set(meta) - seen
    if missing:
        fail("fs_config did not list %s" % sorted(missing)[:5])


def check_meta(unit):
    meta = load_unit(unit).get("meta", {})
    found = {}
    path = None
    for line in sys.stdin:
        command = re.match(r"debugfs:\s+stat\s+\"?/([^\"]+)\"?", line)
        if command:
            path = command.group(1)
            continue
        if path is None:
            continue
        mode = re.search(r"Mode:\s+(\d+)", line)
        if mode:
            found.setdefault(path, {})["mode"] = int(mode.group(1), 8) & 0o7777
        owner = re.search(r"User:\s+(\d+)\s+Group:\s+(\d+)", line)
        if owner:
            found.setdefault(path, {})["owner"] = (int(owner.group(1)), int(owner.group(2)))
    wrong = []
    for rel, (mode, uid, gid) in sorted(meta.items()):
        got = found.get(rel, {})
        if got.get("mode") != mode or got.get("owner") != (uid, gid):
            wrong.append("%s: want %o %d:%d, image has %s" % (rel, mode, uid, gid, got or "nothing"))
    if wrong:
        fail("owners or modes differ in the image:\n  " + "\n  ".join(wrong[:20]))
    print("owners and modes of %d unit files match" % len(meta))


def main():
    args = sys.argv[1:]
    if len(args) == 4 and args[0] == "stage":
        stage(*args[1:])
    elif len(args) == 2 and args[0] == "fs-config":
        fs_config(args[1])
    elif len(args) == 2 and args[0] == "check-meta":
        check_meta(args[1])
    else:
        fail(__doc__.split("\n\n")[1])


if __name__ == "__main__":
    main()
