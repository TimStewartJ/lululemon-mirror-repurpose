#!/usr/bin/env python3
"""Run the repository's complete local validation gate."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys


REPO = pathlib.Path(__file__).resolve().parents[1]
DUMMY_CERTIFICATE = "a" * 64


def run(arguments: list[str]) -> None:
    print("+", subprocess.list2cmdline(arguments), flush=True)
    subprocess.run(arguments, cwd=REPO, check=True)


def executable(name: str) -> str:
    candidate = shutil.which(name)
    if candidate:
        return candidate
    if os.name == "nt":
        candidate = shutil.which(f"{name}.cmd")
        if candidate:
            return candidate
    raise RuntimeError(f"Required executable not found: {name}")


def main() -> None:
    gradle = REPO / ("gradlew.bat" if os.name == "nt" else "gradlew")
    npm = executable("npm")

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
            f"-PmirrorHomeCertificateSha256={DUMMY_CERTIFICATE}",
            f"-PmirrorOtaBootstrapTokenSha256={DUMMY_CERTIFICATE}",
            "--no-daemon",
        ]
    )
    run([npm, "--prefix", str(REPO / "companion"), "test"])
    run([npm, "--prefix", str(REPO / "companion"), "run", "build"])
    run([npm, "--prefix", str(REPO / "companion"), "run", "typecheck"])
    run([npm, "--prefix", str(REPO / "companion"), "audit", "--omit=dev", "--audit-level=high"])
    run(["git", "diff", "--check"])


if __name__ == "__main__":
    main()
