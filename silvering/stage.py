#!/usr/bin/env python
"""Put a unit's files (unit-files.py output) into a Silvering system image.

  stage.py stage UNIT_DIR BUILT_SYSTEM STAGE_SYSTEM
      Copy BUILT_SYSTEM to STAGE_SYSTEM (links kept as links), remove the
      build's kernel modules when the unit replaces them, then put each of the
      unit's files and links in place. Every destination entry is replaced
      itself, never written through, and no path may lead through a link.
  stage.py fs-config UNIT_DIR < DEFAULTS > FS_CONFIG
      Take fs_config's default owners and modes for the staged tree and give
      the unit's files, and its folders the build lacks, the owners and modes
      they have on the unit.
  stage.py build-image IN_DIR IMAGE_INFO OUT_FILE FS_CONFIG
      Make system.img from IN_DIR with that complete owner and mode list.
  stage.py check-commands UNIT_DIR > COMMANDS
  stage.py check-meta UNIT_DIR < DEBUGFS_OUTPUT
      Check that the image gives the unit's files and folders those owners
      and modes.
  stage.py check-signers STAGE_SYSTEM PUBLIC_KEY_DIR PLATFORM_CERT
      Refuse an app signed with one of AOSP's public keys, and check that the
      framework and the security policy carry the host's platform key.

Runs in the build container, so it keeps to Python 2.7.
"""
from __future__ import print_function

import base64
import binascii
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
        os.chmod(dest, 0o600)  # the image takes owners and modes from fs_config

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


def wanted(summary):
    """Owners and modes the image must give, by path under /system."""
    want = dict(summary.get("meta", {}))
    want.update(summary.get("configMeta", {}))
    want.update(summary.get("folderMeta", {}))
    return want


def fs_config(unit):
    want = wanted(load_unit(unit))
    seen = set()
    for line in sys.stdin:
        fields = line.split()
        if len(fields) >= 4 and fields[0].startswith("system/"):
            rel = fields[0][len("system/"):].rstrip("/")
            if rel in want:
                mode, uid, gid = want[rel]
                fields[1:4] = [str(uid), str(gid), "%o" % mode]
                seen.add(rel)
        print(" ".join(fields))
    missing = set(want) - seen
    if missing:
        fail("fs_config did not list %s" % sorted(missing)[:5])


def build_image(in_dir, info, out_file, fs_config_file):
    """build_image.py, but with fs_config set on the image's own properties:
    the global ones it reads from INFO do not carry it over."""
    sys.path.insert(0, os.path.join("build", "tools", "releasetools"))
    import build_image as tool
    props = tool.ImagePropFromGlobalDict(tool.LoadGlobalDict(info), "system")
    props["fs_config"] = fs_config_file
    if not tool.BuildImage(in_dir, props, out_file, in_dir):
        fail("could not build %s from %s" % (out_file, in_dir))


def check_commands(unit):
    """debugfs commands listing the folders that hold the unit's entries."""
    for parent in sorted(set(posixpath.dirname(p) for p in wanted(load_unit(unit)))):
        print('ls -p "/%s"' % parent)


def check_meta(unit):
    """Read `debugfs -f` output of check_commands: `ls -p` prints the full
    mode, which `stat` cuts to its permission bits."""
    want = wanted(load_unit(unit))
    found = {}
    folder = None
    for line in sys.stdin:
        command = re.match(r'debugfs:\s+ls -p\s+"/([^"]*)"', line)
        if command:
            folder = command.group(1)
            continue
        entry = re.match(r"^/(\d+)/(\d+)/(\d+)/(\d+)/([^/]+)/", line)
        if entry and folder is not None:
            rel = posixpath.join(folder, entry.group(5)) if folder else entry.group(5)
            found[rel] = [int(entry.group(2), 8) & 0o7777, int(entry.group(3)), int(entry.group(4))]
    wrong = []
    for rel, meta in sorted(want.items()):
        if found.get(rel) != list(meta):
            wrong.append("%s: want %o %d:%d, image has %s" % (rel, meta[0], meta[1], meta[2],
                                                               found.get(rel, "nothing")))
    if wrong:
        fail("owners or modes differ in the image:\n  " + "\n  ".join(wrong[:20]))
    print("owners and modes of %d unit entries match" % len(want))


def der_items(data, start, end):
    """(item start, content start, content end, tag) of each DER item in
    data[start:end]."""
    pos = start
    while pos < end:
        if end - pos < 2:
            fail("truncated DER item")
        item, tag, length = pos, bytearray(data[pos:pos + 1])[0], bytearray(data[pos + 1:pos + 2])[0]
        pos += 2
        if length & 0x80:
            count = length & 0x7f
            if count < 1 or count > 4 or pos + count > end:
                fail("unsupported DER length")
            length = 0
            for byte in bytearray(data[pos:pos + count]):
                length = length * 256 + byte
            pos += count
        if pos + length > end:
            fail("truncated DER item")
        yield item, pos, pos + length, tag
        pos += length


def pkcs7_certificates(data):
    """The certificates of a PKCS#7 SignedData block (an APK's v1 signature
    file): ContentInfo { OID, [0] SignedData { ..., [0] certificates } }."""
    for _, start, end, tag in der_items(data, 0, len(data)):
        if tag != 0x30:
            break
        inner = list(der_items(data, start, end))
        if len(inner) < 2 or inner[1][3] != 0xa0:
            break
        signed = list(der_items(data, inner[1][1], inner[1][2]))
        if not signed or signed[0][3] != 0x30:
            break
        for _, cstart, cend, ctag in der_items(data, signed[0][1], signed[0][2]):
            if ctag == 0xa0:
                return [bytes(data[i:e]) for i, _, e, _ in der_items(data, cstart, cend)]
        return []
    fail("not a PKCS#7 signature block")


def pem_certificate(path):
    with open(path) as f:
        text = f.read()
    match = re.search(r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", text, re.S)
    if not match:
        fail("%s holds no certificate" % path)
    return base64.b64decode("".join(match.group(1).split()))


def apk_signers(path):
    import zipfile
    signers = []
    with zipfile.ZipFile(path) as apk:
        for name in apk.namelist():
            if re.match(r"META-INF/[^/]+\.(RSA|DSA|EC)$", name):
                signers.extend(pkcs7_certificates(apk.read(name)))
    return signers


def check_signers(system, public_dir, platform_pem):
    """Refuse an image with an app signed by one of AOSP's public keys
    (PUBLIC_DIR, build/target/product/security), and check that the
    framework and the security policy carry the host's platform key."""
    public = {}
    for name in sorted(os.listdir(public_dir)):
        if name.endswith(".x509.pem"):
            public[pem_certificate(os.path.join(public_dir, name))] = name[:-len(".x509.pem")]
    platform = pem_certificate(platform_pem)
    if platform in public:
        fail("the platform key is AOSP's public %s key" % public[platform])
    public_signed, apks = [], 0
    for folder, dirs, files in os.walk(system):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(folder, name)
            if not name.endswith(".apk") or os.path.islink(path):
                continue
            apks += 1
            for cert in apk_signers(path):
                if cert in public:
                    public_signed.append("%s (%s)" % (
                        os.path.relpath(path, system).replace(os.sep, "/"), public[cert]))
    if public_signed:
        fail("apps signed with AOSP's public keys:\n  " + "\n  ".join(public_signed))
    framework = os.path.join(system, "framework", "framework-res.apk")
    if not os.path.isfile(framework) or apk_signers(framework) != [platform]:
        fail("framework/framework-res.apk is not signed with the host's platform key")
    policy = os.path.join(system, "etc", "security", "mac_permissions.xml")
    if not os.path.isfile(policy):
        fail("no etc/security/mac_permissions.xml")
    with open(policy) as f:
        text = f.read().lower()
    if binascii.hexlify(platform).decode("ascii") not in text:
        fail("mac_permissions.xml does not name the host's platform key")
    stale = sorted(n for c, n in public.items() if binascii.hexlify(c).decode("ascii") in text)
    if stale:
        fail("mac_permissions.xml names AOSP's public keys: %s" % ", ".join(stale))
    print("signers of %d apps checked: none is an AOSP public key; framework and policy "
          "carry the platform key %s" % (apks, hashlib.sha256(platform).hexdigest()[:16]))


def main():
    args = sys.argv[1:]
    if len(args) == 4 and args[0] == "stage":
        stage(*args[1:])
    elif len(args) == 2 and args[0] == "fs-config":
        fs_config(args[1])
    elif len(args) == 5 and args[0] == "build-image":
        build_image(*args[1:])
    elif len(args) == 2 and args[0] == "check-commands":
        check_commands(args[1])
    elif len(args) == 2 and args[0] == "check-meta":
        check_meta(args[1])
    elif len(args) == 4 and args[0] == "check-signers":
        check_signers(*args[1:])
    else:
        fail(__doc__.split("\n\n")[1])


if __name__ == "__main__":
    main()
