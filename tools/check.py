#!/usr/bin/env python3
"""Run the repository's complete local validation gate."""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys


REPO = pathlib.Path(__file__).resolve().parents[1]
COMPANION = REPO / "companion"
DUMMY_CERTIFICATE = "a" * 64


def run(arguments: list[str], cwd: pathlib.Path = REPO) -> None:
    print("+", subprocess.list2cmdline(arguments), flush=True)
    subprocess.run(arguments, cwd=cwd, check=True)


def check_companion() -> None:
    """The companion's own tests, which need Node.js and nothing else running."""
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise SystemExit("Node.js 24 is needed for the companion's tests (companion/README.md)")
    if not (COMPANION / "node_modules" / ".package-lock.json").is_file():
        run([npm, "ci", "--no-audit", "--no-fund"], COMPANION)
    run([node, "--test"], COMPANION)


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
    check_companion()
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
    # What R8 wrote for the release build, which no emulator run of the debug build would show.
    releases = sorted((REPO / "android" / "mirror-home" / "build" / "outputs" / "apk" / "release").glob("*.apk"))
    if not releases:
        raise SystemExit("The release build of Mirror Home left no APK to look into")
    run([sys.executable, str(REPO / "tools" / "dex_guard.py"), *map(str, releases)])
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
