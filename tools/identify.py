#!/usr/bin/env python3
"""Reads what a MIRROR is and writes a device report, changing nothing on it.

With ADB authorized it reads Android's properties, the kernel's release, the
partition sizes and the factory packages; with the unit in fastboot it reads
what the bootloader answers to ``getvar all``. It never reboots, writes,
installs or authorizes anything. The report follows
``profiles/schema/device-report.schema.json`` and names the board profile in
``profiles/boards`` that the unit matches, if any. It holds no serial number,
network name, key or account, so it can be pasted into an issue.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys
from typing import Callable

REPO = pathlib.Path(__file__).resolve().parents[1]
PROFILES = REPO / "profiles" / "boards"
SCHEMAS = REPO / "profiles" / "schema"
REPORT_SCHEMA = "mirror-device-report/1"
SUPPORTED_PLATFORM = "msm8916"
PACKAGE_PREFIXES = ("com.mirror.", "co.mirror.", "dev.mirror.")
PRIVATE_BOOTLOADER_VARIABLES = {"serialno"}

# (exit code, standard output, standard error)
Result = tuple[int, str, str]
Runner = Callable[[list[str]], Result]


def run_tool(arguments: list[str]) -> Result:
    try:
        completed = subprocess.run(arguments, text=True, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, "", str(error)
    return completed.returncode, completed.stdout, completed.stderr


class Tools:
    """adb and fastboot, through a runner that tests replace."""

    def __init__(self, adb: str | None, fastboot: str | None, runner: Runner = run_tool):
        self.adb_path = adb
        self.fastboot_path = fastboot
        self.runner = runner

    def adb(self, *arguments: str) -> Result:
        if not self.adb_path:
            return 1, "", "adb is not installed"
        return self.runner([self.adb_path, *arguments])

    def fastboot(self, *arguments: str) -> Result:
        if not self.fastboot_path:
            return 1, "", "fastboot is not installed"
        return self.runner([self.fastboot_path, *arguments])


def find_tool(name: str) -> str | None:
    try:
        from mirrorctl import executable
        return str(executable(name))
    except RuntimeError:
        return None


# ---------------------------------------------------------------------------
# Parsing what the tools print.
# ---------------------------------------------------------------------------


def parse_adb_devices(output: str) -> list[tuple[str, str]]:
    """(serial, state) for each line of ``adb devices``."""
    found = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and not line.startswith(("List of", "*")):
            found.append((fields[0], fields[1]))
    return found


def parse_fastboot_devices(output: str) -> list[str]:
    return [line.split()[0] for line in output.splitlines() if line.split()[1:2] == ["fastboot"]]


def parse_getprop(output: str) -> dict[str, str]:
    return dict(re.findall(r"^\[([^\]]+)\]: \[(.*)\]\s*$", output, re.MULTILINE))


# Variables whose names carry a partition, as in "partition-size:system: 0x...".
QUALIFIED_VARIABLES = {"partition-size", "partition-type", "has-slot", "is-logical"}


def parse_getvar_all(output: str) -> dict[str, str]:
    """Variables from ``fastboot getvar all``, which prints them on standard error.

    The bootloader sends each as ``name:value``; some values begin with spaces.
    """
    variables = {}
    for line in output.splitlines():
        if not line.startswith("(bootloader) "):
            continue
        name, separator, value = line[len("(bootloader) "):].partition(":")
        if name in QUALIFIED_VARIABLES:
            qualifier, separator, value = value.partition(":")
            name = f"{name}:{qualifier}"
        if separator and name.strip():
            variables[name.strip()] = value.strip()
    return variables


def parse_partitions(by_name: str, proc_partitions: str) -> list[dict]:
    """Named partitions with their sizes, from the by-name links and /proc/partitions."""
    blocks = {}
    for line in proc_partitions.splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[2].isdigit():
            blocks[fields[3]] = int(fields[2]) * 1024
    partitions = []
    for line in by_name.splitlines():
        match = re.search(r"\s(\S+) -> \S*/(\S+)$", line)
        if match and match.group(2) in blocks:
            partitions.append({"name": match.group(1), "sizeBytes": blocks[match.group(2)]})
    return sorted(partitions, key=lambda partition: partition["name"])


def bootloader_partitions(variables: dict[str, str]) -> list[dict]:
    partitions = []
    for name, value in variables.items():
        if name.startswith("partition-size:"):
            try:
                partitions.append({"name": name.split(":", 1)[1], "sizeBytes": int(value, 16)})
            except ValueError:
                pass
    return sorted(partitions, key=lambda partition: partition["name"])


# ---------------------------------------------------------------------------
# Reading the unit.
# ---------------------------------------------------------------------------


def find_unit(tools: Tools, serial: str | None) -> tuple[str, str | None]:
    """The unit's state and serial; raises if more than one could be meant.

    Devices ADB lists as offline (an emulator that quit, a phone mid-reboot)
    are left out unless asked for by serial: they cannot be read.
    """
    _, adb_out, _ = tools.adb("devices")
    _, fastboot_out, _ = tools.fastboot("devices")
    units = [(found, state) for found, state in parse_adb_devices(adb_out)]
    units += [(found, "fastboot") for found in parse_fastboot_devices(fastboot_out)]
    if serial:
        units = [unit for unit in units if unit[0] == serial]
    else:
        units = [unit for unit in units if unit[1] != "offline"]
    if len(units) > 1:
        listed = ", ".join(f"{found} ({state})" for found, state in units)
        raise RuntimeError(f"More than one unit is connected: {listed}. Choose one with --serial.")
    if not units:
        return "none", serial
    found, state = units[0]
    if state == "device":
        return "android", found
    if state == "fastboot":
        return "fastboot", found
    if state == "unauthorized":
        return "android-unauthorized", found
    raise RuntimeError(f"{found} is in the state {state!r}; start it into Android or fastboot.")


def read_android(tools: Tools, serial: str) -> dict:
    def shell(*arguments: str) -> str:
        code, out, _ = tools.adb("-s", serial, "shell", *arguments)
        return out if code == 0 else ""

    props = parse_getprop(shell("getprop"))
    sdk = props.get("ro.build.version.sdk", "")
    android = {
        "fingerprint": props.get("ro.build.fingerprint", ""),
        "release": props.get("ro.build.version.release", ""),
        "sdk": int(sdk) if sdk.isdigit() else None,
        "product": props.get("ro.product.name", ""),
        "device": props.get("ro.product.device", ""),
        "platform": props.get("ro.board.platform", ""),
        "hardware": props.get("ro.hardware", ""),
        "buildId": props.get("ro.build.id", ""),
        "buildType": props.get("ro.build.type", ""),
        "mirrorOsVersion": props.get("ro.build.version.mirror") or None,
    }
    kernel = {"release": shell("uname", "-r").strip(), "version": shell("cat", "/proc/version").strip()}
    partitions = parse_partitions(
        shell("ls", "-l", "/dev/block/bootdevice/by-name/"), shell("cat", "/proc/partitions")
    )
    packages = []
    for line in shell("pm", "list", "packages").splitlines():
        name = line.strip().removeprefix("package:")
        if name.startswith(PACKAGE_PREFIXES):
            version = re.search(r"versionName=(\S+)", shell("dumpsys", "package", name))
            packages.append({"name": name, "versionName": version.group(1) if version else None})
    return {
        "android": android,
        "kernel": kernel if kernel["release"] else None,
        "partitions": partitions,
        "packages": sorted(packages, key=lambda package: package["name"]),
    }


def read_bootloader(tools: Tools, serial: str) -> dict:
    code, out, err = tools.fastboot("-s", serial, "getvar", "all")
    variables = parse_getvar_all(err + "\n" + out)
    if code != 0 and not variables:
        raise RuntimeError(f"fastboot getvar all failed: {(err or out).strip()}")
    return {
        "bootloader": {
            name: value for name, value in sorted(variables.items())
            if name not in PRIVATE_BOOTLOADER_VARIABLES
        },
        "partitions": bootloader_partitions(variables),
    }


# ---------------------------------------------------------------------------
# Matching a profile.
# ---------------------------------------------------------------------------


def load_profiles(folder: pathlib.Path = PROFILES) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))]


def match_profile(report: dict, profiles: list[dict]) -> dict:
    notes: list[str] = []
    android, kernel, label = report["android"], report["kernel"], report["board"]["label"]
    found = None
    if report["state"] == "none":
        notes.append("No unit was found over USB. Check the cable and that it carries data.")
    elif report["state"] == "android-unauthorized":
        notes.append(
            "ADB sees this unit, but it has not trusted this computer, so nothing more can be read. "
            "See docs/getting-started.md."
        )
    elif report["state"] == "fastboot":
        notes.append(
            "In fastboot the kernel cannot be read, so no profile can be chosen. "
            "Start Android with ADB authorized and run this again, or give --board-label."
        )
    elif android and android["platform"] != SUPPORTED_PLATFORM:
        notes.append(
            f"This unit's platform is {android['platform'] or 'unknown'}, not {SUPPORTED_PLATFORM}: "
            "it is not the Qualcomm generation this project supports."
        )
    elif kernel:
        for profile in profiles:
            wanted = profile["match"]
            if wanted["kernelRelease"] == kernel["release"] and wanted["platform"] == android["platform"]:
                found = profile
                break
        if not found:
            notes.append(
                f"Kernel {kernel['release']} is not in profiles/boards yet. "
                "Please open an issue with this report and your printed board label."
            )
    if found:
        if found["evidence"] == "reported":
            notes.append(f"Profile {found['id']} comes from an owner's report and has not been seen by this project.")
        if label and found["board"]["labels"] and label not in found["board"]["labels"]:
            notes.append(
                f"The board label {label} differs from the labels this kernel was seen on: "
                + ", ".join(found["board"]["labels"])
                + "."
            )
        if found["install"] is None:
            notes.append("No Silvering image has been accepted for this profile yet, so it cannot be installed.")
    installable = bool(found and found["install"] is not None and found["evidence"] == "observed")
    return {
        "profile": found["id"] if found else None,
        "evidence": found["evidence"] if found else None,
        "installable": installable,
        "notes": notes,
    }


def identify(tools: Tools, serial: str | None, label: str | None, profiles: list[dict], version: str) -> dict:
    state, found = find_unit(tools, serial)
    report = {
        "schema": REPORT_SCHEMA,
        "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "tool": {"name": "identify", "version": version},
        "state": state,
        "board": {"label": label},
        "android": None,
        "kernel": None,
        "partitions": [],
        "packages": [],
        "bootloader": None,
        "match": {},
    }
    if state == "android":
        report.update(read_android(tools, found))
    elif state == "fastboot":
        report.update(read_bootloader(tools, found))
    report["match"] = match_profile(report, profiles)
    return report


# ---------------------------------------------------------------------------
# A small JSON Schema check, for the parts of the language the schemas use,
# so that neither CI nor an owner needs a package to check a report.
# ---------------------------------------------------------------------------

JSON_TYPES = {
    "object": dict, "array": list, "string": str, "integer": int,
    "number": (int, float), "boolean": bool, "null": type(None),
}


def schema_errors(value, schema: dict, where: str = "$") -> list[str]:
    errors = []
    if "const" in schema and value != schema["const"]:
        return [f"{where}: expected {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: {value!r} is not one of {schema['enum']}"]
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        fits = any(
            isinstance(value, JSON_TYPES[kind]) and not (kind in ("integer", "number") and isinstance(value, bool))
            for kind in types
        )
        if not fits:
            return [f"{where}: {type(value).__name__} is not {' or '.join(types)}"]
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        errors.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{where}: fewer than {schema['minItems']} items")
        for index, item in enumerate(value):
            errors += schema_errors(item, schema.get("items", {}), f"{where}[{index}]")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{where}: {name} is missing")
        for name, item in value.items():
            if name in properties:
                errors += schema_errors(item, properties[name], f"{where}.{name}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{where}: {name} is not allowed")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += schema_errors(item, schema["additionalProperties"], f"{where}.{name}")
    return errors


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


def repository_version() -> str:
    code, out, _ = run_tool(["git", "-C", str(REPO), "describe", "--always", "--dirty"])
    return out.strip() if code == 0 and out.strip() else "unknown"


def summary(report: dict) -> str:
    lines = [f"State: {report['state']}"]
    if report["android"]:
        android = report["android"]
        lines.append(f"Android {android['release']} ({android['fingerprint']})")
        if android["mirrorOsVersion"]:
            lines.append(f"MirrorOS {android['mirrorOsVersion']}")
    if report["kernel"]:
        lines.append(f"Kernel {report['kernel']['release']}")
    if report["bootloader"]:
        for name in ("product", "version-bootloader", "secure", "unlocked"):
            if name in report["bootloader"]:
                lines.append(f"Bootloader {name}: {report['bootloader'][name]}")
    if report["partitions"]:
        lines.append(f"{len(report['partitions'])} partitions read")
    match = report["match"]
    lines.append(f"Profile: {match['profile'] or 'none'}" + (f" ({match['evidence']})" if match["evidence"] else ""))
    lines.append(f"Silvering can be installed: {'yes' if match['installable'] else 'no'}")
    lines += [f"- {note}" for note in match["notes"]]
    return "\n".join(lines)


ERROR_SCHEMA = "mirror-identify-error/1"


def main(argv: list[str] | None = None, tools: Tools | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Exit codes: 0 report written, 1 the unit could not be read, 2 adb and fastboot are missing.",
    )
    parser.add_argument("--serial", help="the unit to read, when more than one is connected")
    parser.add_argument("--board-label", help="the board revision printed on the unit's board, such as RevP21")
    parser.add_argument("--output", type=pathlib.Path, help="where to write the report (default: print it)")
    parser.add_argument(
        "--json",
        action="store_true",
        help="for programs: print only JSON on standard output, errors included, and no summary",
    )
    arguments = parser.parse_args(argv)

    def fail(code: int, message: str) -> int:
        if arguments.json:
            sys.stdout.write(json.dumps({"schema": ERROR_SCHEMA, "exitCode": code, "error": message}) + "\n")
        else:
            print(message, file=sys.stderr)
        return code

    tools = tools or Tools(find_tool("adb"), find_tool("fastboot"))
    if not tools.adb_path and not tools.fastboot_path:
        return fail(2, "Neither adb nor fastboot was found; install Android platform-tools.")
    try:
        report = identify(tools, arguments.serial, arguments.board_label, load_profiles(), repository_version())
    except RuntimeError as error:
        return fail(1, str(error))
    errors = schema_errors(report, load_schema("device-report"))
    if errors:
        return fail(1, "The report does not follow its schema: " + "; ".join(errors))
    text = json.dumps(report, indent=2) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(text, encoding="utf-8")
    if arguments.json or not arguments.output:
        sys.stdout.write(text)
    if not arguments.json:
        print(summary(report), file=sys.stderr)
        if arguments.output:
            print(f"Report written to {arguments.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
