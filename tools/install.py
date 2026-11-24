#!/usr/bin/env python3
"""The Silvering installer: the steps of silvering/FIRST-FLASH.md as commands.

Stages arrive one at a time; each says whether it reads or changes the unit.

    thermal   reads only: whether the kernel guards the chip against heat by
              itself, since Silvering has no thermal-engine (FIRST-FLASH,
              "Thermal protection"). Over Wi-Fi through Mirror Home
              (``--mirror CONFIG``) it checks the driver and its device tree;
              the kernel log can only be read over USB (``--serial``), so the
              Wi-Fi answer is at best ``incomplete``.

Exit codes: 0 the gate passes, 1 the unit could not be read, 3 Stop (the
gate fails), 4 incomplete (the part read passes; the kernel log is still to be
read over USB).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import identify  # noqa: E402

THERMAL_NODE = "qcom,msm-thermal"
KERNEL_LOG = re.compile(r"msm_thermal|tsens", re.IGNORECASE)
KERNEL_LOG_FAILURE = re.compile(r"fail|error|\berr\b|invalid|unable", re.IGNORECASE)
# Properties the runbook asks to note, as 32-bit cells.
NOTED_PROPERTIES = (
    "qcom,therm-reset-temp", "qcom,limit-temp", "qcom,temp-hysteresis", "qcom,freq-step",
    "qcom,freq-control-mask", "qcom,core-limit-temp", "qcom,core-temp-hysteresis",
    "qcom,core-control-mask", "qcom,hotplug-temp", "qcom,hotplug-temp-hysteresis",
    "qcom,sensor-id",
)

# What the gate rests on: anything unreadable here is a Stop; an unreadable zone is only noted.
GATE_PATHS = ("/proc/device-tree", "/sys/firmware/devicetree", "/sys/module/msm_thermal",
              "/sys/devices/system/cpu/possible")

PASS, STOP, INCOMPLETE = "pass", "stop", "incomplete"
EXIT_CODES = {PASS: 0, STOP: 3, INCOMPLETE: 4}


def cells(hex_value: str) -> list[int] | None:
    """Big-endian 32-bit cells of a device-tree property, or None if it is not a whole number of them."""
    if not re.fullmatch(r"(?:[0-9a-f]{8})+", hex_value or ""):
        return None
    return [int(hex_value[i:i + 8], 16) for i in range(0, len(hex_value), 8)]


def one_cell(properties: dict, name: str) -> int | None:
    values = cells(properties.get(name, ""))
    return values[0] if values and len(values) == 1 else None


def parse_cpu_list(text: str | None) -> set[int] | None:
    """CPUs in a kernel list such as ``0-3`` or ``0,2-3``; None if it cannot be read."""
    if not text or not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", text.strip()):
        return None
    cpus = set()
    for part in text.strip().split(","):
        first, _, last = part.partition("-")
        cpus.update(range(int(first), int(last or first) + 1))
    return cpus


def evaluate_thermal(thermal: dict | None, kernel_log: list[str] | None) -> dict:
    """The gate's verdict from a ``mirror-thermal/1`` section and, if read, the kernel's log lines.

    ``kernel_log`` None means the log was not read (over Wi-Fi): the verdict is
    then ``incomplete`` at best. An empty list means it was read and said
    nothing about the driver, which is a Stop.
    """
    stops: list[str] = []
    notes: list[str] = []
    noted: dict[str, int] = {}
    if not thermal or thermal.get("schema") != "mirror-thermal/1":
        return {"verdict": STOP, "stops": ["The unit gave no thermal report (Mirror Home 2.3.0-dev.30 or later reports it)"],
                "notes": [], "values": {}}

    for entry in thermal.get("unreadable", []):
        path = entry.get("path", "")
        if path.startswith(GATE_PATHS):
            stops.append(f"{path} could not be read: {entry.get('reason')}")

    enabled = (thermal.get("msmThermalParameters") or {}).get("enabled")
    if enabled != "Y":
        stops.append(f"msm_thermal's 'enabled' is {enabled!r}, not 'Y'")

    nodes = {name: node for name, node in (thermal.get("deviceTreeNodes") or {}).items()
             if name.rsplit("/", 1)[-1] == THERMAL_NODE}
    if len(nodes) != 1:
        stops.append(f"Expected one {THERMAL_NODE} device-tree node, found {sorted(nodes) or 'none'}")
    else:
        properties = next(iter(nodes.values())).get("properties", {})
        for name in NOTED_PROPERTIES:
            if name in properties:
                value = one_cell(properties, name)
                if value is None:
                    stops.append(f"{name} is not one 32-bit cell: {properties[name]!r}")
                else:
                    noted[name] = value
        reset = noted.get("qcom,therm-reset-temp", 0)
        limit = noted.get("qcom,limit-temp", 0)
        step = noted.get("qcom,freq-step", 0)
        mask = noted.get("qcom,freq-control-mask", 0)
        cpus = parse_cpu_list(thermal.get("cpusPossible"))
        if cpus is None:
            stops.append(f"Which CPUs exist could not be read: {thermal.get('cpusPossible')!r}")
            cpus = set()
        uncovered = sorted(cpu for cpu in cpus if not mask >> cpu & 1)
        throttles = limit > 0 and step > 0 and mask != 0 and cpus and not uncovered
        if reset > 0:
            notes.append(f"The kernel resets the chip at {reset} °C without a daemon (qcom,therm-reset-temp)")
        if throttles:
            notes.append(f"The kernel throttles CPUs {sorted(cpus)} from {limit} °C in steps of {step} (mask {mask:#x})")
        elif limit > 0 or mask:
            notes.append(f"Throttling is incomplete: limit {limit}, step {step}, mask {mask:#x}, "
                         f"CPUs not covered {uncovered}")
        if not reset > 0 and not throttles:
            stops.append("The device tree sets neither a chip reset (qcom,therm-reset-temp) nor throttling of "
                         "every CPU (qcom,limit-temp, a positive qcom,freq-step and a qcom,freq-control-mask "
                         "covering each CPU)")

    for zone in thermal.get("zones", []):
        critical = [trip["temp"] for trip in zone.get("trips", []) if trip.get("type") == "critical"]
        notes.append(f"{zone.get('name')} ({zone.get('type')}): {zone.get('temp')}"
                     + (f", critical trip at {', '.join(map(str, critical))}" if critical else ""))

    if kernel_log is not None:
        driver_lines = [line for line in kernel_log if KERNEL_LOG.search(line)]
        failures = [line for line in driver_lines if KERNEL_LOG_FAILURE.search(line)]
        if not driver_lines:
            stops.append("The kernel log has no msm_thermal or tsens line (it may have been overwritten "
                         "since boot: read it soon after a restart)")
        for line in failures:
            stops.append(f"Kernel log: {line.strip()}")
        notes += [f"Kernel log: {line.strip()}" for line in driver_lines if line not in failures]

    if stops:
        verdict = STOP
    elif kernel_log is None:
        verdict = INCOMPLETE
        notes.append("The kernel log was not read: run this stage over USB (--serial) before any write")
    else:
        verdict = PASS
    return {"verdict": verdict, "stops": stops, "notes": notes, "values": noted}


# ---------------------------------------------------------------------------
# Reading the thermal section over ADB, in the shape Mirror Home reports it.
# ---------------------------------------------------------------------------


def read_thermal_over_adb(tools: identify.Tools, serial: str) -> tuple[dict, list[str]]:
    def shell(command: str) -> tuple[int, str]:
        code, out, err = tools.adb("-s", serial, "shell", command)
        return code, out if code == 0 else err

    unreadable = []

    def read(path: str) -> str | None:
        code, out = shell(f"cat '{path}'")
        if code != 0 or "No such file" in out or "Permission denied" in out:
            unreadable.append({"path": path, "reason": out.strip() or f"exit {code}"})
            return None
        return out.strip()

    def read_hex(path: str) -> str | None:
        code, out = shell(f"od -An -tx1 '{path}'")
        # od folds repeated lines into "*", which would lose bytes.
        if code != 0 or "No such file" in out or "Permission denied" in out or "*" in out:
            unreadable.append({"path": path, "reason": out.strip() or f"exit {code}"})
            return None
        return "".join(out.split())

    def listing(path: str) -> list[str] | None:
        code, out = shell(f"ls '{path}'")
        if code != 0 or "No such file" in out or "Permission denied" in out:
            unreadable.append({"path": path, "reason": out.strip() or f"exit {code}"})
            return None
        return sorted(out.split())

    parameters_folder = "/sys/module/msm_thermal/parameters"
    names = listing(parameters_folder)
    parameters = None if names is None else {
        name: value for name in names if (value := read(f"{parameters_folder}/{name}")) is not None
    }
    nodes = {}
    for parent in ("/proc/device-tree", "/proc/device-tree/soc"):
        for name in listing(parent) or []:
            if name.startswith(THERMAL_NODE):
                node = f"{parent}/{name}"
                code, out = shell(f"for f in '{node}'/*; do [ -f \"$f\" ] && echo \"${{f##*/}}\"; done; true")
                properties = {}
                for entry in out.split():
                    if (value := read_hex(f"{node}/{entry}")) is not None:
                        properties[entry] = value
                key = name if parent == "/proc/device-tree" else f"soc/{name}"
                nodes[key] = {"properties": properties, "children": {}}
    zones = []
    for name in sorted((listing("/sys/class/thermal") or []), key=lambda n: identify_zone(n)):
        if identify_zone(name) < 0:
            continue
        zone = {"name": name}
        for field in ("type", "temp", "mode"):
            zone[field] = read(f"/sys/class/thermal/{name}/{field}")
        trips = []
        for index in range(32):
            temp = read(f"/sys/class/thermal/{name}/trip_point_{index}_temp")
            if temp is None:
                unreadable.pop()
                break
            trips.append({"index": index, "type": read(f"/sys/class/thermal/{name}/trip_point_{index}_type"),
                          "temp": temp})
        zone["trips"] = trips
        zones.append(zone)
    thermal = {
        "schema": "mirror-thermal/1",
        "cpusPossible": read("/sys/devices/system/cpu/possible"),
        "msmThermalParameters": parameters,
        "deviceTreeNodes": nodes,
        "zones": zones,
        "unreadable": unreadable,
    }
    code, log = shell("dmesg")
    if code != 0:
        raise RuntimeError(f"The kernel log could not be read over ADB: {log.strip()}")
    return thermal, log.splitlines()


def identify_zone(name: str) -> int:
    match = re.fullmatch(r"thermal_zone(\d{1,6})", name)
    return int(match.group(1)) if match else -1


def print_verdict(result: dict) -> None:
    print(f"Thermal protection: {result['verdict'].upper()}")
    for stop in result["stops"]:
        print(f"  Stop: {stop}")
    for name, value in result["values"].items():
        print(f"  {name} = {value}")
    for note in result["notes"]:
        print(f"  {note}")


def command_thermal(arguments: argparse.Namespace) -> int:
    try:
        if arguments.mirror:
            fetch = identify.http_fetcher(identify.load_mirror_config(pathlib.Path(arguments.mirror)))
            thermal = json.loads(fetch("/api/v1/device/inventory")).get("thermal")
            kernel_log = None
        else:
            tools = identify.Tools(identify.find_tool("adb"), identify.find_tool("fastboot"))
            state, serial = identify.find_unit(tools, arguments.serial)
            if state != "android" or not serial:
                raise RuntimeError(f"Over USB this stage needs Android with ADB authorized; the unit is {state!r}")
            thermal, kernel_log = read_thermal_over_adb(tools, serial)
    except (RuntimeError, ValueError, KeyError) as error:
        print(f"Could not read the unit: {error}", file=sys.stderr)
        return 1
    result = evaluate_thermal(thermal, kernel_log)
    if arguments.json:
        print(json.dumps({"schema": "mirror-install-thermal/1", **result, "thermal": thermal}, indent=1))
    else:
        print_verdict(result)
    return EXIT_CODES[result["verdict"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    stages = parser.add_subparsers(dest="stage", required=True)
    thermal = stages.add_parser("thermal", help="reads only: whether the kernel guards the chip against heat")
    where = thermal.add_mutually_exclusive_group()
    where.add_argument("--serial", help="over USB: the unit to read, when more than one is connected")
    where.add_argument("--mirror", metavar="CONFIG",
                       help="over Wi-Fi through Mirror Home: a JSON file with host, port and a paired token")
    thermal.add_argument("--json", action="store_true", help="print the verdict and what was read as JSON")
    thermal.set_defaults(run=command_thermal)
    arguments = parser.parse_args(argv)
    return arguments.run(arguments)


if __name__ == "__main__":
    sys.exit(main())
