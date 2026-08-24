import hashlib
import hmac
import json
import pathlib
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock

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
from otactl import (
    EMPTY_SHA256,
    OtaClient,
    OtaClientError,
    canonical_request,
    provision,
    signed_headers,
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

    def test_ota_hmac_matches_android_vector(self):
        canonical = canonical_request(
            "GET",
            "/api/v1/status",
            "1787600000",
            "00112233445566778899aabbccddeeff",
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        signature = hmac.new(
            b"test-token-0123456789",
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(
            "b9a4973b26871e37e55088196c48d4c80c618f7b1e235ba4812aba0696e121e9",
            signature,
        )

    @mock.patch("otactl.time.time_ns", return_value=1787600000000000000)
    @mock.patch("otactl.secrets.randbits", side_effect=[1, 2])
    def test_ota_headers_are_unique_on_coarse_windows_clock(
        self,
        _randbits,
        _time_ns,
    ):
        first = signed_headers("token", "GET", "/api/v1/status", EMPTY_SHA256)
        second = signed_headers("token", "GET", "/api/v1/status", EMPTY_SHA256)
        self.assertNotEqual(first["X-Ota-Counter"], second["X-Ota-Counter"])

    def test_ota_push_fails_when_supervisor_rolls_back(self):
        client = OtaClient("127.0.0.1", "token")
        with mock.patch.object(
            client,
            "request",
            return_value={"transactionId": "transaction"},
        ), mock.patch.object(
            client,
            "wait",
            return_value={
                "state": "rolled_back",
                "message": "health check failed",
            },
        ):
            with self.assertRaisesRegex(OtaClientError, "rolled_back"):
                client.push(pathlib.Path(__file__))

    def test_ota_provision_sends_bootstrap_secret_not_status_object(self):
        fake_device = mock.Mock()
        fake_device.property.return_value = "fingerprint"
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "otactl.Device",
            return_value=fake_device,
        ), mock.patch(
            "otactl.free_tcp_port",
            return_value=43123,
        ), mock.patch(
            "otactl.bootstrap_token",
            return_value="bootstrap-secret",
        ), mock.patch(
            "otactl.raw_request",
            side_effect=[
                {"deviceOwner": True},
                {"token": "t" * 43, "port": 8791},
                {"provisioned": True},
            ],
        ) as raw:
            result = provision(
                "serial",
                pathlib.Path(directory) / "token.json",
                pathlib.Path(directory) / "bootstrap.txt",
                "10.0.0.196",
            )

        self.assertTrue(result["provisioned"])
        self.assertEqual(
            "bootstrap-secret",
            raw.call_args_list[1].kwargs["headers"]["X-Ota-Bootstrap-Token"],
        )

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
