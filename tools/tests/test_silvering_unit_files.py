import contextlib
import importlib.util
import io
import json
import pathlib
import tarfile
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unit_files", REPO / "silvering" / "unit-files.py")
unit_files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unit_files)


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
                "lib/modules/wlan.ko": b"unit kernel",
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
            links=[("vendor/lib/libfoo.so", "libbar.so"), ("bin/sh", "mksh")],
            skipped=[("/system/bin/thermal-engine", "read ended early; padded with zeros"),
                     ("/system/bin/cnd", "lstat failed: EACCES (Permission denied)")],
        )
        built = self.make_built({
            "lib/modules/wlan.ko": b"source kernel",
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
                                           "lib/modules/wlan.ko", "vendor/lib/libqmi.so"])
        self.assertEqual((out / "symlinks.txt").read_text(), "vendor/lib/libfoo.so -> libbar.so\n")
        summary = json.loads((out / "unit.json").read_text())
        self.assertEqual(summary["damaged"], ["bin/thermal-engine"])

    def test_refuses_unsafe_paths_and_keeps_the_previous_output(self):
        built = self.make_built({})
        code, out, _ = self.run_tool(self.make_tar({"bin/qmuxd": b"daemon"}), built)
        self.assertEqual(code, 0)
        bad = self.make_tar({"bin/qmuxd": b"daemon"}, raw_names=["system/../../escape"])
        code, out, err = self.run_tool(bad, built)
        self.assertEqual(code, 1)
        self.assertIn("unsafe path", err)
        self.assertFalse((self.tmp / "escape").exists())
        self.assertEqual(self.taken(out), ["bin/qmuxd"])

    def test_refuses_a_missing_build(self):
        code, _, err = self.run_tool(self.make_tar({"bin/qmuxd": b"d"}), self.tmp / "nowhere")
        self.assertEqual(code, 1)
        self.assertIn("not a built /system", err)


if __name__ == "__main__":
    unittest.main()
