import contextlib
import base64
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, REPO / "silvering" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


unit_files = load("unit_files", "unit-files.py")
stage = load("stage", "stage.py")

MODULES = {"lib/modules/pronto/pronto_wlan.ko": b"unit kernel"}
BUILD_PROP = b"ro.build.product=msm8916_64\n"


def run_stage(*args, stdin=""):
    argv, real_stdin = sys.argv, sys.stdin
    sys.argv, sys.stdin = ["stage.py", *map(str, args)], io.StringIO(stdin)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            stage.main()
        return 0, out.getvalue(), err.getvalue()
    except SystemExit as e:
        return e.code, out.getvalue(), err.getvalue()
    finally:
        sys.argv, sys.stdin = argv, real_stdin


class UnitFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))

    def make_tar(self, unit, links=(), skipped=(), raw_names=(), folders=None):
        """unit: path -> bytes, or (bytes, mode, uid, gid). Folders are
        0755 root:root unless FOLDERS says otherwise."""
        path = self.tmp / "system.tar"
        report = {"schema": "mirror-files/1", "root": "/system",
                  "skipped": [{"path": p, "reason": r} for p, r in skipped]}
        with tarfile.open(path, "w") as tar:
            def add(name, data, mode=0o644, uid=0, gid=0):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.uid, info.gid = len(data), mode, uid, gid
                tar.addfile(info, io.BytesIO(data))
            add("MIRROR-FILES.json", json.dumps(report).encode())
            parents = {"/".join(p.split("/")[:i]) for p in [*unit, *(r for r, _ in links)]
                       for i in range(1, p.count("/") + 1)}
            for rel in sorted(parents):
                info = tarfile.TarInfo(f"system/{rel}")
                info.type = tarfile.DIRTYPE
                info.mode, info.uid, info.gid = (folders or {}).get(rel, (0o755, 0, 0))
                tar.addfile(info)
            for rel, value in unit.items():
                add(f"system/{rel}", *(value if isinstance(value, tuple) else (value,)))
            for name in raw_names:
                add(name, b"x")
            for rel, target in links:
                info = tarfile.TarInfo(f"system/{rel}")
                info.type = tarfile.SYMTYPE
                info.linkname = target
                tar.addfile(info)
        return path

    def make_built(self, built):
        root = self.tmp / "built"
        for rel, data in {"build.prop": BUILD_PROP, **built}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        return root

    def run_tool(self, tar, built, *flags):
        out = self.tmp / "unit"
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = unit_files.main([*flags, str(tar), str(built), str(out)])
        return code, out, err.getvalue()

    def taken(self, out):
        files = out / "files"
        return sorted(p.relative_to(files).as_posix() for p in files.rglob("*") if p.is_file())

    def test_takes_board_files_and_keeps_built_ones(self):
        tar = self.make_tar(
            unit={
                "vendor/lib/libqmi.so": b"blob",
            "vendor/secret/key.bin": b"blob",
                "bin/qmuxd": (b"daemon", 0o755, 0, 2000),
                "bin/thermal-engine": b"short",
                "lib/modules/pronto/pronto_wlan.ko": b"unit kernel",
                "etc/mixer_paths.xml": b"unit",
                "etc/wifi/WCNSS_qcom_cfg.ini": (b"same", 0o640, 0, 1010),
                "etc/hosts": b"factory",
                "etc/security/mac_permissions.xml": b"factory keys",
                "lib/libc.so": b"factory",
                "app/MirrorLauncher/MirrorLauncher.apk": b"apk",
                "vendor/overlay/Theme.apk": b"apk",
                "framework/qcom.fmradio.jar": b"jar",
                "build.prop": b"props",
            },
            links=[("vendor/lib/libfoo.so", "libbar.so"), ("bin/sh", "mksh"),
                   ("lib/modules/wlan.ko", "/system/lib/modules/pronto/pronto_wlan.ko")],
            skipped=[("/system/bin/thermal-engine", "read ended early; padded with zeros"),
                     ("/system/bin/cnd", "lstat failed: EACCES (Permission denied)"),
                     ("/system/bin/vold", "lstat failed: EACCES (Permission denied)"),
                     ("/system/bin/install-recovery.sh", "lstat failed: EACCES (Permission denied)"),
                     ("/system/lost+found", "folder cannot be listed")],
            folders={"vendor/secret": (0o750, 0, 1000), "etc/wifi": (0o750, 0, 1010)},
        )
        built = self.make_built({
            "lib/modules/pronto/pronto_wlan.ko": b"source kernel",
            "vendor/lib/libfoo.so": b"x",
            "etc/mixer_paths.xml": b"caf ",
            "etc/wifi/WCNSS_qcom_cfg.ini": b"same",
            "etc/hosts": b"built",
            "etc/security/mac_permissions.xml": b"silvering keys",
            "lib/libc.so": b"rebuilt",
            "bin/sh": b"real file",
            "bin/vold": b"built daemon",
        })
        code, _, err = self.run_tool(tar, built)
        self.assertEqual(code, 1)
        self.assertIn("could not read 2 files that the image needs from the unit", err)

        code, out, _ = self.run_tool(tar, built, "--allow-unread")
        self.assertEqual(code, 0)
        self.assertEqual(self.taken(out), ["bin/qmuxd", "etc/mixer_paths.xml",
                                           "lib/modules/pronto/pronto_wlan.ko",
                                           "vendor/lib/libqmi.so", "vendor/secret/key.bin"])
        summary = json.loads((out / "unit.json").read_text())
        # Module links are always kept: the whole module tree is replaced.
        # libfoo.so is a real file in the build, which wins.
        self.assertEqual(summary["links"], [
            ["lib/modules/wlan.ko", "/system/lib/modules/pronto/pronto_wlan.ko"]])
        self.assertEqual([p for p, _ in summary["unread"]], ["/system/bin/thermal-engine", "/system/bin/cnd"])
        # Unread files the build makes, or leaves out anyway, are not missing.
        self.assertEqual(summary["unreadNotNeeded"], [["/system/bin/vold", "built"],
                                                      ["/system/bin/install-recovery.sh", "skipped"]])
        self.assertEqual(summary["meta"]["bin/qmuxd"], [0o755, 0, 2000])
        # Every folder holding a unit file or link keeps the unit's owner and
        # mode, also where the build has that folder.
        self.assertEqual(sorted(summary["folderMeta"]), [
            "bin", "etc", "etc/wifi", "lib", "lib/modules", "lib/modules/pronto",
            "vendor", "vendor/lib", "vendor/secret"])
        self.assertEqual(summary["folderMeta"]["etc/wifi"], [0o750, 0, 1010])
        self.assertEqual(summary["folderMeta"]["vendor/secret"], [0o750, 0, 1000])
        # A config with the build's bytes is not copied but keeps its owner and mode.
        self.assertEqual(summary["configMeta"], {"etc/wifi/WCNSS_qcom_cfg.ini": [0o640, 0, 1010]})
        self.assertEqual(summary["baseline"]["buildProp"], hashlib.sha256(BUILD_PROP).hexdigest())

    def test_unread_files_the_build_makes_do_not_stop_it(self):
        tar = self.make_tar(MODULES | {"bin/qmuxd": b"daemon"},
                            skipped=[("/system/bin/wpa_supplicant", "lstat failed: EACCES")])
        code, out, err = self.run_tool(tar, self.make_built({"bin/wpa_supplicant": b"built"}))
        self.assertEqual(code, 0, err)
        summary = json.loads((out / "unit.json").read_text())
        self.assertEqual(summary["unread"], [])
        self.assertEqual(summary["unreadNotNeeded"], [["/system/bin/wpa_supplicant", "built"]])

    @unittest.skipUnless(os.name == "posix", "needs symlinks")
    def test_an_unread_path_with_a_working_built_link_is_not_missing(self):
        tar = self.make_tar(MODULES | {"bin/qmuxd": b"daemon"},
                            skipped=[("/system/bin/getevent", "lstat failed: EACCES"),
                                     ("/system/xbin/getevent", "lstat failed: EACCES"),
                                     ("/system/bin/dangling", "lstat failed: EACCES"),
                                     ("/system/bin/through-nothing", "lstat failed: EACCES"),
                                     ("/system/bin/slash", "lstat failed: EACCES"),
                                     ("/system/bin/slash-dot", "lstat failed: EACCES"),
                                     ("/system/outside/thermal-engine", "lstat failed: EACCES"),
                                     ("/system/etc/wifi/WCNSS_qcom_cfg.ini", "lstat failed: EACCES")])
        built = self.make_built({"bin/toolbox": b"tool", "etc/caf/WCNSS_qcom_cfg.ini": b"caf"})
        os.symlink("toolbox", built / "bin" / "getevent")
        os.symlink("/system/bin", built / "xbin")
        os.symlink("/system/bin/nothing", built / "bin" / "dangling")
        # Linux needs old-dir to exist to go back up from it.
        os.symlink("old-dir/../toolbox", built / "bin" / "through-nothing")
        # A file is no folder: toolbox/ and toolbox/. fail with ENOTDIR.
        os.symlink("toolbox/", built / "bin" / "slash")
        os.symlink("toolbox/.", built / "bin" / "slash-dot")
        host = self.tmp / "host"
        host.mkdir()
        (host / "thermal-engine").write_bytes(b"not the build's")
        os.symlink(host, built / "outside")
        os.symlink("caf", built / "etc" / "wifi")
        code, out, err = self.run_tool(tar, built, "--allow-unread")
        self.assertEqual(code, 0, err)
        summary = json.loads((out / "unit.json").read_text())
        # A built link to a built program works, also through a linked folder;
        # one that leads nowhere, or out of the build, does not; linked
        # configuration is still the build's copy, which the owner approves.
        self.assertEqual(summary["unread"], [["/system/bin/dangling", "lstat failed: EACCES"],
                                             ["/system/bin/through-nothing", "lstat failed: EACCES"],
                                             ["/system/bin/slash", "lstat failed: EACCES"],
                                             ["/system/bin/slash-dot", "lstat failed: EACCES"],
                                             ["/system/outside/thermal-engine", "lstat failed: EACCES"]])
        self.assertEqual(summary["unreadNotNeeded"], [["/system/bin/getevent", "built-link"],
                                                      ["/system/xbin/getevent", "built-link"]])
        self.assertEqual(summary["unreadBuildCopy"],
                         [["/system/etc/wifi/WCNSS_qcom_cfg.ini", "lstat failed: EACCES"]])

    def test_unread_files_must_match_the_approved_list(self):
        tar = self.make_tar(MODULES | {"bin/qmuxd": b"daemon"},
                            skipped=[("/system/bin/perfd", "lstat failed: EACCES"),
                                     ("/system/etc/wifi/WCNSS_qcom_cfg.ini", "lstat failed: EACCES")])
        built = self.make_built({"etc/wifi/WCNSS_qcom_cfg.ini": b"caf"})
        approved = self.tmp / "approved.txt"
        approved.write_text("# owner, 2026-11-21\n/system/bin/perfd\n")
        code, _, err = self.run_tool(tar, built, "--expect-unread", str(approved))
        self.assertEqual(code, 1)
        self.assertIn("not listed ['/system/etc/wifi/WCNSS_qcom_cfg.ini']", err)
        approved.write_text("/system/bin/perfd\n/system/etc/wifi/WCNSS_qcom_cfg.ini\n/system/bin/cnd\n")
        code, _, err = self.run_tool(tar, built, "--expect-unread", str(approved))
        self.assertEqual(code, 1)
        self.assertIn("listed but read or not needed ['/system/bin/cnd']", err)
        approved.write_text("/system/etc/wifi/WCNSS_qcom_cfg.ini\n/system/bin/perfd\n")
        code, out, err = self.run_tool(tar, built, "--expect-unread", str(approved))
        self.assertEqual(code, 0, err)
        summary = json.loads((out / "unit.json").read_text())
        # The image has the build's copy of the configuration, not nothing.
        self.assertEqual(summary["unread"], [["/system/bin/perfd", "lstat failed: EACCES"]])
        self.assertEqual(summary["unreadBuildCopy"],
                         [["/system/etc/wifi/WCNSS_qcom_cfg.ini", "lstat failed: EACCES"]])

    def test_refuses_unsafe_paths_and_keeps_the_previous_output(self):
        built = self.make_built({})
        code, out, _ = self.run_tool(self.make_tar(MODULES | {"bin/qmuxd": b"daemon"}), built)
        self.assertEqual(code, 0)
        bad = self.make_tar(MODULES | {"bin/qmuxd": b"daemon"}, raw_names=["system/../../escape"])
        code, out, err = self.run_tool(bad, built)
        self.assertEqual(code, 1)
        self.assertIn("unsafe path", err)
        self.assertFalse((self.tmp / "escape").exists())
        self.assertEqual(self.taken(out), ["bin/qmuxd", "lib/modules/pronto/pronto_wlan.ko"])
        self.assertEqual([p.name for p in out.iterdir() if p.name.startswith("files.new")], [])

    def test_refuses_a_missing_build(self):
        code, _, err = self.run_tool(self.make_tar(MODULES), self.tmp / "nowhere")
        self.assertEqual(code, 1)
        self.assertIn("not a built /system", err)

    def test_refuses_an_incomplete_module_set(self):
        tar = self.make_tar(MODULES, skipped=[("/system/lib/modules/core_ctl.ko",
                                               "lstat failed: EACCES (Permission denied)")])
        code, _, err = self.run_tool(tar, self.make_built({}), "--allow-unread")
        self.assertEqual(code, 1)
        self.assertIn("kernel modules were not all read", err)
        only_metadata = self.make_tar({"lib/modules/modules.dep": b"", "bin/qmuxd": b"d"})
        code, _, err = self.run_tool(only_metadata, self.make_built({}))
        self.assertEqual(code, 1)
        self.assertIn("no kernel modules", err)

    def test_fs_config_gives_unit_files_their_owners_and_modes(self):
        unit = self.tmp / "unit"
        unit.mkdir()
        (unit / "unit.json").write_text(json.dumps({
            "schema": "silvering-unit/2", "meta": {"vendor/bin/qmuxd": [0o755, 0, 2000]}}))
        defaults = ("system 0 0 755 capabilities=0x0\n"
                    "system/vendor/bin/qmuxd 0 0 644 capabilities=0x0\n"
                    "system/bin/sh 0 2000 755 capabilities=0x0\n")
        code, out, _ = run_stage("fs-config", unit, stdin=defaults)
        self.assertEqual(code, 0)
        self.assertIn("system/vendor/bin/qmuxd 0 2000 755 capabilities=0x0\n", out)
        self.assertIn("system/bin/sh 0 2000 755 capabilities=0x0\n", out)
        code, _, err = run_stage("fs-config", unit, stdin="system 0 0 755\n")
        self.assertEqual(code, 1)
        code, out, _ = run_stage("check-commands", unit)
        self.assertEqual(out, 'ls -p "/vendor/bin"\n')
        listing = ('debugfs:  ls -p "/vendor/bin"\n'
                   "/11/040755/0/2000/./4096/\n"
                   "/12/%s/0/2000/qmuxd/6/\n")
        code, _, err = run_stage("check-meta", unit, stdin=listing % "104755")
        self.assertEqual(code, 1)  # a setuid bit the unit does not have
        self.assertIn("vendor/bin/qmuxd: want 755 0:2000", err)
        code, out, _ = run_stage("check-meta", unit, stdin=listing % "100755")
        self.assertEqual(code, 0)

    @unittest.skipUnless(os.name == "posix", "needs symlinks")
    def test_stage_replaces_links_instead_of_writing_through_them(self):
        built = self.make_built({"lib/modules/old.ko": b"source kernel", "etc/target": b"keep"})
        os.symlink("../etc/target", built / "lib/cfg")
        unit = self.tmp / "unit"
        (unit / "files/lib/modules/pronto").mkdir(parents=True)
        (unit / "files/lib/cfg").write_bytes(b"unit")
        (unit / "files/lib/modules/pronto/pronto_wlan.ko").write_bytes(b"unit kernel")
        summary = {
            "schema": "silvering-unit/2", "modules": "replace",
            "baseline": {"buildProp": hashlib.sha256(BUILD_PROP).hexdigest()},
            "meta": {"lib/cfg": [0o644, 0, 0],
                     "lib/modules/pronto/pronto_wlan.ko": [0o644, 0, 0]},
            "links": [["lib/modules/wlan.ko", "/system/lib/modules/pronto/pronto_wlan.ko"]]}
        (unit / "unit.json").write_text(json.dumps(summary))
        staged = self.tmp / "stage"
        code, _, err = run_stage("stage", unit, built, staged)
        self.assertEqual(code, 0, err)
        self.assertEqual((built / "etc/target").read_bytes(), b"keep")
        self.assertEqual((staged / "etc/target").read_bytes(), b"keep")
        self.assertFalse((staged / "lib/cfg").is_symlink())
        self.assertEqual((staged / "lib/cfg").read_bytes(), b"unit")
        self.assertFalse((staged / "lib/modules/old.ko").exists())
        self.assertEqual((staged / "lib/modules/pronto/pronto_wlan.ko").read_bytes(), b"unit kernel")
        self.assertEqual(os.readlink(staged / "lib/modules/wlan.ko"),
                         "/system/lib/modules/pronto/pronto_wlan.ko")

        (built / "build.prop").write_bytes(b"another build\n")
        code, _, err = run_stage("stage", unit, built, staged)
        self.assertEqual(code, 1)
        self.assertIn("another build", err)
        (built / "build.prop").write_bytes(BUILD_PROP)

        (unit / "files/lib/cfg").unlink()
        code, _, err = run_stage("stage", unit, built, staged)
        self.assertEqual(code, 1)
        self.assertIn("do not match unit.json", err)
        (unit / "files/lib/cfg").write_bytes(b"unit")

        # A lib folder that is a link must not lead the module cleanup outside.
        outside = self.tmp / "outside/modules"
        outside.mkdir(parents=True)
        (outside / "keep.ko").write_bytes(b"keep")
        built2 = self.tmp / "built2"
        built2.mkdir()
        (built2 / "build.prop").write_bytes(BUILD_PROP)
        os.symlink(self.tmp / "outside", built2 / "lib")
        code, _, err = run_stage("stage", unit, built2, self.tmp / "stage2")
        self.assertEqual(code, 1)
        self.assertIn("leads through a link", err)
        self.assertTrue((outside / "keep.ko").exists())


class CheckSignersTest(unittest.TestCase):
    PUBLIC_PLATFORM = b"0\x82aosp platform certificate"
    PUBLIC_TESTKEY = b"0\x82aosp testkey certificate"
    HOST_PLATFORM = b"0\x82host platform certificate"
    HOST_RELEASE = b"0\x82host releasekey certificate"

    def setUp(self):
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.public = self.tmp / "security"
        self.public.mkdir()
        self.pem(self.public / "platform.x509.pem", self.PUBLIC_PLATFORM)
        self.pem(self.public / "testkey.x509.pem", self.PUBLIC_TESTKEY)
        self.platform = self.pem(self.tmp / "platform.x509.pem", self.HOST_PLATFORM)
        self.system = self.tmp / "system"
        # ApkSigners.java's verified signers of each app.
        self.signers = {}
        self.apk("framework/framework-res.apk", self.HOST_PLATFORM)
        self.apk("app/Settings/Settings.apk", self.HOST_PLATFORM)
        self.apk("app/Gallery/Gallery.apk", self.HOST_RELEASE)
        self.policy((self.HOST_PLATFORM, "platform"), (self.HOST_RELEASE, "release"))

    def pem(self, path, cert):
        body = base64.encodebytes(cert).decode()
        path.write_text(f"-----BEGIN CERTIFICATE-----\n{body}-----END CERTIFICATE-----\n")
        return path

    def apk(self, rel, *certs, result=None):
        path = self.system / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"apk")
        self.signers[rel] = result or ",".join(sorted(hashlib.sha256(c).hexdigest() for c in certs))

    def policy(self, *signers):
        """signers: (certificate, seinfo) pairs, as mac_permissions.xml has them."""
        self.write_policy("".join(
            f'<signer signature="{c.hex()}"><seinfo value="{s}" /></signer>' for c, s in signers)
            + '<default><seinfo value="default" /></default>')

    def write_policy(self, body):
        path = self.system / "etc/security/mac_permissions.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"<?xml version='1.0'?>\n<policy>{body}</policy>")

    def check(self):
        listing = "".join(f"./{rel}\t{result}\n" for rel, result in self.signers.items())
        return run_stage("check-signers", self.system, self.public, self.platform, stdin=listing)

    def test_accepts_a_system_signed_with_the_hosts_keys(self):
        code, out, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertIn("verified signers of 3 apps", out)

    def test_refuses_apps_that_do_not_verify_or_were_not_checked(self):
        self.apk("app/Bad/Bad.apk", result="ERROR java.lang.SecurityException: invalid signature")
        self.apk("app/Half/Half.apk", result="ERROR java.lang.Exception: unsigned entry classes.dex")
        (self.system / "app/Missed.apk").write_bytes(b"apk")
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("app/Bad/Bad.apk: java.lang.SecurityException: invalid signature", err)
        self.assertIn("app/Half/Half.apk: java.lang.Exception: unsigned entry", err)
        self.assertIn("app/Missed.apk: not checked", err)

    def test_refuses_a_report_with_rows_twice_or_for_other_files(self):
        self.signers["app/Gone.apk"] = self.signers["framework/framework-res.apk"]
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("lists app/Gone.apk which is not an app of the image", err)

        del self.signers["app/Gone.apk"]
        listing = "".join(f"./{rel}\t{result}\n" for rel, result in self.signers.items())
        code, _, err = run_stage("check-signers", self.system, self.public, self.platform,
                                 stdin=listing + listing.splitlines(True)[0])
        self.assertEqual(code, 1)
        self.assertIn("lists framework/framework-res.apk twice", err)

    def test_refuses_public_keys_anywhere(self):
        self.apk("priv-app/Shell/Shell.apk", self.HOST_PLATFORM, self.PUBLIC_PLATFORM)
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("priv-app/Shell/Shell.apk: signed with AOSP's public platform key", err)

        self.apk("priv-app/Shell/Shell.apk", self.HOST_PLATFORM)
        self.policy((self.HOST_PLATFORM, "platform"), (self.PUBLIC_TESTKEY, "release"))
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("mac_permissions.xml names AOSP's public keys: testkey", err)

        self.pem(self.platform, self.PUBLIC_PLATFORM)
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("the platform key is AOSP's public platform key", err)

    def test_requires_the_hosts_platform_key_in_framework_and_policy(self):
        self.apk("framework/framework-res.apk", self.HOST_PLATFORM, self.HOST_RELEASE)
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("framework-res.apk is not signed with the host's platform key", err)

        self.apk("framework/framework-res.apk", self.HOST_PLATFORM)
        self.policy((self.HOST_PLATFORM, "release"))
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("does not map the host's platform key to seinfo platform", err)

    def test_policy_certificates_in_cert_children_and_package_labels(self):
        host, public = self.HOST_PLATFORM.hex(), self.PUBLIC_TESTKEY.hex()
        # A label for one package does not map the key for every app.
        self.write_policy(f'<signer signature="{host}"><package name="com.x">'
                          '<seinfo value="platform" /></package></signer>')
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("does not map the host's platform key", err)

        self.write_policy(f'<signer><cert signature="{host}" /><seinfo value="platform" /></signer>')
        code, _, err = self.check()
        self.assertEqual(code, 0, err)

        self.write_policy(f'<signer signature="{host}"><seinfo value="platform" /></signer>'
                          f'<signer><cert signature="{public}" /><seinfo value="release" /></signer>')
        code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("names AOSP's public keys: testkey", err)


@unittest.skipIf(os.name == "nt" or not shutil.which("bash"), "check-ext4.sh runs in a POSIX bash")
class CheckExt4(unittest.TestCase):
    """check-ext4.sh with a stand-in e2fsck that prints a given report (in Swedish unless LC_ALL=C)."""

    HEAD = "e2fsck 1.47.0 (5-Feb-2023)\n" + "".join(
        f"Pass {n}: Checking {what}\n" for n, what in enumerate(
            ("inodes, blocks, and sizes", "directory structure", "directory connectivity", "reference counts",
             "group summary information"), start=1))
    PADDING = "Padding at end of inode bitmap is not set. Fix? no\n"
    TAIL = ("\n\n/w/userdata.raw: ********** WARNING: Filesystem still has errors **********\n\n"
            "/w/userdata.raw: 16/327680 files (0.0% non-contiguous), 43615/1310716 blocks\n")

    def check(self, report, status):
        folder = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        stub = folder / "e2fsck"
        stub.write_text('#!/usr/bin/env bash\n'
                        '[ "$LC_ALL" = C ] || { printf "Pass 5: Kontrollerar\\nFel? nej\\n"; exit 4; }\n'
                        'cat "$REPORT"; exit "$STATUS"\n')
        stub.chmod(0o755)
        (folder / "report").write_text(report)
        environment = {**os.environ, "E2FSCK": str(stub), "REPORT": str(folder / "report"), "STATUS": str(status),
                       "LC_ALL": "sv_SE.UTF-8"}
        return subprocess.run(["bash", str(REPO / "silvering" / "check-ext4.sh"), "/w/userdata.raw"],
                              env=environment, capture_output=True, text=True).returncode

    def test_a_clean_report_passes(self):
        self.assertEqual(self.check(self.HEAD + "/w/userdata.raw: 16/327680 files (0.0% non-contiguous), "
                                    "43615/1310716 blocks\n", 0), 0)

    def test_the_bitmap_padding_alone_passes_in_any_locale(self):
        self.assertEqual(self.check(self.HEAD + self.PADDING + self.TAIL, 4), 0)

    def test_any_other_finding_fails(self):
        other = "Free blocks count wrong for group #0 (31, counted=32).\nFix? no\n"
        self.assertEqual(self.check(self.HEAD + self.PADDING + other + self.TAIL, 4), 1)
        self.assertEqual(self.check(self.HEAD + other + self.TAIL, 4), 1)

    def test_a_report_cut_short_or_another_status_fails(self):
        self.assertEqual(self.check(self.HEAD.replace("Pass 5: Checking group summary information\n", "")
                                    + self.PADDING + self.TAIL, 4), 1)
        self.assertEqual(self.check(self.HEAD + self.PADDING + self.TAIL, 8), 1)
        self.assertEqual(self.check(self.HEAD + self.PADDING, 4), 1)
        self.assertEqual(self.check(self.HEAD + self.PADDING + self.TAIL.rsplit("\n\n", 1)[0] + "\n", 4), 1)
        repeated = self.HEAD.replace("Pass 5: Checking group summary information", "Pass 4: Checking reference counts")
        self.assertEqual(self.check(repeated + self.PADDING + self.TAIL, 4), 1)
        self.assertEqual(self.check(self.HEAD + self.PADDING + self.PADDING + self.TAIL, 4), 1)


if __name__ == "__main__":
    unittest.main()
