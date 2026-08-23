#!/usr/bin/env python3
"""Cross-platform setup and recovery CLI for supported MIRROR devices."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

from janus_wrap import sha256, write_guarded


REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = REPO / "tools" / "device-profiles" / "ifc6309-mirror-329.json"
KIOSK_SETTINGS = (
    ("global", "stay_on_while_plugged_in"),
    ("system", "screen_off_timeout"),
    ("secure", "immersive_mode_confirmations"),
)


def executable(name: str) -> pathlib.Path:
    found = shutil.which(name)
    if found:
        return pathlib.Path(found)
    candidates: list[pathlib.Path] = []
    for variable in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        if os.environ.get(variable):
            candidates.append(pathlib.Path(os.environ[variable]) / "platform-tools" / f"{name}.exe")
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        candidates.append(
            pathlib.Path(os.environ["LOCALAPPDATA"])
            / "Android"
            / "Sdk"
            / "platform-tools"
            / f"{name}.exe"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"Unable to find {name}; install Android platform-tools")


def run(arguments: list[str], *, capture: bool = True) -> str:
    completed = subprocess.run(
        arguments,
        check=True,
        text=True,
        capture_output=capture,
    )
    return completed.stdout.strip() if capture else ""


def parse_adb_devices(output: str) -> list[str]:
    devices = []
    for line in output.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "device":
            devices.append(fields[0])
    return devices


class Device:
    def __init__(self, serial: str | None = None):
        self.adb = executable("adb")
        if serial is None:
            devices = parse_adb_devices(run([str(self.adb), "devices", "-l"]))
            if len(devices) != 1:
                raise RuntimeError(
                    f"Expected exactly one authorized Android device, found {len(devices)}"
                )
            serial = devices[0]
        self.serial = serial

    def command(self, *arguments: str) -> str:
        return run([str(self.adb), "-s", self.serial, *arguments])

    def shell(self, *arguments: str) -> str:
        return self.command("shell", *arguments)

    def property(self, name: str) -> str:
        return self.shell("getprop", name).strip()


def load_profile(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_device(device: Device, profile: dict) -> None:
    observed = {
        "product": device.property("ro.product.name"),
        "device": device.property("ro.product.device"),
        "boardPlatform": device.property("ro.board.platform"),
        "androidRelease": device.property("ro.build.version.release"),
        "buildId": device.property("ro.build.id"),
        "fingerprint": device.property("ro.build.fingerprint"),
    }
    mismatches = {
        name: (profile[name], value)
        for name, value in observed.items()
        if profile.get(name) != value
    }
    if mismatches:
        details = ", ".join(
            f"{name}: expected {expected!r}, received {actual!r}"
            for name, (expected, actual) in mismatches.items()
        )
        raise RuntimeError(f"Connected device does not match profile: {details}")


def backup(device: Device, profile: dict, destination: pathlib.Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for package_name, metadata in profile["systemApks"].items():
        output = destination / f"{package_name}.apk"
        device.command("pull", metadata["path"], str(output))
        actual = sha256(output)
        if actual != metadata["sha256"]:
            raise RuntimeError(
                f"Backup hash mismatch for {package_name}: "
                f"expected {metadata['sha256']}, received {actual}"
            )


def save_kiosk_settings(device: Device, destination: pathlib.Path) -> None:
    if destination.is_file():
        return
    values = {}
    for namespace, key in KIOSK_SETTINGS:
        value = device.shell("settings", "get", namespace, key).strip()
        values[f"{namespace}.{key}"] = None if value == "null" else value
    destination.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")


def restore_kiosk_settings(device: Device, source: pathlib.Path) -> None:
    if not source.is_file():
        return
    values = json.loads(source.read_text(encoding="utf-8"))
    for namespace, key in KIOSK_SETTINGS:
        value = values.get(f"{namespace}.{key}")
        if value is None:
            device.shell("settings", "delete", namespace, key)
        else:
            device.shell("settings", "put", namespace, key, str(value))


def gradle(*arguments: str) -> None:
    wrapper = REPO / ("gradlew.bat" if os.name == "nt" else "gradlew")
    run([str(wrapper), "-p", str(REPO), *arguments], capture=False)


def find_build_tool(name: str) -> pathlib.Path:
    sdk = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if not sdk and os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        sdk = str(pathlib.Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk")
    if not sdk:
        raise RuntimeError("ANDROID_SDK_ROOT or ANDROID_HOME is required")
    if os.name == "nt":
        suffix = f"{name}.bat" if name in {"apksigner", "d8"} else f"{name}.exe"
    else:
        suffix = name
    candidates = sorted(
        pathlib.Path(sdk).glob(f"build-tools/*/{suffix}"),
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"Unable to find {name} in the Android SDK")
    return candidates[0]


def certificate_sha256(apk: pathlib.Path) -> str:
    output = run(
        [str(find_build_tool("apksigner")), "verify", "--print-certs", str(apk)]
    )
    match = re.search(r"certificate SHA-256 digest: ([0-9a-fA-F]{64})", output)
    if not match:
        raise RuntimeError("Unable to read APK signing-certificate digest")
    return match.group(1).lower()


def verify_helper_manifest(apk: pathlib.Path, profile: dict) -> None:
    contract = profile["helperContract"]
    aapt = find_build_tool("aapt")
    badging = run([str(aapt), "dump", "badging", str(apk)])
    if f"package: name='{contract['package']}'" not in badging:
        raise RuntimeError("Stock helper target package does not match its contract")

    manifest = run(
        [str(aapt), "dump", "xmltree", str(apk), "AndroidManifest.xml"]
    )
    required_values = [
        contract["sharedUserId"],
        *contract["requiredComponents"],
    ]
    missing = [value for value in required_values if f'"{value}"' not in manifest]
    if missing:
        raise RuntimeError(
            "Stock helper manifest is missing required values: " + ", ".join(missing)
        )

    service_marker = f'"{contract["bindService"]}"'
    service_position = manifest.find(service_marker)
    service_block = manifest[service_position : service_position + 600]
    if contract.get("bindServiceExported") and (
        "android:exported" not in service_block or "0xffffffff" not in service_block
    ):
        raise RuntimeError("Stock helper bind service is not exported as required")


def install_home(device: Device) -> None:
    gradle(":android:mirror-home:assembleDebug", "--no-daemon")
    apk = (
        REPO
        / "android"
        / "mirror-home"
        / "build"
        / "outputs"
        / "apk"
        / "debug"
        / "mirror-home-debug.apk"
    )
    result = device.command("install", "-r", "-g", str(apk))
    if "Success" not in result:
        raise RuntimeError(f"Mirror Home installation failed: {result}")
    device.shell("am", "start", "-n", "dev.mirror.repurpose/.MainActivity")


def install_helper(device: Device, profile_path: pathlib.Path, profile: dict) -> None:
    backup_dir = REPO / "backups" / profile["id"]
    backup(device, profile, backup_dir)
    save_kiosk_settings(device, backup_dir / "kiosk-settings.json")

    home_apk = (
        REPO
        / "android"
        / "mirror-home"
        / "build"
        / "outputs"
        / "apk"
        / "debug"
        / "mirror-home-debug.apk"
    )
    if not home_apk.is_file():
        gradle(":android:mirror-home:assembleDebug", "--no-daemon")
    certificate = certificate_sha256(home_apk)
    gradle(
        ":android:system-helper:assembleDebug",
        "--no-daemon",
        f"-PmirrorHomeCertificateSha256={certificate}",
    )

    helper_apk = (
        REPO
        / "android"
        / "system-helper"
        / "build"
        / "outputs"
        / "apk"
        / "debug"
        / "system-helper-debug.apk"
    )
    generated = REPO / "generated" / profile["id"]
    generated.mkdir(parents=True, exist_ok=True)
    dex_path = generated / "system-helper.dex"
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary = pathlib.Path(temporary_directory)
        with zipfile.ZipFile(helper_apk) as archive:
            dex_entries = sorted(
                name
                for name in archive.namelist()
                if re.fullmatch(r"classes(?:[0-9]+)?\.dex", name)
            )
            if not dex_entries:
                raise RuntimeError("System-helper APK contains no DEX payload")
            dex_inputs = []
            for entry in dex_entries:
                extracted = temporary / entry
                extracted.write_bytes(archive.read(entry))
                dex_inputs.append(extracted)

        merged = temporary / "merged"
        merged.mkdir()
        run(
            [
                str(find_build_tool("d8")),
                "--min-api",
                "23",
                "--output",
                str(merged),
                *[str(item) for item in dex_inputs],
            ],
            capture=False,
        )
        merged_dex = sorted(merged.glob("classes*.dex"))
        if len(merged_dex) != 1:
            raise RuntimeError(
                f"Expected one merged helper DEX, received {len(merged_dex)}"
            )
        dex_path.write_bytes(merged_dex[0].read_bytes())

    source_apk = backup_dir / "co.mirror.datacap.apk"
    verify_helper_manifest(source_apk, profile)
    wrapped_apk = generated / "co.mirror.datacap-system-helper.apk"
    write_guarded(
        dex_path,
        source_apk,
        wrapped_apk,
        profile_path,
        "co.mirror.datacap",
    )
    try:
        result = device.command("install", "-r", str(wrapped_apk))
        if "Success" not in result:
            raise RuntimeError(f"System helper installation failed: {result}")
        path = device.shell("pm", "path", "co.mirror.datacap")
        if "/data/app/" not in path:
            raise RuntimeError("System helper did not install as an update")
        device.shell(
            "am",
            "startservice",
            "-n",
            "co.mirror.datacap/.DataCapIntentService",
        )
        time.sleep(2)
        if "co.mirror.datacap" not in device.shell("ps"):
            raise RuntimeError("System helper process did not start")
        print(
            "System helper installed for this boot only. "
            "Run restore-helper before rebooting."
        )
    except Exception:
        restore_helper(device, profile)
        raise


def restore_helper(
    device: Device,
    profile: dict,
    *,
    restore_settings: bool = True,
) -> None:
    expected = "/system/app/co.mirror.datacap/co.mirror.datacap.apk"
    current_path = device.shell("pm", "path", "co.mirror.datacap")
    if expected in current_path:
        if restore_settings:
            restore_kiosk_settings(
                device,
                REPO / "backups" / profile["id"] / "kiosk-settings.json",
            )
        return
    device.shell("am", "force-stop", "co.mirror.datacap")
    result = device.shell("pm", "uninstall", "-k", "co.mirror.datacap")
    if "Success" not in result:
        raise RuntimeError(f"Unable to remove system-helper update: {result}")
    path = device.shell("pm", "path", "co.mirror.datacap")
    if expected not in path:
        raise RuntimeError(f"Factory APK was not restored; observed {path!r}")
    if restore_settings:
        restore_kiosk_settings(
            device,
            REPO / "backups" / profile["id"] / "kiosk-settings.json",
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=pathlib.Path, default=DEFAULT_PROFILE)
    parser.add_argument("--serial")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("backup")
    subparsers.add_parser("build")
    subparsers.add_parser("install-home")
    subparsers.add_parser("install-helper")
    restore_parser = subparsers.add_parser("restore-helper")
    restore_parser.add_argument(
        "--keep-kiosk-settings",
        action="store_true",
        help="Remove the helper update without restoring captured kiosk settings",
    )
    forward_parser = subparsers.add_parser("forward")
    forward_parser.add_argument("--host-port", type=int, default=18787)
    args = parser.parse_args()

    profile = load_profile(args.profile)
    if args.command == "build":
        gradle(
            ":android:mirror-home:assembleDebug",
            ":android:system-helper:assembleDebug",
            "--no-daemon",
        )
        return

    device = Device(args.serial)
    verify_device(device, profile)
    if args.command == "status":
        print(json.dumps({"serial": device.serial, "profile": profile["id"]}, indent=2))
    elif args.command == "backup":
        backup(device, profile, REPO / "backups" / profile["id"])
    elif args.command == "install-home":
        install_home(device)
    elif args.command == "install-helper":
        install_helper(device, args.profile, profile)
    elif args.command == "restore-helper":
        restore_helper(
            device,
            profile,
            restore_settings=not args.keep_kiosk_settings,
        )
    elif args.command == "forward":
        device.command(
            "forward",
            f"tcp:{args.host_port}",
            f"tcp:{8787}",
        )
        print(f"http://127.0.0.1:{args.host_port}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
