import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
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
                     ("/system/lost+found", "folder cannot be listed")],
            folders={"vendor/secret": (0o750, 0, 1000)},
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
        })
        code, _, err = self.run_tool(tar, built)
        self.assertEqual(code, 1)
        self.assertIn("could not read 2 files", err)

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
        self.assertEqual(summary["meta"]["bin/qmuxd"], [0o755, 0, 2000])
        # Every folder holding a unit file or link keeps the unit's owner and
        # mode, also where the build has that folder.
        self.assertEqual(sorted(summary["folderMeta"]), [
            "bin", "etc", "lib", "lib/modules", "lib/modules/pronto",
            "vendor", "vendor/lib", "vendor/secret"])
        self.assertEqual(summary["folderMeta"]["vendor/secret"], [0o750, 0, 1000])
        # A config with the build's bytes is not copied but keeps its owner and mode.
        self.assertEqual(summary["configMeta"], {"etc/wifi/WCNSS_qcom_cfg.ini": [0o640, 0, 1010]})
        self.assertEqual(summary["baseline"]["buildProp"], hashlib.sha256(BUILD_PROP).hexdigest())

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


if __name__ == "__main__":
    unittest.main()
