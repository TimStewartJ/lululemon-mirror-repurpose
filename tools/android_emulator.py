#!/usr/bin/env python3
"""Run the Android 6 emulator that stands in for a MIRROR during validation.

The MIRROR runs Android 6.0.1 with the Chromium 44 WebView. The stock
``system-images;android-23;default;x86_64`` SDK image has the same API level
and the same WebView generation, so Mirror Home's dashboard and control API
can be exercised without the physical unit. This module creates a private AVD
shaped like the Mirror's 1080x1920 portrait panel, boots it headless, and
shuts it down again. It never touches a physical device.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time


REPO = pathlib.Path(__file__).resolve().parents[1]
SYSTEM_IMAGE = "system-images;android-23;default;x86_64"
DEFAULT_AVD_NAME = "mirror-android6"
DEFAULT_AVD_HOME = REPO / "build" / "emulator" / "avd"
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
# What a Mirror's health report gives as device.display.densityDpi.
DEFAULT_DENSITY = 240
# A Mirror has just under 1 GB of memory and a 128 MB heap for each app.
MEMORY_MB = 1024
HEAP_MB = 128
BOOT_TIMEOUT_SECONDS = 420
FIRST_CONSOLE_PORT = 5580
LAST_CONSOLE_PORT = 5680


class EmulatorError(RuntimeError):
    pass


def sdk_root() -> pathlib.Path:
    candidates = [os.environ.get("ANDROID_SDK_ROOT"), os.environ.get("ANDROID_HOME")]
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        candidates.append(str(pathlib.Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk"))
    candidates.append(str(pathlib.Path.home() / "Android" / "Sdk"))
    candidates.append(str(pathlib.Path.home() / "Library" / "Android" / "sdk"))
    for candidate in candidates:
        if candidate and pathlib.Path(candidate).is_dir():
            return pathlib.Path(candidate)
    raise EmulatorError("Android SDK not found; set ANDROID_SDK_ROOT")


def executable_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def adb_path(sdk: pathlib.Path) -> pathlib.Path:
    found = shutil.which("adb")
    if found:
        return pathlib.Path(found)
    candidate = sdk / "platform-tools" / executable_name("adb")
    if candidate.is_file():
        return candidate
    raise EmulatorError("adb not found; install Android platform-tools")


def emulator_path(sdk: pathlib.Path) -> pathlib.Path:
    candidate = sdk / "emulator" / executable_name("emulator")
    if not candidate.is_file():
        raise EmulatorError(
            'Android Emulator not found; install it with: sdkmanager "emulator"'
        )
    return candidate


def system_image_relative() -> str:
    return SYSTEM_IMAGE.replace(";", os.sep) + os.sep


def require_system_image(sdk: pathlib.Path) -> pathlib.Path:
    directory = sdk / system_image_relative()
    if not (directory / "system.img").is_file():
        raise EmulatorError(
            f'Android 6 system image not found; install it with: sdkmanager "{SYSTEM_IMAGE}"'
        )
    return directory


def avd_config(
    name: str,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    density: int = DEFAULT_DENSITY,
) -> str:
    """config.ini for an AVD with the Mirror's panel and no phone hardware."""
    settings = {
        "AvdId": name,
        "PlayStore.enabled": "false",
        "abi.type": "x86_64",
        "avd.ini.encoding": "UTF-8",
        "disk.dataPartition.size": "2G",
        "hw.accelerometer": "no",
        "hw.audioInput": "no",
        "hw.audioOutput": "no",
        "hw.battery": "no",
        "hw.camera.back": "none",
        "hw.camera.front": "none",
        "hw.cpu.arch": "x86_64",
        "hw.cpu.ncore": "2",
        # A Mirror has no touchscreen, keys or pointer (see hw.keyboard,
        # hw.screen, hw.touchScreen and hw.trackBall too). Android 6 then shows
        # no "has stopped" dialogs, and Mirror Home takes the device for
        # unattended and keeps its dashboard in front.
        "hw.dPad": "no",
        "hw.gps": "no",
        "hw.gpu.enabled": "yes",
        "hw.gpu.mode": "swiftshader_indirect",
        "hw.initialOrientation": "portrait",
        "hw.keyboard": "no",
        "hw.keyboard.lid": "no",
        "hw.lcd.density": str(density),
        "hw.lcd.height": str(height),
        "hw.lcd.width": str(width),
        # Hardware keys mean no on-screen navigation bar, as on the Mirror.
        "hw.mainKeys": "yes",
        "hw.ramSize": str(MEMORY_MB),
        "hw.screen": "no-touch",
        "hw.sdCard": "no",
        "hw.sensors.orientation": "no",
        "hw.sensors.proximity": "no",
        "hw.touchScreen": "no",
        "hw.trackBall": "no",
        "image.sysdir.1": system_image_relative(),
        "showDeviceFrame": "no",
        "skin.dynamic": "no",
        "skin.name": f"{width}x{height}",
        "skin.path": "_no_skin",
        "tag.display": "Default",
        "tag.id": "default",
        "vm.heapSize": str(HEAP_MB),
    }
    return "".join(f"{key}={value}\n" for key, value in sorted(settings.items()))


def ensure_avd(
    avd_home: pathlib.Path,
    name: str,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    density: int = DEFAULT_DENSITY,
) -> pathlib.Path:
    """Write the AVD's two ini files; rewriting them is harmless."""
    directory = avd_home / f"{name}.avd"
    directory.mkdir(parents=True, exist_ok=True)
    (avd_home / f"{name}.ini").write_text(
        f"avd.ini.encoding=UTF-8\npath={directory}\ntarget=android-23\n",
        encoding="ascii",
    )
    (directory / "config.ini").write_text(
        avd_config(name, width=width, height=height, density=density),
        encoding="ascii",
    )
    return directory


def emulator_arguments(name: str, port: int, *, wipe_data: bool, window: bool) -> list[str]:
    arguments = [
        "-avd",
        name,
        "-port",
        str(port),
        "-no-audio",
        "-no-boot-anim",
        "-no-snapshot",
        "-no-metrics",
        # Keeps guest memory out of a file as large as the RAM in the AVD.
        "-feature",
        "-QuickbootFileBacked",
        "-gpu",
        "swiftshader_indirect",
        "-netdelay",
        "none",
        "-netspeed",
        "full",
    ]
    if not window:
        arguments.append("-no-window")
    if wipe_data:
        arguments.append("-wipe-data")
    return arguments


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def free_console_port() -> int:
    """An even console port whose ADB neighbour is free too."""
    for port in range(FIRST_CONSOLE_PORT, LAST_CONSOLE_PORT + 1, 2):
        if port_is_free(port) and port_is_free(port + 1):
            return port
    raise EmulatorError("No free emulator console port")


def is_emulator_serial(serial: str) -> bool:
    return serial.startswith("emulator-") and serial[len("emulator-"):].isdigit()


class Emulator:
    """One headless Android 6 emulator, owned from start to stop."""

    def __init__(
        self,
        *,
        avd_home: pathlib.Path = DEFAULT_AVD_HOME,
        name: str = DEFAULT_AVD_NAME,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        density: int = DEFAULT_DENSITY,
        wipe_data: bool = True,
        window: bool = False,
        log_path: pathlib.Path | None = None,
    ):
        self.sdk = sdk_root()
        self.adb = adb_path(self.sdk)
        self.avd_home = avd_home
        self.name = name
        self.wipe_data = wipe_data
        self.window = window
        self.log_path = log_path
        self.port = 0
        self.process: subprocess.Popen | None = None
        self._log = None
        require_system_image(self.sdk)
        ensure_avd(avd_home, name, width=width, height=height, density=density)

    @property
    def serial(self) -> str:
        return f"emulator-{self.port}"

    def start(self) -> None:
        self.port = free_console_port()
        environment = dict(os.environ)
        environment["ANDROID_AVD_HOME"] = str(self.avd_home)
        environment["ANDROID_SDK_ROOT"] = str(self.sdk)
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [
                str(emulator_path(self.sdk)),
                *emulator_arguments(
                    self.name, self.port, wipe_data=self.wipe_data, window=self.window
                ),
            ],
            env=environment,
            stdout=self._log or subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
        )

    def wait_for_boot(self, timeout: float = BOOT_TIMEOUT_SECONDS) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise EmulatorError(
                    f"Emulator exited with code {self.process.returncode} before booting"
                )
            if boot_completed(self.adb, self.serial):
                return
            time.sleep(2)
        raise EmulatorError(f"Emulator did not finish booting within {int(timeout)} s")

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            subprocess.run(
                [str(self.adb), "-s", self.serial, "emu", "kill"],
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            # Its exit code is not meaningful: some versions fault on the way out.
            self.process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            terminate_tree(self.process)
            self.process.wait(timeout=30)
        self.process = None
        if self._log is not None:
            self._log.close()
            self._log = None
        if self.wipe_data:
            discard_disks(self.avd_home / f"{self.name}.avd")

    def __enter__(self) -> "Emulator":
        self.start()
        return self

    def __exit__(self, *_exception) -> None:
        self.stop()


def discard_disks(avd_directory: pathlib.Path) -> None:
    """Delete what a finished run left in the AVD, keeping only its settings.

    The disk images run to several gigabytes and the next run starts from
    empty storage anyway. Best effort: a file still held open is left behind.
    """
    if not avd_directory.is_dir():
        return
    for entry in avd_directory.iterdir():
        if entry.name == "config.ini":
            continue
        try:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()
        except OSError:
            pass


def terminate_tree(process: subprocess.Popen) -> None:
    """The launcher starts the virtual machine as a child; end both."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
        )
    else:
        import signal

        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()


def boot_completed(adb: pathlib.Path, serial: str) -> bool:
    try:
        completed = subprocess.run(
            [str(adb), "-s", serial, "shell", "getprop", "sys.boot_completed"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "1"


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--window", action="store_true", help="show the emulator window")
    parser.add_argument("--keep-data", action="store_true", help="do not reset user data")
    parser.add_argument("--density", type=int, default=DEFAULT_DENSITY)
    options = parser.parse_args(arguments)
    try:
        emulator = Emulator(
            wipe_data=not options.keep_data,
            window=options.window,
            density=options.density,
            log_path=REPO / "build" / "emulator" / "emulator.log",
        )
        emulator.start()
        print(f"Starting {emulator.serial}; waiting for Android 6 to boot")
        emulator.wait_for_boot()
        print(f"{emulator.serial} is ready. Press Ctrl+C to shut it down.")
        try:
            while emulator.process is not None and emulator.process.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        emulator.stop()
        return 0
    except EmulatorError as error:
        print(f"android_emulator: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
