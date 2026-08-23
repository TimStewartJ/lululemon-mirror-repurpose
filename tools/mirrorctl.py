#!/usr/bin/env python3
"""Cross-platform setup and recovery CLI for supported MIRROR devices."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
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
    def version_key(path: pathlib.Path) -> tuple:
        return tuple(
            int(part) if part.isdigit() else part
            for part in re.split(r"[.-]", path.parent.name)
        )

    candidates = sorted(
        pathlib.Path(sdk).glob(f"build-tools/*/{suffix}"),
        key=version_key,
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


def home_apk(variant: str) -> pathlib.Path:
    variant_title = variant.capitalize()
    gradle(f":android:mirror-home:assemble{variant_title}", "--no-daemon")
    apk = (
        REPO
        / "android"
        / "mirror-home"
        / "build"
        / "outputs"
        / "apk"
        / variant
        / f"mirror-home-{variant}.apk"
    )
    if not apk.is_file():
        if variant == "release":
            raise RuntimeError(
                "Signed release APK was not produced. Copy "
                "keystore.properties.example to keystore.properties and "
                "configure a private release key."
            )
        raise RuntimeError(f"Expected APK was not produced: {apk}")
    return apk


def apk_package_and_version(apk: pathlib.Path) -> tuple[str, str]:
    badging = run([str(find_build_tool("aapt")), "dump", "badging", str(apk)])
    match = re.search(
        r"package: name='([^']+)' versionCode='[^']+' versionName='([^']+)'",
        badging,
    )
    if not match:
        raise RuntimeError(f"Unable to read package metadata from {apk}")
    return match.group(1), match.group(2)


def installed_apk_path(device: Device, package_name: str) -> str | None:
    paths = [
        line.removeprefix("package:").strip()
        for line in device.shell("pm", "path", package_name).splitlines()
        if line.startswith("package:")
    ]
    base_paths = [path for path in paths if path.endswith("/base.apk")]
    candidates = base_paths or paths
    if not candidates:
        return None
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected one installed APK for {package_name}, received {paths!r}"
        )
    return candidates[0]


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def installed_package_version(device: Device, package_name: str) -> str | None:
    output = device.shell("dumpsys", "package", package_name)
    match = re.search(r"^\s*versionName=(.+?)\s*$", output, re.MULTILINE)
    return match.group(1) if match else None


def wait_home_health(
    device: Device,
    expected_version: str,
    *,
    timeout_seconds: int = 60,
) -> None:
    host_port = free_tcp_port()
    local = f"tcp:{host_port}"
    device.command("forward", local, "tcp:8787")
    try:
        device.shell("am", "start", "-n", "dev.mirror.repurpose/.MainActivity")
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{host_port}/api/v1/status",
                    timeout=3,
                ) as response:
                    status = json.loads(response.read().decode("utf-8"))
                reported_version = status.get("appVersion")
                version_matches = (
                    reported_version == expected_version
                    if reported_version is not None
                    else installed_package_version(
                        device,
                        "dev.mirror.repurpose",
                    ) == expected_version
                )
                if status.get("apiVersion") == 1 and version_matches:
                    activity = device.shell("dumpsys", "activity", "activities")
                    if "dev.mirror.repurpose/.MainActivity" in activity:
                        return
            except (OSError, ValueError, urllib.error.URLError) as error:
                last_error = error
            time.sleep(1)
        detail = f": {last_error}" if last_error else ""
        raise RuntimeError(f"Mirror Home failed its post-install health check{detail}")
    finally:
        try:
            device.command("forward", "--remove", local)
        except subprocess.CalledProcessError:
            pass


def install_home(device: Device, profile: dict, variant: str) -> None:
    apk = home_apk(variant)
    package_name, version = apk_package_and_version(apk)
    if package_name != "dev.mirror.repurpose":
        raise RuntimeError(f"Unexpected Mirror Home package {package_name!r}")

    remote_apk = installed_apk_path(device, package_name)
    backup_apk: pathlib.Path | None = None
    previous_version: str | None = None
    if remote_apk:
        installed_certificate = installed_certificate_sha256(device, package_name)
        candidate_certificate = certificate_sha256(apk)
        if installed_certificate != candidate_certificate:
            raise RuntimeError(
                "Candidate signing certificate does not match the installed Mirror Home"
            )
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
        backup_dir = (
            REPO
            / "backups"
            / profile["id"]
            / "home-updates"
            / timestamp
        )
        backup_dir.mkdir(parents=True, exist_ok=False)
        partial_backup = backup_dir / "base.apk.partial"
        device.command("pull", remote_apk, str(partial_backup))
        apk_package_and_version(partial_backup)
        backup_apk = backup_dir / "base.apk"
        partial_backup.replace(backup_apk)
        _, previous_version = apk_package_and_version(backup_apk)

    try:
        result = device.command("install", "-r", "-g", str(apk))
        if "Success" not in result:
            raise RuntimeError(f"Mirror Home installation failed: {result}")
        wait_home_health(device, version)
    except Exception as install_error:
        if backup_apk is None or previous_version is None:
            try:
                device.command("uninstall", package_name)
            except subprocess.CalledProcessError:
                pass
            raise
        rollback = device.command("install", "-r", "-d", "-g", str(backup_apk))
        if "Success" not in rollback:
            raise RuntimeError(
                "Mirror Home update failed and automatic rollback also failed: "
                f"{rollback}"
            ) from install_error
        wait_home_health(device, previous_version)
        raise RuntimeError(
            "Mirror Home update failed; the previous signed APK was restored"
        ) from install_error
    print(f"Mirror Home {version} installed and health-checked successfully")


def rollback_home(
    device: Device,
    profile: dict,
    backup_apk: pathlib.Path | None,
) -> None:
    if backup_apk is None:
        backup_root = REPO / "backups" / profile["id"] / "home-updates"
        candidates = sorted(backup_root.glob("*/base.apk"), reverse=True)
        if not candidates:
            raise RuntimeError("No Mirror Home update backup is available")
        for candidate in candidates:
            try:
                package_name, _ = apk_package_and_version(candidate)
                if package_name == "dev.mirror.repurpose":
                    backup_apk = candidate
                    break
            except (OSError, RuntimeError, subprocess.CalledProcessError):
                continue
        if backup_apk is None:
            raise RuntimeError("No valid Mirror Home update backup is available")
    backup_apk = backup_apk.resolve()
    if not backup_apk.is_file():
        raise RuntimeError(f"Mirror Home backup does not exist: {backup_apk}")
    package_name, version = apk_package_and_version(backup_apk)
    if package_name != "dev.mirror.repurpose":
        raise RuntimeError("Rollback APK is not Mirror Home")
    if certificate_sha256(backup_apk) != installed_certificate_sha256(
        device,
        package_name,
    ):
        raise RuntimeError("Rollback APK signing certificate does not match")
    result = device.command("install", "-r", "-d", "-g", str(backup_apk))
    if "Success" not in result:
        raise RuntimeError(f"Mirror Home rollback failed: {result}")
    wait_home_health(device, version)
    print(f"Mirror Home rolled back to {version}")


def installed_certificate_sha256(device: Device, package_name: str) -> str:
    installed_path = installed_apk_path(device, package_name)
    if installed_path is None:
        raise RuntimeError(f"{package_name} is not installed")
    with tempfile.TemporaryDirectory() as temporary_directory:
        local_apk = pathlib.Path(temporary_directory) / f"{package_name}.apk"
        device.command("pull", installed_path, str(local_apk))
        return certificate_sha256(local_apk)


def install_helper(device: Device, profile_path: pathlib.Path, profile: dict) -> None:
    backup_dir = REPO / "backups" / profile["id"]
    backup(device, profile, backup_dir)
    save_kiosk_settings(device, backup_dir / "kiosk-settings.json")

    certificate = installed_certificate_sha256(device, "dev.mirror.repurpose")
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
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument(
        "--variant",
        choices=("debug", "release"),
        default="debug",
    )
    install_home_parser = subparsers.add_parser("install-home")
    install_home_parser.add_argument(
        "--variant",
        choices=("debug", "release"),
        default="release",
    )
    rollback_home_parser = subparsers.add_parser("rollback-home")
    rollback_home_parser.add_argument("--backup", type=pathlib.Path)
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
        variant_title = args.variant.capitalize()
        gradle(
            f":android:mirror-home:assemble{variant_title}",
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
        install_home(device, profile, args.variant)
    elif args.command == "rollback-home":
        rollback_home(device, profile, args.backup)
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
