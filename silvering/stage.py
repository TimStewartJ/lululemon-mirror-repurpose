#!/usr/bin/env python
"""Lay a unit's files (unit-files.py output) over a copy of a build's /system.

  stage.py UNIT_DIR BUILT_SYSTEM STAGE_SYSTEM

Copies BUILT_SYSTEM to STAGE_SYSTEM (links kept as links), removes the build's
kernel modules when the unit replaces them, then puts each of the unit's
files and links in place. Every destination entry is replaced itself, never
written through, and no path may lead through a link or out of the stage.
Runs in the build container, so it keeps to Python 2.7.
"""
from __future__ import print_function

import json
import os
import posixpath
import shutil
import sys


def fail(message):
    print("stage: " + message, file=sys.stderr)
    sys.exit(1)


def check_rel(rel):
    if (not rel or rel.startswith("/") or posixpath.normpath(rel) != rel
            or ".." in rel.split("/")):
        fail("unsafe path %r" % rel)


def make_parents(stage, rel):
    current = stage
    for part in rel.split("/")[:-1]:
        current = os.path.join(current, part)
        if os.path.islink(current):
            fail("%s leads through a link" % rel)
        if not os.path.isdir(current):
            if os.path.lexists(current):
                fail("%s needs a folder where the build has a file" % rel)
            os.mkdir(current)


def clear(dest):
    if os.path.islink(dest) or os.path.isfile(dest):
        os.remove(dest)
    elif os.path.isdir(dest):
        shutil.rmtree(dest)


def main():
    if len(sys.argv) != 4:
        fail("usage: stage.py UNIT_DIR BUILT_SYSTEM STAGE_SYSTEM")
    unit, built, stage = sys.argv[1:]
    with open(os.path.join(unit, "unit.json")) as f:
        summary = json.load(f)
    if summary.get("schema") != "silvering-unit/1":
        fail("%s/unit.json is not unit-files.py output" % unit)
    if not os.path.isfile(os.path.join(built, "build.prop")):
        fail("%s is not a built /system" % built)

    # The payload must be exactly what unit.json lists, before anything of the
    # build is removed.
    files = os.path.join(unit, "files")
    declared = set()
    for paths in summary.get("byReason", {}).values():
        declared.update(paths)
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
    if summary.get("modules") == "replace" and not any(
            p.startswith("lib/modules/") and p.endswith(".ko") for p in present):
        fail("unit.json replaces the kernel modules but has none")

    if os.path.lexists(stage):
        shutil.rmtree(stage)
    shutil.copytree(built, stage, symlinks=True)
    if summary.get("modules") == "replace":
        clear(os.path.join(stage, "lib", "modules"))

    count = 0
    for rel in sorted(present):
        src = os.path.join(files, *rel.split("/"))
        check_rel(rel)
        make_parents(stage, rel)
        dest = os.path.join(stage, rel)
        clear(dest)
        shutil.copyfile(src, dest)
        os.chmod(dest, 0o644)
        count += 1

    for rel, target in summary.get("links", []):
        check_rel(rel)
        if not target or "\n" in target or "\0" in target:
            fail("bad link target for %s" % rel)
        make_parents(stage, rel)
        dest = os.path.join(stage, rel)
        clear(dest)
        os.symlink(target, dest)
    print("staged %d files and %d links over %s" % (count, len(summary.get("links", [])), built))


if __name__ == "__main__":
    main()
