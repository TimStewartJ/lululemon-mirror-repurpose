import json
import pathlib
import tempfile
import unittest
import zipfile

import sys

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from janus_wrap import expected_apk_hash, write_guarded
from mirrorctl import parse_adb_devices


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
