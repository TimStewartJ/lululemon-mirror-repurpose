#!/usr/bin/env python3
"""The Silvering installer: the steps of silvering/FIRST-FLASH.md as commands.

Stages arrive one at a time; each says whether it reads or changes the unit.

    thermal   reads only: whether the kernel guards the chip against heat by
              itself, since Silvering has no thermal-engine (FIRST-FLASH,
              "Thermal protection"). Over Wi-Fi through Mirror Home
              (``--mirror CONFIG``) it checks the driver and its device tree;
              the kernel log can only be read over USB (``--serial``).
    plan      reads only: the fastboot client's version, a folder holding
              only system.img, userdata.img and their sha256 files (each image
              hashed, walked chunk by chunk and checked to be ext4), and, with
              the unit in fastboot, the partitions' sizes. Prints the only
              writes the installer ever makes: system, then userdata. Never
              restarts the unit and never sends it anything but getvar.

The thermal stage never answers a plain pass yet: which kernel messages prove
that the limits were registered is not known for this board, so its best
answer over USB is ``review`` (every automatic check passed; a person reads
the driver's kernel lines it lists), and over Wi-Fi ``incomplete``.

Exit codes: 1 the unit could not be read, 3 Stop (the gate fails),
4 incomplete (what was read passes; the rest must be read over USB, or for
the plan, the unit is not in fastboot or userdata.img is not built yet),
5 review (all automatic checks pass; a person judges the kernel lines shown).
0 is the plan's ``ready``, and is kept for a thermal pass once the kernel's
own messages are known.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shlex
import struct
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
# What the CAF msm_thermal probe reads before anything else; any one missing and it gives up.
PROBE_INPUTS = ("qcom,sensor-id", "qcom,poll-ms", "qcom,limit-temp", "qcom,temp-hysteresis", "qcom,freq-step")
MAX_CPUS = 32

# What the gate rests on: anything unreadable here is a Stop; an unreadable zone is only noted.
GATE_PATHS = DEVICE_TREES + ("/sys/module/msm_thermal", "/sys/devices/system/cpu/possible")

STOP, INCOMPLETE, REVIEW = "stop", "incomplete", "review"
EXIT_CODES = {STOP: 3, INCOMPLETE: 4, REVIEW: 5}


def cells(hex_value: str) -> list[int] | None:
    """Big-endian 32-bit cells of a device-tree property, or None if it is not a whole number of them."""
    if not isinstance(hex_value, str) or not re.fullmatch(r"(?:[0-9a-f]{8})+", hex_value):
        return None
    return [int(hex_value[i:i + 8], 16) for i in range(0, len(hex_value), 8)]


def strings(hex_value: str) -> list[str] | None:
    """A device-tree string list (NUL-separated), or None if it is not one."""
    if not isinstance(hex_value, str) or not re.fullmatch(r"(?:[0-9a-f]{2})+", hex_value):
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
    """Text with anything shaped like a hardware address or a long identifier hidden.

    Boundaries are any character that is not a hex digit (``_`` included), not word boundaries.
    """
    line = re.sub(r"(?<![0-9a-fA-F])[0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5}(?![0-9a-fA-F])", "<hidden>", line)
    line = re.sub(r"(?<![0-9a-fA-F])[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}(?![0-9a-fA-F])", "<hidden>",
                  line)
    return re.sub(r"(?<![0-9a-fA-F])(?:0[xX])?[0-9a-fA-F]{12,}(?![0-9a-fA-F])", "<hidden>", line)


def safe(value) -> str:
    """Text from the unit as it may be shown: redacted, short, and withheld if it names an identifier."""
    if value is None:
        return "none"
    text = " ".join(str(value).split())
    if IDENTIFYING.search(text):
        return "<left out: it may identify the unit>"
    return redact(text)[:200]


def plain_text(text: str) -> bool:
    """Text that names nothing: no identifier word, and nothing the address redactor would hide."""
    return not IDENTIFYING.search(text) and redact(text) == text


def showable_name(name) -> bool:
    return isinstance(name, str) and bool(SAFE_NODE_PATH.fullmatch(name)) and plain_text(name)


def label(name) -> str:
    return name if showable_name(name) else "<a node whose name is not shown>"


def showable_property(key: str, value) -> bool:
    """A property as it may be exported: one 32-bit cell, or a short string list that names nothing."""
    if not isinstance(value, str):
        return False
    if key in ("compatible", "status"):
        decoded = strings(value)
        return (decoded is not None and len(value) <= 2 * 256
                and all(re.fullmatch(r"[A-Za-z0-9,._-]{1,64}", entry) and plain_text(entry)
                        for entry in decoded))
    return bool(re.fullmatch(r"[0-9a-f]{8}", value))


def as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def as_list(value) -> list:
    return value if isinstance(value, list) else []


def exported(thermal: dict | None) -> dict | None:
    """The thermal section as it may leave this computer: only the fields the gate reads, text redacted."""
    if not isinstance(thermal, dict):
        return None

    def text(value):
        return safe(value) if isinstance(value, str) else None

    nodes = {}
    extra = []
    for name, node in as_dict(thermal.get("deviceTreeNodes")).items():
        if not showable_name(name) or not isinstance(node, dict):
            extra.append({"path": "<a node whose name is not shown>", "reason": "left out of the report"})
            continue
        properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        kept = {key: properties[key] for key in KNOWN_PROPERTIES
                if key in properties and showable_property(key, properties[key])}
        extra += [{"path": f"{name}/{key}", "reason": "value not valid; left out of the report"}
                  for key in KNOWN_PROPERTIES if key in properties and key not in kept]
        nodes[name] = {"properties": kept, "listed": node.get("listed") is True}
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
                             for trip in as_list(zone.get("trips")) if isinstance(trip, dict)]}
                  for zone in as_list(thermal.get("zones")) if isinstance(zone, dict)],
        "unreadable": [{"path": text(entry.get("path")), "reason": text(entry.get("reason"))}
                       for entry in as_list(thermal.get("unreadable")) if isinstance(entry, dict)] + extra,
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
    candidates = {}
    records = thermal.get("deviceTreeNodes")
    if not isinstance(records, dict):
        stops.append("The report's device-tree nodes are not valid")
        records = {}
    for name, node in records.items():
        if not isinstance(name, str) or not name.rsplit("/", 1)[-1].startswith(THERMAL_NODE):
            continue
        if not isinstance(node, dict) or not isinstance(node.get("properties"), dict):
            stops.append(f"{label(name)} has no readable record, so it cannot be told whose it is")
            continue
        candidates[name] = node
    # A node that could not be listed is recorded as such; that one entry is what "listed" already says.
    unlisted_folders = {f"{root}/{name}" for name, node in candidates.items() if node.get("listed") is not True
                        for root in DEVICE_TREES}

    entries = thermal.get("unreadable")
    if not isinstance(entries, list):
        stops.append("The report's list of unreadable paths is not valid")
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            stops.append("The report's list of unreadable paths is not valid")
            continue
        path = entry.get("path") if isinstance(entry.get("path"), str) else ""
        reason = entry.get("reason") if isinstance(entry.get("reason"), str) else ""
        if path in DEVICE_TREES and not listed:
            continue
        if path in unlisted_folders:
            continue
        if not path or path.startswith(GATE_PATHS):
            stops.append(f"{safe(path) if path else 'A path'} could not be read: {safe(reason)}")

    enabled = as_dict(thermal.get("msmThermalParameters")).get("enabled")
    if enabled != "Y":
        stops.append(f"msm_thermal's 'enabled' is {safe(enabled)}, not Y")

    nodes = candidates
    bound = driver_nodes(nodes, stops)
    if len(bound) != 1:
        stops.append(f"Expected one enabled device-tree node compatible with {THERMAL_NODE}, found "
                     f"{sorted(map(label, bound)) or 'none'} among {sorted(map(label, nodes)) or 'no nodes'}")
    else:
        credited, node = next(iter(bound.items()))
        if not showable_name(credited):
            stops.append("The node the gate would rely on has a name that cannot be shown; check its binding "
                         "by hand at the unit")
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
        # Without these the CAF driver's probe stops before it sets up either the reset or the throttling.
        missing = [name for name in PROBE_INPUTS if name not in noted]
        if missing:
            stops.append(f"The driver cannot start without {', '.join(missing)} (one 32-bit cell each)")
            polls = False
        if "qcom,poll-ms" in noted and not in_range(poll, POLL_MS_RANGE):
            stops.append(f"qcom,poll-ms is {poll!r}: the driver would not check the temperature often enough "
                         f"(credited {POLL_MS_RANGE[0]}-{POLL_MS_RANGE[1]} ms)")
        resets = polls and in_range(reset, RESET_RANGE)
        throttles = (polls and in_range(limit, LIMIT_RANGE) and in_range(step, FREQ_STEP_RANGE)
                     and bool(cpus) and not uncovered)
        review_node = label(credited)
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

    for zone in as_list(thermal.get("zones")):
        if not isinstance(zone, dict):
            continue
        critical = [safe(trip.get("temp")) for trip in as_list(zone.get("trips"))
                    if isinstance(trip, dict) and trip.get("type") == "critical"]
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
        # Only the line ends are taken off: a name that differs by a space is another name.
        return sorted(line.rstrip("\r") for line in out.split("\n") if line.rstrip("\r"))

    def plain(folder: str, name: str) -> bool:
        if SAFE_NAME.fullmatch(name) and plain_text(name):
            return True
        failed(folder, "a node whose name is not shown was left out")
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
                if prefix + name in nodes:
                    failed(parent, "two listed names lead to the same node")
                    continue
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


# ---------------------------------------------------------------------------
# The plan: the images and the unit in fastboot, checked before any write.
# ---------------------------------------------------------------------------

# The only partitions the installer ever writes, in the order it writes them. boot, aboot,
# recovery and the rest stay as the factory left them: a unit whose boot is overwritten stays
# in fastboot until it gets a boot image signed for its board, which nobody hosts.
WRITABLE = ("system", "userdata")
FASTBOOT_VERSION = "35.0.1-11580240"
IMAGE_FILES = {f"{partition}{suffix}" for partition in WRITABLE for suffix in (".img", ".img.sha256")}
SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW, CHUNK_FILL, CHUNK_DONT_CARE, CHUNK_CRC32 = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4
EXT4_MAGIC = 0xEF53
VERITY_MAGIC = 0xB001B001
SUPERBLOCK = (1024, 1024)  # offset and length in the unpacked image
# The board's fstab marks userdata encryptable=footer: its last 16 KiB stay outside the file system.
CRYPT_FOOTER = 16384
USERDATA_RANGE = (1 << 30, 64 << 30)
READY = "ready"
EXIT_CODES[READY] = 0


def flash_arguments(serial: str, partition: str, image: str) -> list[str]:
    """fastboot's arguments for one write: the only kind of write the installer builds.

    Only ``system`` and ``userdata``, each from the image of its own name.
    """
    if partition not in WRITABLE:
        raise ValueError(f"{partition!r} is never written; only {' and '.join(WRITABLE)} are")
    if image != f"{partition}.img":
        raise ValueError(f"{partition} is written only from {partition}.img, not {image!r}")
    return ["-s", serial, "flash", partition, image]


def sha256_of(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def recorded_hash(path: pathlib.Path, image: str) -> str:
    """The sha256 a ``sha256sum`` line records for ``image``; raises ValueError if it records anything else."""
    lines = [line for line in path.read_text(encoding="ascii", errors="replace").splitlines() if line.strip()]
    match = re.fullmatch(r"([0-9a-f]{64}) [ *](\S+)", lines[0].strip()) if len(lines) == 1 else None
    if not match or match.group(2) != image:
        raise ValueError(f"{path.name} is not one sha256sum line for {image}")
    return match.group(1)


def image_layout(path: pathlib.Path) -> dict:
    """How big an image is once unpacked, its ext4 file system's size, and what follows that.

    Reads Android sparse images (walking every chunk header, so a cut-off or padded file is
    refused) and raw images. Raises ValueError for anything that is not a whole ext4 image.
    ``afterFileSystem`` is None when the file system fills the image, ``verity`` when the rest
    begins with dm-verity's metadata (the build appends it and the hash tree), else ``unknown``.
    """
    file_size = path.stat().st_size
    # (first, last, where in the file) of each run of raw data, in bytes of the unpacked image
    extents = []
    with path.open("rb") as stream:
        head = stream.read(28)
        if len(head) == 28 and struct.unpack_from("<I", head)[0] == SPARSE_MAGIC:
            (_, major, _, file_header, chunk_header, block_size, total_blocks, total_chunks,
             _) = struct.unpack("<IHHHHIIII", head)
            if major != 1 or file_header < 28 or chunk_header < 12 or not block_size or block_size % 4:
                raise ValueError("its sparse header is not one this tool reads")
            stream.seek(file_header)
            block = 0
            for _ in range(total_chunks):
                header = stream.read(chunk_header)
                if len(header) != chunk_header:
                    raise ValueError("it ends inside a chunk header")
                kind, _, blocks, total = struct.unpack_from("<HHII", header)
                data = total - chunk_header
                expected = {CHUNK_RAW: blocks * block_size, CHUNK_FILL: 4, CHUNK_DONT_CARE: 0,
                            CHUNK_CRC32: 4}.get(kind)
                if expected is None or data != expected or (kind == CHUNK_CRC32 and blocks):
                    raise ValueError(f"a chunk at block {block} is not valid")
                start = stream.tell()
                if start + data > file_size:
                    raise ValueError(f"it ends inside the chunk at block {block}")
                if kind == CHUNK_RAW:
                    extents.append((block * block_size, (block + blocks) * block_size, start))
                stream.seek(start + data)
                block += blocks
            if block != total_blocks:
                raise ValueError(f"its chunks hold {block} blocks, its header says {total_blocks}")
            if stream.tell() != file_size:
                raise ValueError("it has bytes after its last chunk")
            unpacked, sparse = total_blocks * block_size, True
        else:
            extents.append((0, file_size, 0))
            unpacked, sparse = file_size, False

        def read_at(offset: int, length: int) -> bytes | None:
            for first, last, where in extents:
                if first <= offset and offset + length <= last:
                    stream.seek(where + offset - first)
                    return stream.read(length)
            return None

        superblock = read_at(*SUPERBLOCK)
        if superblock is None or struct.unpack_from("<H", superblock, 0x38)[0] != EXT4_MAGIC:
            raise ValueError("it is not an ext4 file system")
        blocks = struct.unpack_from("<I", superblock, 0x04)[0]
        if struct.unpack_from("<I", superblock, 0x60)[0] & 0x80:  # 64bit: the count's high half
            blocks |= struct.unpack_from("<I", superblock, 0x150)[0] << 32
        log_block_size = struct.unpack_from("<I", superblock, 0x18)[0]
        if log_block_size > 6:
            raise ValueError("its ext4 block size is not valid")
        file_system = blocks * (1024 << log_block_size)
        if file_system > unpacked:
            raise ValueError(f"its file system ({file_system} bytes) is larger than the image ({unpacked})")
        after = None
        if file_system < unpacked:
            magic = read_at(file_system, 4)
            after = "verity" if magic and struct.unpack("<I", magic)[0] == VERITY_MAGIC else "unknown"
    return {"format": "sparse" if sparse else "raw", "unpackedBytes": unpacked, "fileSystemBytes": file_system,
            "afterFileSystem": after}


def check_images(folder: pathlib.Path, stops: list[str], unresolved: list[str]) -> dict:
    """What the images folder holds, each image checked against its hash and as ext4."""
    images = {}
    if not folder.is_dir():
        stops.append(f"{folder} is not a folder")
        return images
    names = sorted(entry.name for entry in folder.iterdir())
    others = [name for name in names if name not in IMAGE_FILES]
    if others:
        stops.append(f"The images folder holds {', '.join(others)} as well: it may hold only "
                     f"{', '.join(sorted(IMAGE_FILES))}, so no other image can be written by mistake")
    for partition in WRITABLE:
        image, hash_file = folder / f"{partition}.img", folder / f"{partition}.img.sha256"
        if not image.exists() and not hash_file.exists():
            if partition == "system":
                stops.append("There is no system.img")
            else:
                unresolved.append("There is no userdata.img yet: build it for this unit's partition size "
                                  "(the command is below once the unit is in fastboot)")
            continue
        if not image.is_file() or not hash_file.is_file():
            stops.append(f"{partition}.img and {partition}.img.sha256 must both be there, as files")
            continue
        try:
            expected = recorded_hash(hash_file, image.name)
        except ValueError as error:
            stops.append(str(error))
            continue
        actual = sha256_of(image)
        if actual != expected:
            stops.append(f"{image.name}'s sha256 is {actual}, but {hash_file.name} records {expected}")
            continue
        try:
            layout = image_layout(image)
        except ValueError as error:
            stops.append(f"{image.name} cannot be written: {error}")
            continue
        extra = layout["unpackedBytes"] - layout["fileSystemBytes"]
        if layout["afterFileSystem"] == "unknown" or (partition == "userdata" and extra):
            stops.append(f"{image.name} holds {extra} bytes after its file system that "
                         + ("userdata.sh never writes" if partition == "userdata"
                            else "do not begin with dm-verity's metadata"))
            continue
        images[partition] = {"sha256": actual, **layout}
    return images


def fastboot_version(tools: identify.Tools) -> str | None:
    _, out, err = tools.fastboot("--version")
    match = re.search(r"^fastboot version (\S+)", out + "\n" + err, re.MULTILINE)
    return match.group(1) if match else None


def partition_size(tools: identify.Tools, serial: str, partition: str) -> int:
    """``getvar partition-size:<partition>`` in bytes. fastboot prints it on standard error,
    always in hex, with or without 0x (CAF's bootloader sends it either way)."""
    code, out, err = tools.fastboot("-s", serial, "getvar", f"partition-size:{partition}")
    text = (err + "\n" + out).replace(serial, "<unit>")
    match = re.search(rf"^partition-size:{partition}:\s*(?:0x)?([0-9a-fA-F]{{1,16}})\s*$", text, re.MULTILINE)
    if code != 0 or not match or "FAILED" in text:
        raise RuntimeError(f"fastboot getvar partition-size:{partition} gave no size: {safe(text)}")
    return int(match.group(1), 16)


def evaluate_plan(images: dict, sizes: dict, stops: list[str], unresolved: list[str]) -> list[str]:
    """Checks the images against the partitions; the notes of what fits."""
    notes = []
    system, userdata = images.get("system"), images.get("userdata")
    if "system" in sizes and system:
        if system["unpackedBytes"] > sizes["system"]:
            stops.append(f"system.img unpacks to {system['unpackedBytes']} bytes; the system partition holds "
                         f"{sizes['system']}")
        else:
            notes.append(f"system.img ({system['unpackedBytes']} bytes unpacked) fits the system partition "
                         f"({sizes['system']} bytes)")
    if system and system["afterFileSystem"] == "verity":
        notes.append(f"system.img carries {system['unpackedBytes'] - system['fileSystemBytes']} bytes of "
                     "dm-verity metadata and hash tree after its file system; a board whose fstab has no "
                     "verify flag (the last gate over ADB) never reads them")
    if "userdata" in sizes:
        size = sizes["userdata"]
        if not USERDATA_RANGE[0] <= size <= USERDATA_RANGE[1]:
            stops.append(f"The userdata partition is {size} bytes, not between 1 and 64 GiB")
        elif userdata:
            wanted = (size - CRYPT_FOOTER) // 4096 * 4096
            if userdata["fileSystemBytes"] != wanted:
                stops.append(f"userdata.img holds a {userdata['fileSystemBytes']}-byte file system; this unit's "
                             f"partition takes {wanted} bytes ({size} less the 16 KiB encryption footer): it "
                             "was built for another partition")
            else:
                notes.append(f"userdata.img was built for this partition ({size} bytes, the last 16 KiB left "
                             "for the encryption footer)")
    return notes


def make_plan(tools: identify.Tools, folder: pathlib.Path, serial: str | None) -> dict:
    stops: list[str] = []
    unresolved: list[str] = []
    version = fastboot_version(tools)
    if version != FASTBOOT_VERSION:
        stops.append(f"fastboot is {version or 'missing or did not say its version'}, not {FASTBOOT_VERSION}: "
                     "older clients erase ext4 partitions before writing them")
    images = check_images(folder, stops, unresolved)
    state, found = identify.find_unit(tools, serial)
    sizes = {}
    if state == "fastboot":
        for partition in WRITABLE:
            try:
                sizes[partition] = partition_size(tools, found, partition)
            except RuntimeError as error:
                stops.append(str(error))
    elif state == "none":
        unresolved.append("No unit is in fastboot: power it off, then hold VOL- while powering it on")
    else:
        unresolved.append("The unit is running Android. This stage does not restart it: power it off, then "
                          "hold VOL- while powering it on, which also shows the way back works by hand")
    notes = evaluate_plan(images, sizes, stops, unresolved)
    userdata_command = None
    if "userdata" in sizes and "userdata" not in images and USERDATA_RANGE[0] <= sizes["userdata"] <= USERDATA_RANGE[1]:
        userdata_command = (f"FILE_CONTEXTS=~/silvering/units/<profile>/ramdisk/file_contexts ./userdata.sh "
                            f"{sizes['userdata']:#x} adbkey.pub userdata.img")
    verdict = STOP if stops else INCOMPLETE if unresolved else READY
    writes = [" ".join(["fastboot", *flash_arguments("<serial>", partition, f"{partition}.img")])
              for partition in WRITABLE] if verdict == READY else []
    return {"verdict": verdict, "stops": stops, "unresolved": unresolved, "notes": notes,
            "fastboot": version, "images": images, "partitionBytes": sizes, "userdataCommand": userdata_command,
            "writes": writes}


def print_plan(result: dict) -> None:
    print(f"Plan: {result['verdict'].upper()}")
    for stop in result["stops"]:
        print(f"  Stop: {stop}")
    for item in result["unresolved"]:
        print(f"  Not settled: {item}")
    for partition, image in result["images"].items():
        print(f"  {partition}.img: sha256 {image['sha256']}, {image['format']}, {image['unpackedBytes']} bytes "
              "unpacked, ext4")
    for note in result["notes"]:
        print(f"  {note}")
    if result["userdataCommand"]:
        print("  Build userdata.img on the build host, then copy it and its .sha256 here and run this stage again:")
        print(f"    {result['userdataCommand']}")
    if result["writes"]:
        print("  The only writes, from the images folder, one at a time, each only after the one before "
              "ended with 'Finished' and no 'FAILED' (silvering/FIRST-FLASH.md, In the session, step 5):")
        for write in result["writes"]:
            print(f"    {write}")


def command_plan(arguments: argparse.Namespace) -> int:
    tools = identify.Tools(identify.find_tool("adb"), identify.find_tool("fastboot"))
    try:
        result = make_plan(tools, pathlib.Path(arguments.images), arguments.serial)
    except (RuntimeError, OSError) as error:
        print(f"Could not read the unit or the images: {error}", file=sys.stderr)
        return 1
    if arguments.json:
        print(json.dumps({"schema": "mirror-install-plan/1", **result}, indent=1))
    else:
        print_plan(result)
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
    plan = stages.add_parser("plan", help="reads only: checks the images and the unit in fastboot, and prints "
                                          "the only writes allowed")
    plan.add_argument("--images", required=True, metavar="FOLDER",
                      help="a folder holding only system.img, userdata.img and their .sha256 files")
    plan.add_argument("--serial", help="the unit, when more than one is connected")
    plan.add_argument("--json", action="store_true", help="print the plan as JSON")
    plan.set_defaults(run=command_plan)
    arguments = parser.parse_args(argv)
    return arguments.run(arguments)


if __name__ == "__main__":
    sys.exit(main())
