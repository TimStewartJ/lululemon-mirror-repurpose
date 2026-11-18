import importlib.util
import io
import pathlib
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("unit_files", REPO / "silvering" / "unit-files.py")
unit_files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(unit_files)


class UnitFilesTest(unittest.TestCase):
    def run_tool(self, unit, built, links=()):
        tmp = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        tar_path = tmp / "system.tar"
        with tarfile.open(tar_path, "w") as tar:
            for rel, data in unit.items():
                info = tarfile.TarInfo(f"system/{rel}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            for rel, target in links:
                info = tarfile.TarInfo(f"system/{rel}")
                info.type = tarfile.SYMTYPE
                info.linkname = target
                tar.addfile(info)
        built_dir = tmp / "built"
        for rel, data in built.items():
            (built_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (built_dir / rel).write_bytes(data)
        built_dir.mkdir(exist_ok=True)
        out = tmp / "unit"
        argv = sys.argv
        sys.argv = ["unit-files.py", str(tar_path), str(built_dir), str(out)]
        try:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(unit_files.main(), 0)
        finally:
            sys.argv = argv
        taken = sorted(p.relative_to(out / "files").as_posix()
                       for p in (out / "files").rglob("*") if p.is_file())
        return taken, (out / "unit.mk").read_text(), (out / "symlinks.txt").read_text()

    def test_takes_board_files_and_keeps_built_ones(self):
        taken, mk, links = self.run_tool(
            unit={
                "vendor/lib/libqmi.so": b"blob",
                "bin/qmuxd": b"daemon",
                "lib/modules/wlan.ko": b"unit kernel",
                "etc/mixer_paths.xml": b"unit",
                "etc/hosts": b"same",
                "lib/libc.so": b"factory",
                "app/MirrorLauncher/MirrorLauncher.apk": b"apk",
                "framework/qcom.fmradio.jar": b"jar",
                "build.prop": b"props",
            },
            built={
                "lib/modules/wlan.ko": b"source kernel",
                "etc/mixer_paths.xml": b"caf ",
                "etc/hosts": b"same",
                "lib/libc.so": b"rebuilt",
            },
            links=[("vendor/lib/libfoo.so", "libbar.so")],
        )
        self.assertEqual(taken, ["bin/qmuxd", "etc/mixer_paths.xml",
                                 "lib/modules/wlan.ko", "vendor/lib/libqmi.so"])
        self.assertIn("$(LOCAL_PATH)/files/bin/qmuxd:system/bin/qmuxd", mk)
        self.assertNotIn("libc.so", mk)
        self.assertEqual(links, "vendor/lib/libfoo.so -> libbar.so\n")


if __name__ == "__main__":
    unittest.main()
