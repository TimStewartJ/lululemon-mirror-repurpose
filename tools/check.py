#!/usr/bin/env python3
"""Run the repository's complete local validation gate."""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys


REPO = pathlib.Path(__file__).resolve().parents[1]
DUMMY_CERTIFICATE = "a" * 64


def run(arguments: list[str]) -> None:
    print("+", subprocess.list2cmdline(arguments), flush=True)
    subprocess.run(arguments, cwd=REPO, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--emulator",
        action="store_true",
        help="also run the Android 6 emulator suite (see docs/validation.md)",
    )
    options = parser.parse_args()
    gradle = REPO / ("gradlew.bat" if os.name == "nt" else "gradlew")

    run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(REPO / "tools" / "tests"),
            "-v",
        ]
    )
    run(
        [
            str(gradle),
            ":android:mirror-home:testDebugUnitTest",
            ":android:mirror-home:assembleDebug",
            ":android:mirror-home:assembleRelease",
            ":android:mirror-home:lintDebug",
            ":android:ota-updater:testDebugUnitTest",
            ":android:ota-updater:assembleDebug",
            ":android:ota-updater:assembleRelease",
            ":android:ota-updater:lintDebug",
            ":android:system-helper:assembleDebug",
            ":android:system-helper:lintDebug",
            ":android:voice-lab:testDebugUnitTest",
            ":android:voice-lab:assembleDebug",
            ":android:voice-lab:lintDebug",
            f"-PmirrorHomeCertificateSha256={DUMMY_CERTIFICATE}",
            f"-PmirrorOtaBootstrapTokenSha256={DUMMY_CERTIFICATE}",
            "--no-daemon",
        ]
    )
    if options.emulator:
        run(
            [
                sys.executable,
                str(REPO / "tools" / "validate.py"),
                "emulator",
                "--skip-build",
            ]
        )
    run(["git", "diff", "--check"])


if __name__ == "__main__":
    main()
