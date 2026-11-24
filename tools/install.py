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
import shlex
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
# Only these are read from the driver's node (as Mirror Home does), so nothing else a board's
# device tree holds, such as addresses, ends up in a report.
KNOWN_PROPERTIES = ("compatible", "status") + NOTED_PROPERTIES
# Names the unit lists that are read further; anything else is never put in a command.
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9,._#@+-]{0,127}")
SAFE_NODE_PATH = re.compile(r"(?:soc/)?(?=qcom,msm-thermal)[A-Za-z0-9][A-Za-z0-9,._#@+-]{0,127}")
# Degrees Celsius a setting must lie in to be credited: a value outside could never
# act (60000) or would act all the time; either way it is no evidence of protection.
RESET_RANGE = (80, 130)
LIMIT_RANGE = (50, 110)
FREQ_STEP_RANGE = (1, 20)
POLL_MS_RANGE = (10, 1000)
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
    if not isinstance(text, str) or not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", text.strip()):
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


def redact(line: str) -> str:
    """Text with anything shaped like a hardware address or a long identifier hidden."""
    line = re.sub(r"\b[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}\b", "<hidden>", line)
    return re.sub(r"\b(?:0[xX])?[0-9a-fA-F]{12,}\b", "<hidden>", line)


def safe(value) -> str:
    """Text from the unit as it may be shown: redacted, short, and withheld if it names an identifier."""
    if value is None:
        return "none"
    text = " ".join(str(value).split())
    if IDENTIFYING.search(text):
        return "<left out: it may identify the unit>"
    return redact(text)[:200]


def label(name) -> str:
    return name if isinstance(name, str) and SAFE_NODE_PATH.fullmatch(name) else "<a node with an unusual name>"


def exported(thermal: dict | None) -> dict | None:
    """The thermal section as it may leave this computer: only the fields the gate reads, text redacted."""
    if not isinstance(thermal, dict):
        return None

    def text(value):
        return safe(value) if isinstance(value, str) else None

    nodes = {}
    extra = []
    for name, node in (thermal.get("deviceTreeNodes") or {}).items():
        if isinstance(node, dict) and not (isinstance(name, str) and SAFE_NODE_PATH.fullmatch(name)):
            extra.append({"path": "<a node with an unusual name>", "reason": "left out of the report"})
        if isinstance(node, dict) and isinstance(name, str) and SAFE_NODE_PATH.fullmatch(name):
            properties = node.get("properties") or {}
            nodes[name] = {"properties": {key: properties[key] for key in KNOWN_PROPERTIES
                                          if isinstance(properties.get(key), str)
                                          and re.fullmatch(r"(?:[0-9a-f]{2}){0,1024}", properties[key])},
                           "listed": node.get("listed") is True}
    parameters = thermal.get("msmThermalParameters")
    return {
        "schema": thermal.get("schema") if thermal.get("schema") == "mirror-thermal/1" else None,
        "cpusPossible": text(thermal.get("cpusPossible")),
        "deviceTreeListed": thermal.get("deviceTreeListed") is True,
        "msmThermalParameters": None if not isinstance(parameters, dict)
        else {"enabled": text(parameters["enabled"])} if "enabled" in parameters else {},
        "deviceTreeNodes": nodes,
        "zones": [{"name": text(zone.get("name")), "type": text(zone.get("type")), "temp": text(zone.get("temp")),
                   "mode": text(zone.get("mode")),
                   "trips": [{"index": trip.get("index") if isinstance(trip.get("index"), int) else None,
                              "type": text(trip.get("type")), "temp": text(trip.get("temp"))}
                             for trip in zone.get("trips") or [] if isinstance(trip, dict)]}
                  for zone in thermal.get("zones") or [] if isinstance(zone, dict)],
        "unreadable": [{"path": text(entry.get("path")), "reason": text(entry.get("reason"))}
                       for entry in thermal.get("unreadable") or [] if isinstance(entry, dict)] + extra,
    }


def driver_nodes(nodes: dict, stops: list[str]) -> dict:
    """The nodes the msm_thermal driver binds to: compatible with it and not disabled."""
    bound = {}
    for name, node in nodes.items():
        properties = node.get("properties", {})
        if "compatible" not in properties:
            stops.append(f"{label(name)} has no readable compatible property, so it cannot be told whose it is")
            continue
        compatible = strings(properties["compatible"])
        if compatible is None:
            stops.append(f"{label(name)}'s compatible is not a string list")
            continue
        status = strings(properties["status"]) if "status" in properties else ["okay"]
        if status is None or len(status) != 1:
            stops.append(f"{label(name)}'s status is not one string")
            continue
        # The kernel compares compatible strings without regard to case.
        if THERMAL_NODE in [entry.lower() for entry in compatible] and status[0] in ("okay", "ok"):
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
    review_node = None
    if not isinstance(thermal, dict):
        thermal = None
    if not thermal or thermal.get("schema") != "mirror-thermal/1":
        return {"verdict": STOP, "stops": ["The unit gave no thermal report (Mirror Home 2.3.0-dev.30 or later "
                                           "reports it)"], "unresolved": [], "notes": [], "values": {},
                "kernelLines": []}

    listed = thermal.get("deviceTreeListed") is True
    if not listed:
        unresolved.append(f"The device tree could not be listed, so another {THERMAL_NODE} node cannot be "
                          "ruled out")
    for entry in thermal.get("unreadable") or []:
        if not isinstance(entry, dict):
            stops.append("The report's list of unreadable paths is not valid")
            continue
        path = entry.get("path") if isinstance(entry.get("path"), str) else ""
        reason = entry.get("reason") if isinstance(entry.get("reason"), str) else ""
        if path in DEVICE_TREES and not listed:
            continue
        if "known properties read by name" in reason:
            continue
        if not path or path.startswith(GATE_PATHS):
            stops.append(f"{safe(path) if path else 'A path'} could not be read: {safe(reason)}")

    enabled = (thermal.get("msmThermalParameters") or {}).get("enabled")
    if enabled != "Y":
        stops.append(f"msm_thermal's 'enabled' is {safe(enabled)}, not Y")

    nodes = {name: node for name, node in (thermal.get("deviceTreeNodes") or {}).items()
             if isinstance(name, str) and isinstance(node, dict)
             and name.rsplit("/", 1)[-1].startswith(THERMAL_NODE)}
    bound = driver_nodes(nodes, stops)
    if len(bound) != 1:
        stops.append(f"Expected one enabled device-tree node compatible with {THERMAL_NODE}, found "
                     f"{sorted(map(label, bound)) or 'none'} among {sorted(map(label, nodes)) or 'no nodes'}")
    else:
        credited, node = next(iter(bound.items()))
        properties = node.get("properties", {})
        if node.get("listed") is not True:
            unresolved.append(f"{label(credited)} could not be listed: a property it holds may have been missed "
                              "(status, for one)")
        for name in NOTED_PROPERTIES:
            if name in properties:
                values = cells(properties[name])
                if values is None or len(values) != 1:
                    stops.append(f"{name} is not one 32-bit cell")
                else:
                    noted[name] = values[0]
        reset = noted.get("qcom,therm-reset-temp")
        limit = noted.get("qcom,limit-temp")
        step = noted.get("qcom,freq-step")
        mask = noted.get("qcom,freq-control-mask", 0)
        poll = noted.get("qcom,poll-ms")
        cpus = parse_cpu_list(thermal.get("cpusPossible"))
        if cpus is None:
            stops.append("Which CPUs exist could not be read, or the list is not valid")
            cpus = set()
        uncovered = sorted(cpu for cpu in cpus if not mask >> cpu & 1)
        # The driver checks the temperature for both the reset and the throttling every poll-ms.
        polls = in_range(poll, POLL_MS_RANGE)
        if not polls:
            stops.append(f"qcom,poll-ms is {poll!r}: the driver would not check the temperature often enough "
                         f"(credited {POLL_MS_RANGE[0]}-{POLL_MS_RANGE[1]} ms)")
        resets = polls and in_range(reset, RESET_RANGE)
        throttles = (polls and in_range(limit, LIMIT_RANGE) and in_range(step, FREQ_STEP_RANGE)
                     and bool(cpus) and not uncovered)
        review_node = credited
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
        critical = [safe(trip.get("temp")) for trip in zone.get("trips", []) if trip.get("type") == "critical"]
        notes.append(f"{safe(zone.get('name'))} ({safe(zone.get('type'))}): {safe(zone.get('temp'))}"
                     + (f", critical trip at {', '.join(critical)}" if critical else ""))

    if kernel_log is not None:
        for line in kernel_log:
            if not KERNEL_LOG.search(line):
                continue
            if IDENTIFYING.search(line):
                stops.append("A thermal kernel line was left out because it may identify the unit; read the "
                             "log by hand")
                continue
            review.append(redact(line.strip()))
            if KERNEL_LOG_FAILURE.search(line):
                stops.append(f"Kernel log: {redact(line.strip())}")
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
        notes.append(f"Every automatic check passed on {review_node}. Go on only if the kernel lines listed show "
                     "the msm_thermal driver bound to that node with its limits registered, and nothing failed")
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
    Every path is quoted, and names the unit lists are used only if they are
    plain. Of the driver's node only the gate's known properties are read.
    """
    def shell(command: str) -> tuple[bool, str]:
        code, out, err = tools.adb("-s", serial, "shell", f"{command} 2>&1; echo {STATUS_MARKER}$?")
        text = (out + err).replace(serial, "<unit>")
        body, marker, status = text.rpartition(STATUS_MARKER)
        ok = code == 0 and bool(marker) and status.strip() == "0"
        return ok, body if marker else text

    unreadable = []

    def failed(path: str, out: str) -> None:
        reason = safe(out) if out.strip() else "no answer"
        unreadable.append({"path": path, "reason": reason})

    def read(path: str) -> str | None:
        ok, out = shell(f"cat {shlex.quote(path)}")
        if not ok:
            failed(path, out)
            return None
        return out.strip()

    def exists(path: str) -> bool:
        return shell(f"[ -e {shlex.quote(path)} ]")[0]

    def read_hex(path: str) -> str | None:
        ok, out = shell(f"od -An -tx1 {shlex.quote(path)}")
        tokens = out.split()
        # od folds repeated lines into "*", which would lose bytes; anything but byte pairs is refused.
        if not ok or not all(re.fullmatch(r"[0-9a-f]{2}", token) for token in tokens):
            failed(path, out if not ok else "od printed something other than bytes")
            return None
        return "".join(tokens)

    def listing(path: str) -> list[str] | None:
        """Every name in a folder; a caller uses only the plain ones it looks for."""
        # Android 6's ls shows a link to a folder (each thermal zone, the device tree) as the link itself
        # unless the path ends in /.
        ok, out = shell(f"ls {shlex.quote(path.rstrip('/') + '/')}")
        if not ok:
            failed(path, out)
            return None
        return sorted(out.split())

    def plain(folder: str, name: str) -> bool:
        if SAFE_NAME.fullmatch(name):
            return True
        failed(folder, "a name it lists is outside the expected characters and was not read")
        return False

    parameters_folder = "/sys/module/msm_thermal/parameters"
    if exists(parameters_folder):
        enabled = read(f"{parameters_folder}/enabled")
        parameters = {} if enabled is None else {"enabled": enabled}
    else:
        parameters = None
        failed(parameters_folder, "not found")
    nodes = {}
    tree = DEVICE_TREES[0]
    top = listing(tree)
    if top is not None:
        parents = [("", tree)] + ([("soc/", f"{tree}/soc")] if "soc" in top else [])
        for prefix, parent in parents:
            entries = top if not prefix else listing(parent)
            for name in entries or []:
                if not name.startswith(THERMAL_NODE) or not plain(parent, name):
                    continue
                node = f"{parent}/{name}"
                present = listing(node)
                properties = {}
                for entry in KNOWN_PROPERTIES:
                    there = entry in present if present is not None else exists(f"{node}/{entry}")
                    if there and (value := read_hex(f"{node}/{entry}")) is not None:
                        properties[entry] = value
                nodes[prefix + name] = {"properties": properties, "listed": present is not None}
    zones = []
    for name in sorted(listing("/sys/class/thermal") or [], key=zone_number):
        if zone_number(name) < 0:
            continue
        folder = f"/sys/class/thermal/{name}"
        zone = {"name": name}
        for field in ("type", "temp", "mode"):
            zone[field] = read(f"{folder}/{field}")
        trips = []
        present = listing(folder) or []
        for index in range(32):
            # The zone's own listing says which trip points exist; one that cannot be read is recorded.
            if f"trip_point_{index}_temp" not in present:
                break
            trips.append({"index": index, "type": read(f"{folder}/trip_point_{index}_type"),
                          "temp": read(f"{folder}/trip_point_{index}_temp")})
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
        raise RuntimeError(f"The kernel log could not be read over ADB: {safe(log)}")
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
        print(json.dumps({"schema": "mirror-install-thermal/1", **result, "thermal": exported(thermal)}, indent=1))
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
