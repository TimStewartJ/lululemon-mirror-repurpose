import contextlib
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
spec = importlib.util.spec_from_file_location("unit_files", REPO / "silvering" / "unit-files.py")
unit_files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unit_files)
stage_spec = importlib.util.spec_from_file_location("stage", REPO / "silvering" / "stage.py")
stage = importlib.util.module_from_spec(stage_spec)
stage_spec.loader.exec_module(stage)

MODULES = {"lib/modules/pronto/pronto_wlan.ko": b"unit kernel"}


class UnitFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))

    def make_tar(self, unit, links=(), skipped=(), raw_names=()):
        path = self.tmp / "system.tar"
        report = {"schema": "mirror-files/1", "root": "/system",
                  "skipped": [{"path": p, "reason": r} for p, r in skipped]}
        with tarfile.open(path, "w") as tar:
            def add(name, data):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            add("MIRROR-FILES.json", json.dumps(report).encode())
            for rel, data in unit.items():
                add(f"system/{rel}", data)
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
        for rel, data in {"build.prop": b"ro.build.product=msm8916_64\n", **built}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        return root

    def run_tool(self, tar, built):
        out = self.tmp / "unit"
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = unit_files.main([str(tar), str(built), str(out)])
        return code, out, err.getvalue()

    def taken(self, out):
        files = out / "files"
        return sorted(p.relative_to(files).as_posix() for p in files.rglob("*") if p.is_file())

    def test_takes_board_files_and_keeps_built_ones(self):
        tar = self.make_tar(
            unit={
                "vendor/lib/libqmi.so": b"blob",
                "bin/qmuxd": b"daemon",
                "bin/thermal-engine": b"short",
                "lib/modules/pronto/pronto_wlan.ko": b"unit kernel",
                "etc/mixer_paths.xml": b"unit",
                "etc/wifi/WCNSS_qcom_cfg.ini": b"same",
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
                     ("/system/bin/cnd", "lstat failed: EACCES (Permission denied)")],
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
        code, out, _ = self.run_tool(tar, built)
        self.assertEqual(code, 0)
        self.assertEqual(self.taken(out), ["bin/qmuxd", "etc/mixer_paths.xml",
                                           "lib/modules/pronto/pronto_wlan.ko",
                                           "vendor/lib/libqmi.so"])
        summary = json.loads((out / "unit.json").read_text())
        # Module links are always kept: the whole module tree is replaced.
        # libfoo.so is a real file in the build, which wins.
        self.assertEqual(summary["links"], [
            ["lib/modules/wlan.ko", "/system/lib/modules/pronto/pronto_wlan.ko"]])
        self.assertEqual(summary["damaged"], ["bin/thermal-engine"])

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

    def test_refuses_a_missing_build(self):
        code, _, err = self.run_tool(self.make_tar(MODULES | {"bin/qmuxd": b"d"}), self.tmp / "nowhere")
        self.assertEqual(code, 1)
        self.assertIn("not a built /system", err)


    def test_refuses_an_incomplete_module_set(self):
        tar = self.make_tar(MODULES, skipped=[("/system/lib/modules/core_ctl.ko",
                                               "lstat failed: EACCES (Permission denied)")])
        code, _, err = self.run_tool(tar, self.make_built({}))
        self.assertEqual(code, 1)
        self.assertIn("kernel modules were not all read", err)
        only_metadata = self.make_tar({"lib/modules/modules.dep": b"", "bin/qmuxd": b"d"})
        code, _, err = self.run_tool(only_metadata, self.make_built({}))
        self.assertEqual(code, 1)
        self.assertIn("no kernel modules", err)

    @unittest.skipUnless(hasattr(os, "symlink") and os.name == "posix", "needs symlinks")
    def test_stage_replaces_links_instead_of_writing_through_them(self):
        built = self.make_built({"lib/modules/old.ko": b"source kernel", "etc/target": b"keep"})
        os.symlink("../etc/target", built / "lib/cfg")
        unit = self.tmp / "unit"
        (unit / "files/lib").mkdir(parents=True)
        (unit / "files/lib/cfg").write_bytes(b"unit")
        (unit / "files/lib/modules/pronto").mkdir(parents=True)
        (unit / "files/lib/modules/pronto/pronto_wlan.ko").write_bytes(b"unit kernel")
        (unit / "unit.json").write_text(json.dumps({
            "schema": "silvering-unit/1", "modules": "replace",
            "byReason": {"config": ["lib/cfg"],
                         "kernel-module": ["lib/modules/pronto/pronto_wlan.ko"]},
            "links": [["lib/modules/wlan.ko", "/system/lib/modules/pronto/pronto_wlan.ko"]]}))
        staged = self.tmp / "stage"
        argv = sys.argv
        sys.argv = ["stage.py", str(unit), str(built), str(staged)]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                stage.main()
        finally:
            sys.argv = argv
        self.assertEqual((built / "etc/target").read_bytes(), b"keep")
        self.assertEqual((staged / "etc/target").read_bytes(), b"keep")
        self.assertFalse((staged / "lib/cfg").is_symlink())
        self.assertEqual((staged / "lib/cfg").read_bytes(), b"unit")
        self.assertFalse((staged / "lib/modules/old.ko").exists())
        self.assertEqual((staged / "lib/modules/pronto/pronto_wlan.ko").read_bytes(), b"unit kernel")
        self.assertEqual(os.readlink(staged / "lib/modules/wlan.ko"),
                         "/system/lib/modules/pronto/pronto_wlan.ko")

        # A payload that does not match unit.json is refused before staging.
        (unit / "files/lib/cfg").unlink()
        sys.argv = ["stage.py", str(unit), str(built), str(staged)]
        try:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                stage.main()
        finally:
            sys.argv = argv


if __name__ == "__main__":
    unittest.main()
