#!/usr/bin/env python3
"""The Silvering installer: the steps of silvering/FIRST-FLASH.md as commands.

Stages arrive one at a time; each says whether it reads or changes the unit.

    thermal   reads only: whether the kernel guards the chip against heat by
              itself, since Silvering has no thermal-engine (FIRST-FLASH,
              "Thermal protection"). Over Wi-Fi through Mirror Home
              (``--mirror CONFIG``) it checks the driver and its device tree;
              the kernel log can only be read over USB (``--serial``).

The thermal stage never answers a plain pass yet: which kernel messages prove
that the limits were registered is not known for this board, so its best
answer over USB is ``review`` (every automatic check passed; a person reads
the driver's kernel lines it lists), and over Wi-Fi ``incomplete``.

Exit codes: 1 the unit could not be read, 3 Stop (the gate fails),
4 incomplete (what was read passes; the rest must be read over USB),
5 review (all automatic checks pass; a person judges the kernel lines shown).
0 is kept for a pass once the kernel's own messages are known.
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
DEVICE_TREES = ("/proc/device-tree", "/sys/firmware/devicetree/base")
KERNEL_LOG = re.compile(r"msm[_-]thermal|tsens", re.IGNORECASE)
KERNEL_LOG_FAILURE = re.compile(
    r"fail|error|\berr\b|invalid|unable|could ?not|cannot|can't|not regist|disabl|interrupt[ _]mode",
    re.IGNORECASE)
IDENTIFYING = re.compile(r"serial|androidboot|\bmac\b|mac[_-]?addr|imei|meid|ssid|bdaddr|uuid|android_id",
                         re.IGNORECASE)
# Properties the runbook asks to note, as 32-bit cells.
NOTED_PROPERTIES = (
    "qcom,therm-reset-temp", "qcom,limit-temp", "qcom,temp-hysteresis", "qcom,freq-step",
    "qcom,freq-control-mask", "qcom,core-limit-temp", "qcom,core-temp-hysteresis",
    "qcom,core-control-mask", "qcom,hotplug-temp", "qcom,hotplug-temp-hysteresis",
    "qcom,sensor-id", "qcom,poll-ms",
)
# Degrees Celsius a setting must lie in to be credited: a value outside could never
# act (60000) or would act all the time; either way it is no evidence of protection.
RESET_RANGE = (80, 130)
LIMIT_RANGE = (50, 110)
FREQ_STEP_RANGE = (1, 20)
MAX_CPUS = 32

# What the gate rests on: anything unreadable here is a Stop; an unreadable zone is only noted.
GATE_PATHS = DEVICE_TREES + ("/sys/module/msm_thermal", "/sys/devices/system/cpu/possible")

STOP, INCOMPLETE, REVIEW = "stop", "incomplete", "review"
EXIT_CODES = {STOP: 3, INCOMPLETE: 4, REVIEW: 5}


def cells(hex_value: str) -> list[int] | None:
    """Big-endian 32-bit cells of a device-tree property, or None if it is not a whole number of them."""
    if not re.fullmatch(r"(?:[0-9a-f]{8})+", hex_value or ""):
        return None
    return [int(hex_value[i:i + 8], 16) for i in range(0, len(hex_value), 8)]


def strings(hex_value: str) -> list[str] | None:
    """A device-tree string list (NUL-separated), or None if it is not one."""
    if not re.fullmatch(r"(?:[0-9a-f]{2})+", hex_value or ""):
        return None
    raw = bytes.fromhex(hex_value)
    if not raw.endswith(b"\0"):
        return None
    try:
        return raw[:-1].decode("ascii").split("\0")
    except UnicodeDecodeError:
        return None


def parse_cpu_list(text: str | None) -> set[int] | None:
    """CPUs in a kernel list such as ``0-3`` or ``0,2-3``; None if any part of it is not valid."""
    if not text or not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", text.strip()):
        return None
    cpus = set()
    for part in text.strip().split(","):
        first, _, last = part.partition("-")
        first_cpu, last_cpu = int(first), int(last or first)
        if first_cpu > last_cpu or last_cpu >= MAX_CPUS:
            return None
        cpus.update(range(first_cpu, last_cpu + 1))
    return cpus


def in_range(value: int | None, limits: tuple[int, int]) -> bool:
    return value is not None and limits[0] <= value <= limits[1]


def driver_nodes(nodes: dict, stops: list[str]) -> dict:
    """The nodes the msm_thermal driver binds to: compatible with it and not disabled."""
    bound = {}
    for name, node in nodes.items():
        properties = node.get("properties", {})
        if "compatible" not in properties:
            stops.append(f"{name} has no readable compatible property, so it cannot be told whose it is")
            continue
        compatible = strings(properties["compatible"])
        if compatible is None:
            stops.append(f"{name}'s compatible is not a string list: {properties['compatible']!r}")
            continue
        status = strings(properties["status"]) if "status" in properties else ["okay"]
        if THERMAL_NODE in compatible and status in (["okay"], ["ok"]):
            bound[name] = node
    return bound


def evaluate_thermal(thermal: dict | None, kernel_log: list[str] | None) -> dict:
    """The gate's verdict from a ``mirror-thermal/1`` section and, if read, the kernel's log lines.

    ``kernel_log`` None means the log was not read (over Wi-Fi): the verdict is
    then ``incomplete`` at best. An empty list means it was read and said
    nothing about the driver, which is a Stop.
    """
    stops: list[str] = []
    unresolved: list[str] = []
    notes: list[str] = []
    noted: dict[str, int] = {}
    review: list[str] = []
    if not thermal or thermal.get("schema") != "mirror-thermal/1":
        return {"verdict": STOP, "stops": ["The unit gave no thermal report (Mirror Home 2.3.0-dev.30 or later "
                                           "reports it)"], "unresolved": [], "notes": [], "values": {},
                "kernelLines": []}

    listed = thermal.get("deviceTreeListed") is True
    for entry in thermal.get("unreadable", []):
        path = entry.get("path", "")
        if not listed and (path in DEVICE_TREES or "known properties read by name" in entry.get("reason", "")):
            unresolved.append(f"{path} could not be listed, so another {THERMAL_NODE} node cannot be ruled out "
                              "and only properties known by name were read")
        elif path.startswith(GATE_PATHS):
            stops.append(f"{path} could not be read: {entry.get('reason')}")

    enabled = (thermal.get("msmThermalParameters") or {}).get("enabled")
    if enabled != "Y":
        stops.append(f"msm_thermal's 'enabled' is {enabled!r}, not 'Y'")

    nodes = {name: node for name, node in (thermal.get("deviceTreeNodes") or {}).items()
             if name.rsplit("/", 1)[-1].startswith(THERMAL_NODE)}
    bound = driver_nodes(nodes, stops)
    if len(bound) != 1:
        stops.append(f"Expected one enabled device-tree node compatible with {THERMAL_NODE}, found "
                     f"{sorted(bound) or 'none'} among {sorted(nodes) or 'no nodes'}")
    else:
        properties = next(iter(bound.values())).get("properties", {})
        for name in NOTED_PROPERTIES:
            if name in properties:
                values = cells(properties[name])
                if values is None or len(values) != 1:
                    stops.append(f"{name} is not one 32-bit cell: {properties[name]!r}")
                else:
                    noted[name] = values[0]
        reset = noted.get("qcom,therm-reset-temp")
        limit = noted.get("qcom,limit-temp")
        step = noted.get("qcom,freq-step")
        mask = noted.get("qcom,freq-control-mask", 0)
        cpus = parse_cpu_list(thermal.get("cpusPossible"))
        if cpus is None:
            stops.append(f"Which CPUs exist could not be read: {thermal.get('cpusPossible')!r}")
            cpus = set()
        uncovered = sorted(cpu for cpu in cpus if not mask >> cpu & 1)
        resets = in_range(reset, RESET_RANGE)
        throttles = (in_range(limit, LIMIT_RANGE) and in_range(step, FREQ_STEP_RANGE)
                     and bool(cpus) and not uncovered)
        if reset is not None:
            notes.append(f"qcom,therm-reset-temp {reset} °C: " + (
                "the kernel resets the chip without a daemon" if resets
                else f"not credited, outside {RESET_RANGE[0]}-{RESET_RANGE[1]} °C"))
        if limit is not None or mask:
            notes.append(f"Throttling from qcom,limit-temp {limit} °C in steps of {step}, mask {mask:#x} for CPUs "
                         f"{sorted(cpus)}: " + ("credited" if throttles else
                                                f"not credited (limit within {LIMIT_RANGE}, step within "
                                                f"{FREQ_STEP_RANGE}, CPUs not covered {uncovered})"))
        if not resets and not throttles:
            stops.append("The device tree sets neither a chip reset (qcom,therm-reset-temp within "
                         f"{RESET_RANGE[0]}-{RESET_RANGE[1]} °C) nor throttling of every CPU (qcom,limit-temp "
                         f"within {LIMIT_RANGE[0]}-{LIMIT_RANGE[1]} °C, qcom,freq-step within "
                         f"{FREQ_STEP_RANGE[0]}-{FREQ_STEP_RANGE[1]} and a qcom,freq-control-mask covering each CPU)")

    for zone in thermal.get("zones", []):
        critical = [str(trip.get("temp")) for trip in zone.get("trips", []) if trip.get("type") == "critical"]
        notes.append(f"{zone.get('name')} ({zone.get('type')}): {zone.get('temp')}"
                     + (f", critical trip at {', '.join(critical)}" if critical else ""))

    if kernel_log is not None:
        for line in kernel_log:
            if not KERNEL_LOG.search(line):
                continue
            if IDENTIFYING.search(line):
                stops.append("A thermal kernel line was left out because it may identify the unit; read the "
                             "log by hand")
                continue
            review.append(line.strip())
            if KERNEL_LOG_FAILURE.search(line):
                stops.append(f"Kernel log: {line.strip()}")
        if not any(re.search(r"msm[_-]thermal", line, re.IGNORECASE) for line in review):
            stops.append("The kernel log has no msm_thermal line (it may have been overwritten since boot: read "
                         "it soon after a restart)")

    if stops:
        verdict = STOP
    elif unresolved or kernel_log is None:
        verdict = INCOMPLETE
        if kernel_log is None:
            unresolved.append("The kernel log was not read: run this stage over USB (--serial) before any write")
    else:
        verdict = REVIEW
        notes.append("Every automatic check passed. Read the kernel lines listed: go on only if they show the "
                     "limits registered and nothing failed")
    return {"verdict": verdict, "stops": stops, "unresolved": unresolved, "notes": notes, "values": noted,
            "kernelLines": review}


# ---------------------------------------------------------------------------
# Reading the thermal section over ADB, in the shape Mirror Home reports it.
# ---------------------------------------------------------------------------

STATUS_MARKER = "__mirror_status="


def read_thermal_over_adb(tools: identify.Tools, serial: str) -> tuple[dict, list[str]]:
    """The same section Mirror Home reports, and the kernel log's lines.

    adb on Android 6 does not pass on a command's exit status, so each command
    prints its own after its output; a command without one counts as failed.
    Node properties are read one level deep: the gate needs no child node.
    """
    def shell(command: str) -> tuple[bool, str]:
        code, out, err = tools.adb("-s", serial, "shell", f"{command} 2>&1; echo {STATUS_MARKER}$?")
        text = (out + err).replace(serial, "<unit>")
        body, marker, status = text.rpartition(STATUS_MARKER)
        ok = code == 0 and bool(marker) and status.strip() == "0"
        return ok, body if marker else text

    unreadable = []

    def failed(path: str, out: str) -> None:
        reason = " ".join(out.split())[:200] or "no answer"
        unreadable.append({"path": path, "reason": reason})

    def read(path: str, record: bool = True) -> str | None:
        ok, out = shell(f"cat '{path}'")
        if not ok:
            if record:
                failed(path, out)
            return None
        return out.strip()

    def read_hex(path: str) -> str | None:
        ok, out = shell(f"od -An -tx1 '{path}'")
        tokens = out.split()
        # od folds repeated lines into "*", which would lose bytes; anything but byte pairs is refused.
        if not ok or not all(re.fullmatch(r"[0-9a-f]{2}", token) for token in tokens):
            failed(path, out if not ok else "od printed something other than bytes")
            return None
        return "".join(tokens)

    def listing(path: str) -> list[str] | None:
        ok, out = shell(f"ls '{path}'")
        if not ok:
            failed(path, out)
            return None
        return sorted(out.split())

    parameters_folder = "/sys/module/msm_thermal/parameters"
    names = listing(parameters_folder)
    parameters = None if names is None else {
        name: value for name in names
        if not IDENTIFYING.search(name) and (value := read(f"{parameters_folder}/{name}")) is not None
    }
    nodes = {}
    tree = DEVICE_TREES[0]
    top = listing(tree)
    if top is not None:
        parents = [("", tree)] + ([("soc/", f"{tree}/soc")] if "soc" in top else [])
        for prefix, parent in parents:
            entries = top if not prefix else listing(parent)
            for name in entries or []:
                if not name.startswith(THERMAL_NODE):
                    continue
                node = f"{parent}/{name}"
                ok, out = shell(f"for f in '{node}'/*; do if [ -f \"$f\" ]; then echo \"${{f##*/}}\"; fi; done")
                if not ok:
                    failed(node, out)
                    continue
                properties = {}
                for entry in out.split():
                    if IDENTIFYING.search(entry):
                        continue
                    if (value := read_hex(f"{node}/{entry}")) is not None:
                        properties[entry] = value
                nodes[prefix + name] = {"properties": properties, "children": {}, "listed": True}
    zones = []
    for name in sorted(listing("/sys/class/thermal") or [], key=zone_number):
        if zone_number(name) < 0:
            continue
        zone = {"name": name}
        for field in ("type", "temp", "mode"):
            zone[field] = read(f"/sys/class/thermal/{name}/{field}")
        trips = []
        for index in range(32):
            temp = read(f"/sys/class/thermal/{name}/trip_point_{index}_temp", record=False)
            if temp is None:
                break
            trips.append({"index": index, "type": read(f"/sys/class/thermal/{name}/trip_point_{index}_type"),
                          "temp": temp})
        zone["trips"] = trips
        zones.append(zone)
    thermal = {
        "schema": "mirror-thermal/1",
        "cpusPossible": read("/sys/devices/system/cpu/possible"),
        "deviceTreeListed": top is not None,
        "msmThermalParameters": parameters,
        "deviceTreeNodes": nodes,
        "zones": zones,
        "unreadable": unreadable,
    }
    ok, log = shell("dmesg")
    if not ok:
        raise RuntimeError(f"The kernel log could not be read over ADB: {' '.join(log.split())[:200]}")
    return thermal, log.splitlines()


def zone_number(name: str) -> int:
    match = re.fullmatch(r"thermal_zone(\d{1,6})", name)
    return int(match.group(1)) if match else -1


def print_verdict(result: dict) -> None:
    print(f"Thermal protection: {result['verdict'].upper()}")
    for stop in result["stops"]:
        print(f"  Stop: {stop}")
    for item in result["unresolved"]:
        print(f"  Not settled: {item}")
    for name, value in result["values"].items():
        print(f"  {name} = {value}")
    for note in result["notes"]:
        print(f"  {note}")
    for line in result["kernelLines"]:
        print(f"  | {line}")


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
