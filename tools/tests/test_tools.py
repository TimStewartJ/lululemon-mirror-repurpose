import json
import pathlib
import subprocess
import tempfile
import unittest
import zipfile

import sys

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from janus_wrap import expected_apk_hash, write_guarded
from mirrorctl import (
    DeviceStateUnknownError,
    install_apk_verified,
    installed_apk_matches,
    installed_apk_path,
    installed_package_version,
    parse_adb_devices,
)


class MirrorCtlTest(unittest.TestCase):
    def test_parse_adb_devices_keeps_only_authorized_devices(self):
        output = """List of devices attached
mirror-one device product:mirror model:mirror
offline-one offline
pending unauthorized
"""
        self.assertEqual(["mirror-one"], parse_adb_devices(output))

    def test_profile_requires_known_package(self):
        profile = {"systemApks": {"known": {"sha256": "abc"}}}
        self.assertEqual("abc", expected_apk_hash(profile, "known"))
        with self.assertRaises(ValueError):
            expected_apk_hash(profile, "unknown")

    def test_installed_apk_path_prefers_base_apk(self):
        class FakeDevice:
            @staticmethod
            def shell(*_arguments):
                return "\n".join(
                    [
                        "package:/data/app/pkg/split_config.apk",
                        "package:/data/app/pkg/base.apk",
                    ]
                )

        self.assertEqual(
            "/data/app/pkg/base.apk",
            installed_apk_path(FakeDevice(), "pkg"),
        )

    def test_installed_apk_path_returns_none_when_absent(self):
        class FakeDevice:
            @staticmethod
            def shell(*_arguments):
                return ""

        self.assertIsNone(installed_apk_path(FakeDevice(), "pkg"))

    def test_installed_package_version_parses_dumpsys(self):
        class FakeDevice:
            @staticmethod
            def shell(*_arguments):
                return "versionCode=9 targetSdk=35\n    versionName=0.9.0\n"

        self.assertEqual(
            "0.9.0",
            installed_package_version(FakeDevice(), "pkg"),
        )

    def test_installed_apk_matches_pulled_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = pathlib.Path(directory) / "expected.apk"
            expected.write_bytes(b"signed apk")

            class FakeDevice:
                @staticmethod
                def shell(*_arguments):
                    return "package:/data/app/pkg/base.apk"

                @staticmethod
                def command(*arguments):
                    self.assertEqual("pull", arguments[0])
                    pathlib.Path(arguments[2]).write_bytes(b"signed apk")
                    return ""

            self.assertTrue(installed_apk_matches(FakeDevice(), "pkg", expected))

    def test_installed_apk_matches_rejects_different_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = pathlib.Path(directory) / "expected.apk"
            expected.write_bytes(b"new apk")

            class FakeDevice:
                @staticmethod
                def shell(*_arguments):
                    return "package:/data/app/pkg/base.apk"

                @staticmethod
                def command(*arguments):
                    pathlib.Path(arguments[2]).write_bytes(b"old apk")
                    return ""

            self.assertFalse(installed_apk_matches(FakeDevice(), "pkg", expected))

    def test_verified_install_accepts_committed_apk_after_adb_error(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = pathlib.Path(directory) / "expected.apk"
            expected.write_bytes(b"committed apk")

            class FakeDevice:
                @staticmethod
                def shell(*_arguments):
                    return "package:/data/app/pkg/base.apk"

                @staticmethod
                def command(*arguments):
                    if arguments[0] == "install":
                        raise subprocess.CalledProcessError(1, arguments)
                    pathlib.Path(arguments[2]).write_bytes(b"committed apk")
                    return ""

            install_apk_verified(FakeDevice(), "pkg", expected, "-r")

    def test_verified_install_stops_when_device_state_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = pathlib.Path(directory) / "expected.apk"
            expected.write_bytes(b"candidate")

            class FakeDevice:
                @staticmethod
                def shell(*arguments):
                    raise subprocess.CalledProcessError(1, arguments)

                @staticmethod
                def command(*arguments):
                    raise subprocess.CalledProcessError(1, arguments)

            with self.assertRaisesRegex(
                DeviceStateUnknownError,
                "no rollback was attempted",
            ):
                install_apk_verified(FakeDevice(), "pkg", expected, "-r")

    def test_verified_install_stops_when_success_marker_and_state_are_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = pathlib.Path(directory) / "expected.apk"
            expected.write_bytes(b"candidate")

            class FakeDevice:
                @staticmethod
                def shell(*arguments):
                    raise subprocess.CalledProcessError(1, arguments)

                @staticmethod
                def command(*arguments):
                    return "Performing Push Install"

            with self.assertRaisesRegex(
                DeviceStateUnknownError,
                "no rollback was attempted",
            ):
                install_apk_verified(FakeDevice(), "pkg", expected, "-r")

    def test_hash_guard_rejects_wrong_source_apk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            dex = root / "classes.dex"
            dex.write_bytes(b"dex\n035\x00" + b"\x00" * 104)
            apk = root / "source.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"manifest")
            profile = root / "profile.json"
            profile.write_text(
                json.dumps(
                    {"systemApks": {"pkg": {"sha256": "0" * 64}}}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "hash does not match"):
                write_guarded(dex, apk, root / "out.apk", profile, "pkg")


if __name__ == "__main__":
    unittest.main()
