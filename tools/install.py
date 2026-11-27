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
    flash     CHANGES THE UNIT: the plan again, which must answer ready with
              this unit the only one fastboot lists; then, once a person at the
              terminal types the confirmation exactly, the plan's two writes one
              at a time from private copies of the checked images (hashed and
              read back) and a restart once both are written. Ctrl-C stops it
              before its next step, never during a write. The unit's journal
              (one per unit on this computer) records every command and answer,
              and each run replays it to know what earlier runs left.
    verify    reads only: after the first boot, the unit's /system over ADB
              against system.img, entry by entry: inode, type and mode, owner,
              links, size, modification time, symlink target and SELinux label,
              and each regular file's sha1, hashed on the unit (sha1sum) so that
              only digests cross the cable. The board's boot image keeps
              ro.debuggable=0 and its own SELinux policy, so adb's shell can read
              neither the partition itself nor the files that policy keeps from
              it; those are listed as not read. A difference counts only if a
              second reading of both sides finds the same.

The thermal stage never answers a plain pass yet: which kernel messages prove
that the limits were registered is not known for this board, so its best
answer over USB is ``review`` (every automatic check passed; a person reads
the driver's kernel lines it lists), and over Wi-Fi ``incomplete``.

Exit codes: 1 the unit could not be read (for flash: this run could not use the
unit's journal, or another flash stage holds the unit; it sent the unit
nothing), 3 Stop (the gate fails; for flash, nothing is written and the unit's
journal records no unfinished write), 4 incomplete (what was read passes; the
rest must be read over USB, or for the plan, the unit is not in fastboot or
userdata.img is not built yet), 5 review (all automatic checks pass; a person
judges the kernel lines shown), 6 a partition may hold part of an image, or
only system is written (keep the unit in fastboot and run flash again), 7 both
partitions written but the restart was not seen (check the cable and
`fastboot devices` before any power action), 8 written and restarting, but the
journal could not be completed. 0 is the plan's ``ready``, the flash done and
every entry verified, and is kept for a thermal pass once the kernel's own
messages are known.

For verify: 0 every entry of system.img is on the unit as written and was read
in full; 5 review: no difference, but adb's shell could not read some entries
in full (listed); 3 Stop: /system differs from system.img in two readings
(restart into fastboot and run flash again); 4 incomplete: no unit runs
Android with ADB authorized; 1 nothing concluded: system.img could not be
used, the unit's answers could not be read, or two readings disagreed.
"""

from __future__ import annotations

import argparse
import codecs
import datetime
import hashlib
import json
import math
import os
import pathlib
import re
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Callable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import identify  # noqa: E402
import ext4image  # noqa: E402

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


def find_one_unit(tools: identify.Tools, serial: str | None) -> tuple[str, str | None]:
    """identify.find_unit, with a refusal that names no serial (it may be pasted into an issue)."""
    try:
        return identify.find_unit(tools, serial)
    except RuntimeError:
        raise RuntimeError("adb or fastboot lists more than one unit, or one in a state this stage cannot "
                           "use: see `adb devices` and `fastboot devices`, and choose one with --serial") from None


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
            state, serial = find_one_unit(tools, arguments.serial)
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
MAX_HASH_FILE = 1024
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


def recorded_hash(path: pathlib.Path, image: str) -> str:
    """The sha256 a ``sha256sum`` line records for ``image``; raises ValueError if it records anything else."""
    with path.open("rb") as stream:
        raw = stream.read(MAX_HASH_FILE + 1)
    lines = [line for line in raw.decode("ascii", errors="replace").splitlines() if line.strip()]
    match = re.fullmatch(r"([0-9a-f]{64}) [ *](\S+)", lines[0].strip()) \
        if len(raw) <= MAX_HASH_FILE and len(lines) == 1 else None
    if not match or match.group(2) != image:
        raise ValueError(f"{path.name} is not one sha256sum line for {image}")
    return match.group(1)


def check_superblock(superblock: bytes | None) -> int:
    """The size in bytes of the ext4 file system this superblock describes; raises ValueError
    unless the superblock is consistent.

    This tells a whole ext4 image from anything else named like one. It does not show that the
    file system mounts: the build shows that by reading the image back (stage-image.sh with
    debugfs, userdata.sh with e2fsck) before writing its sha256, and the hash ties this copy to it.
    """
    if superblock is None or struct.unpack_from("<H", superblock, 0x38)[0] != EXT4_MAGIC:
        raise ValueError("it is not an ext4 file system")
    inodes, blocks, first_block, log_block_size, blocks_per_group, inodes_per_group = (
        struct.unpack_from("<I", superblock, offset)[0] for offset in (0x00, 0x04, 0x14, 0x18, 0x20, 0x28))
    if struct.unpack_from("<I", superblock, 0x60)[0] & 0x80:
        raise ValueError("it is a 64-bit ext4 file system, which Silvering's build never makes")
    if log_block_size > 6:
        raise ValueError("its ext4 block size is not valid")
    block_size = 1024 << log_block_size
    revision, inode_size = struct.unpack_from("<I", superblock, 0x4C)[0], struct.unpack_from("<H", superblock, 0x58)[0]
    groups = -(-(blocks - first_block) // blocks_per_group) if blocks_per_group and blocks > first_block else 0
    if (first_block != (1 if block_size == 1024 else 0) or not 0 < blocks_per_group <= 8 * block_size
            or not 0 < inodes_per_group <= 8 * block_size or not groups or inodes != groups * inodes_per_group
            or (revision >= 1 and (inode_size < 128 or inode_size > block_size or inode_size & (inode_size - 1)))):
        raise ValueError("its ext4 superblock is not consistent")
    return blocks * block_size


def read_layout(stream, file_size: int) -> dict:
    """How big an image is once unpacked, its ext4 file system's size, and what follows that.

    Reads Android sparse images, walking every chunk header (a cut-off or padded file is
    refused), and raw images. Raises ValueError for anything that is not a whole ext4 image.
    ``afterFileSystem`` is None when the file system fills the image, ``verity`` when the rest
    begins with dm-verity's metadata (the build appends it and the hash tree), else ``unknown``.
    """
    stream.seek(0)
    head = stream.read(28)
    if len(head) == 28 and struct.unpack_from("<I", head)[0] == SPARSE_MAGIC:
        (_, major, _, file_header, chunk_header, block_size, total_blocks, total_chunks,
         checksum) = struct.unpack("<IHHHHIIII", head)
        if major != 1 or file_header < 28 or chunk_header < 12 or not block_size or block_size % 4:
            raise ValueError("its sparse header is not one this tool reads")
        if checksum:
            raise ValueError("it carries a sparse checksum, which this tool does not check")
        if total_chunks > total_blocks or total_chunks * chunk_header > file_size - file_header:
            raise ValueError("its header counts more chunks than it can hold")

        def read_at(offset: int, length: int) -> bytes | None:
            """Walks every chunk; the unpacked bytes at offset if all of them are data (raw or fill)."""
            stream.seek(file_header)
            found, covered, block = bytearray(length), 0, 0
            for _ in range(total_chunks):
                header = stream.read(chunk_header)
                if len(header) != chunk_header:
                    raise ValueError("it ends inside a chunk header")
                kind, _, blocks, total = struct.unpack_from("<HHII", header)
                if kind == CHUNK_CRC32:
                    raise ValueError("it carries sparse checksums, which this tool does not check")
                data = {CHUNK_RAW: blocks * block_size, CHUNK_FILL: 4, CHUNK_DONT_CARE: 0}.get(kind)
                if data is None or total - chunk_header != data or not blocks:
                    raise ValueError(f"a chunk at block {block} is not valid")
                start = stream.tell()
                if start + data > file_size:
                    raise ValueError(f"it ends inside the chunk at block {block}")
                low = max(block * block_size, offset)
                high = min((block + blocks) * block_size, offset + length)
                if low < high and kind != CHUNK_DONT_CARE:
                    if kind == CHUNK_RAW:
                        stream.seek(start + low - block * block_size)
                        piece = stream.read(high - low)
                    else:  # a fill chunk starts on a block, and blocks are whole words
                        pattern = stream.read(4)
                        piece = bytes(pattern[i % 4] for i in range(low, high)) if len(pattern) == 4 else b""
                    if len(piece) != high - low:
                        raise ValueError("it changed while it was being read")
                    found[low - offset:high - offset] = piece
                    covered += high - low
                stream.seek(start + data)
                block += blocks
            if block != total_blocks:
                raise ValueError(f"its chunks hold {block} blocks, its header says {total_blocks}")
            if stream.tell() != file_size:
                raise ValueError("it has bytes after its last chunk")
            return bytes(found) if covered == length else None

        unpacked, sparse = total_blocks * block_size, True
    else:
        def read_at(offset: int, length: int) -> bytes | None:
            stream.seek(offset)
            data = stream.read(length)
            return data if len(data) == length else None

        unpacked, sparse = file_size, False
    file_system = check_superblock(read_at(*SUPERBLOCK))
    if file_system > unpacked:
        raise ValueError(f"its file system ({file_system} bytes) is larger than the image ({unpacked})")
    after = None
    if file_system < unpacked:
        magic = read_at(file_system, 4)
        after = "verity" if magic and struct.unpack("<I", magic)[0] == VERITY_MAGIC else "unknown"
    return {"format": "sparse" if sparse else "raw", "unpackedBytes": unpacked, "fileSystemBytes": file_system,
            "afterFileSystem": after}


def image_layout(path: pathlib.Path) -> dict:
    with path.open("rb") as stream:
        return read_layout(stream, os.fstat(stream.fileno()).st_size)


def identity(status: os.stat_result) -> tuple:
    return status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns


def check_image(image: pathlib.Path, hash_file: pathlib.Path) -> dict:
    """An image hashed and read through one open handle; raises ValueError if anything is wrong,
    including either file changing while it was checked."""
    expected = recorded_hash(hash_file, image.name)
    with image.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"{image.name}'s sha256 is {digest.hexdigest()}, but {hash_file.name} records "
                             f"{expected}")
        layout = read_layout(stream, opened.st_size)
        if identity(os.fstat(stream.fileno())) != identity(opened) or identity(image.stat()) != identity(opened) \
                or recorded_hash(hash_file, image.name) != expected:
            raise ValueError(f"{image.name} or its hash changed while it was being checked")
    return {"sha256": expected, **layout, "identity": identity(opened)}


def unchanged(folder: pathlib.Path, names: list[str], images: dict) -> bool:
    """Whether the folder still holds exactly what was checked: the same names, the same image
    files and the same recorded hashes."""
    try:
        return (sorted(entry.name for entry in folder.iterdir()) == names
                and all(identity((folder / f"{partition}.img").stat()) == image["identity"]
                        and recorded_hash(folder / f"{partition}.img.sha256", f"{partition}.img") == image["sha256"]
                        for partition, image in images.items()))
    except (OSError, ValueError):
        return False


def check_images(folder: pathlib.Path, stops: list[str], unresolved: list[str]) -> tuple[dict, list[str]]:
    """What the images folder holds (its names, for the last look before ready), each image
    checked against its hash and as ext4."""
    images = {}
    if not folder.is_dir():
        stops.append(f"{folder} is not a folder")
        return images, []
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
            checked = check_image(image, hash_file)
        except ValueError as error:
            stops.append(f"{image.name} cannot be written: {error}")
            continue
        extra = checked["unpackedBytes"] - checked["fileSystemBytes"]
        if checked["afterFileSystem"] == "unknown" or (partition == "userdata" and extra):
            stops.append(f"{image.name} holds {extra} bytes after its file system that "
                         + ("userdata.sh never writes" if partition == "userdata"
                            else "do not begin with dm-verity's metadata"))
            continue
        images[partition] = checked
    return images, names


def fastboot_version(tools: identify.Tools) -> str | None:
    code, out, err = tools.fastboot("--version")
    match = re.search(r"^fastboot version (\S+)", out + "\n" + err, re.MULTILINE)
    return match.group(1) if code == 0 and match else None


def partition_size(tools: identify.Tools, serial: str, partition: str) -> int:
    """``getvar partition-size:<partition>`` in bytes. fastboot prints it on standard error,
    always in hex, with or without 0x (CAF's bootloader sends it either way). Exactly one
    answer line must name the partition, and it must hold the number alone."""
    code, out, err = tools.fastboot("-s", serial, "getvar", f"partition-size:{partition}")
    lines = [line.rstrip("\r") for line in (err + "\n" + out).splitlines()]
    answers = [line for line in lines if line.startswith(f"partition-size:{partition}:")]
    match = re.fullmatch(rf"partition-size:{partition}:[ \t]*(?:0x)?([0-9a-fA-F]{{1,16}})[ \t]*", answers[0]) \
        if len(answers) == 1 else None
    if code != 0 or not match or any("FAILED" in line for line in lines):
        shown = safe("; ".join(line for line in lines if line.strip()).replace(serial, "<unit>"))
        raise RuntimeError(f"fastboot getvar partition-size:{partition} gave no single size: {shown}")
    return int(match.group(1), 16)


SERIAL_PLACEHOLDER = "<serial>"


def render(arguments: list[str], shell: str) -> str:
    """A command for PowerShell or a POSIX shell, with the unit's serial as the shell variable
    ``$serial`` or ``$SERIAL`` in place of the placeholder."""
    if shell == "powershell":
        def word(argument: str) -> str:
            if argument == SERIAL_PLACEHOLDER:
                return "$serial"
            return argument if re.fullmatch(r"[A-Za-z0-9._-]+", argument) \
                else "'" + re.sub("(['\u2018\u2019\u201a\u201b])", r"\1\1", argument) + "'"
        return "& " + " ".join(word(argument) for argument in arguments)
    return " ".join('"$SERIAL"' if argument == SERIAL_PLACEHOLDER else shlex.quote(argument)
                    for argument in arguments)


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
    return checked_plan(tools, folder, serial)[0]


def checked_plan(tools: identify.Tools, folder: pathlib.Path, serial: str | None) -> tuple[dict, list[str], dict]:
    """The plan, with what the folder held and each image as checked (for ``unchanged``)."""
    stops: list[str] = []
    unresolved: list[str] = []
    version = fastboot_version(tools)
    if version != FASTBOOT_VERSION:
        stops.append(f"fastboot is {version or 'missing or did not say its version'}, not {FASTBOOT_VERSION}: "
                     "older clients erase ext4 partitions before writing them")
    images, names = check_images(folder, stops, unresolved)
    state, found = find_one_unit(tools, serial)
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
    if not stops and not unresolved and not unchanged(folder, names, images):
        stops.append("The images folder changed while it was being checked")
    verdict = STOP if stops else INCOMPLETE if unresolved else READY
    # The very client whose version was checked, so another fastboot on the PATH cannot answer.
    writes = [[tools.fastboot_path, *flash_arguments(SERIAL_PLACEHOLDER, partition, f"{partition}.img")]
              for partition in WRITABLE] if verdict == READY else []
    return {"verdict": verdict, "stops": stops, "unresolved": unresolved, "notes": notes,
            "fastboot": version, "fastbootPath": tools.fastboot_path, "folder": str(folder.resolve()),
            "images": {partition: {key: value for key, value in image.items() if key != "identity"}
                       for partition, image in images.items()},
            "partitionBytes": sizes, "userdataCommand": userdata_command, "writes": writes}, names, images


def print_plan(result: dict, show_writes: bool = True) -> None:
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
    if result["writes"] and show_writes:
        shell = "powershell" if os.name == "nt" else "posix"
        variable = "$serial" if shell == "powershell" else "SERIAL"
        print(f"  The only writes, in {'PowerShell' if shell == 'powershell' else 'a POSIX shell'} from "
              f"{result['folder']} with {variable} set to the serial `fastboot devices` lists, one at a time, "
              "each only after the one before ended with 'Finished' and no 'FAILED' (silvering/FIRST-FLASH.md, "
              "In the session, step 5):")
        for write in result["writes"]:
            print(f"    {render(write, shell)}")


def absolute_tool(path: str | None) -> str | None:
    """A tool's absolute path, fixed before it is first run, so the printed writes name it from any folder."""
    return str(pathlib.Path(path).resolve()) if path else None


def command_plan(arguments: argparse.Namespace) -> int:
    tools = identify.Tools(identify.find_tool("adb"), absolute_tool(identify.find_tool("fastboot")))
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


# ---------------------------------------------------------------------------
# The flash: the plan's two writes, one at a time, after a person confirms.
# ---------------------------------------------------------------------------

CONFIRMATION = "write system and userdata"
# What one fastboot flash did to its partition, and what a partition holds as far as is known.
WRITTEN, UNCHANGED, UNKNOWN = "written", "unchanged", "unknown"
RESTARTED, ACKNOWLEDGED, NOT_RESTARTED_YET = "restarted", "acknowledged", "not restarted"
# The flash stage's own exit codes. 1: this run could not use the unit's journal, or another flash
# stage holds the unit; it sent the unit nothing. 3: nothing is written and the journal records no
# unfinished write. 6: a partition may hold part of an image, or only system is written (keep the unit
# in fastboot and run the stage again). 7: both partitions are written and the restart was not seen.
# 8: written and restarting, but the journal could not be completed.
UNIT_HELD, WRITE_FAILED, NOT_RESTARTED, JOURNAL_INCOMPLETE = 1, 6, 7, 8
# fastboot 35.0.1 prints each step as "%-50s " and then "OKAY [%7.3fs]" or "FAILED (<error>)"; a
# bootloader's INFO line ("(bootloader) ...") may come between the two. A sparse image larger than the
# bootloader's max-download-size goes in pieces, each sent and then written.
STEP = re.compile(r"Sending sparse '([^'\n]*)' (\d+)/(\d+) \(\d+ KB\)|Sending '([^'\n]*)' \(\d+ KB\)"
                  r"|Writing '([^'\n]*)'|Rebooting(?= )|(OKAY \[ *\d+\.\d+s\])|FAILED \(([^\n]*)\)$", re.MULTILINE)
BOOTLOADER_INFO = re.compile(r"\(bootloader\) [^\n]*\n")
# CAF LK's refusals of a flash by policy (app/aboot/aboot.c, cmd_flash_mmc), sent before it writes
# anything; fastboot reports a bootloader's FAIL as remote: '<its message>'.
POLICY_REFUSALS = {f"remote: '{message}'" for message in ("Partition flashing is not allowed",
                                                          "Critical partition flashing is not allowed")}
FINISHED = re.compile(r"^Finished\. Total time: \d+\.\d+s$", re.MULTILINE)


def fastboot_words(output: str) -> str:
    """fastboot's own words: line ends made plain, the bootloader's INFO lines taken out."""
    return BOOTLOADER_INFO.sub("", output.replace("\r\n", "\n"))


def fastboot_steps(text: str) -> list[dict] | None:
    """The steps in fastboot's words, each with its answer; None if an answer has no step before it."""
    steps: list[dict] = []
    for match in STEP.finditer(text):
        sparse_name, piece, pieces, name, written, okay, failed = match.groups()
        if okay or failed is not None:
            if not steps or steps[-1]["answer"] is not None:
                return None
            steps[-1]["answer"] = "OKAY" if okay else "FAILED"
            steps[-1]["error"] = failed
        elif match.group(0) == "Rebooting":
            steps.append({"kind": "reboot", "partition": None, "answer": None, "error": None})
        else:
            steps.append({"kind": "write" if written is not None else "send",
                          "partition": next(value for value in (sparse_name, name, written) if value is not None),
                          "piece": (int(piece), int(pieces)) if sparse_name is not None else None,
                          "answer": None, "error": None})
    return steps


def classify_write(partition: str, code: int, output: str) -> tuple[str, str | None]:
    """What one ``fastboot flash <partition>`` did, and fastboot's error if there was one.

    ``written`` only when fastboot finished with status 0, nothing failed, and the image went as one
    send and one write, or as sparse pieces 1/N to N/N each sent and written, all to this partition.
    ``unchanged`` when fastboot failed before it began any write (it prints a step before sending its
    command, and sending only fills the bootloader's memory), or CAF's bootloader refused the first
    write by policy, which it checks before it writes anything. Anything else is ``unknown``: the
    bootloader writes chunk by chunk, so the partition may hold part of the image.
    """
    text = fastboot_words(output)
    steps = fastboot_steps(text)
    if steps is None or any(step["partition"] != partition for step in steps):
        return UNKNOWN, None
    failed = next((step for step in steps if step["answer"] == "FAILED"), None)
    writes = [step for step in steps if step["kind"] == "write"]
    if code == 0:
        sends = [step for step in steps if step["kind"] == "send"]
        pieces = [step["piece"] for step in sends]
        whole = len(steps) == 2 and pieces == [None]
        in_pieces = (len(sends) == len(writes) >= 1 and pieces[0] is not None
                     and pieces == [(number, len(sends)) for number in range(1, len(sends) + 1)])
        if (failed is None and "FAILED" not in text and FINISHED.search(text)
                and all(step["answer"] == "OKAY" for step in steps)
                and [step["kind"] for step in steps] == ["send", "write"] * len(writes) and (whole or in_pieces)):
            return WRITTEN, None
        return UNKNOWN, None
    if not writes:
        return UNCHANGED, failed["error"] if failed else None
    if (len(steps) == 2 and steps[0]["kind"] == "send" and steps[0]["answer"] == "OKAY"
            and (steps[0]["piece"] is None or steps[0]["piece"][0] == 1)
            and writes[0]["answer"] == "FAILED" and writes[0]["error"] in POLICY_REFUSALS):
        return UNCHANGED, writes[0]["error"]
    return UNKNOWN, failed["error"] if failed else None


def restart_outcome(code: int, output: str) -> str:
    """``restarted`` when fastboot finished a reboot the bootloader acknowledged; ``acknowledged`` when
    the bootloader acknowledged it but fastboot did not finish (the unit may be starting);
    otherwise ``not restarted``."""
    text = fastboot_words(output)
    steps = fastboot_steps(text) or []
    acknowledged = [(step["kind"], step["answer"]) for step in steps] == [("reboot", "OKAY")]
    if acknowledged and code == 0 and "FAILED" not in text and FINISHED.search(text):
        return RESTARTED
    return ACKNOWLEDGED if any(step["kind"] == "reboot" and step["answer"] == "OKAY" for step in steps) \
        else NOT_RESTARTED_YET


def reboot_arguments(serial: str) -> list[str]:
    return ["-s", serial, "reboot"]


# Standard streams that could not be written and were pointed at the null device.
SILENCED: set[int] = set()


def silence(stream) -> None:
    """Points a standard stream that can no longer be written at the null device, so that Python's own
    flush at exit cannot fail and replace the exit code (it would exit 120), and stops ``say`` from
    using it."""
    SILENCED.add(id(stream))
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, stream.fileno())
        os.close(null)
    except (OSError, ValueError, AttributeError):  # not a real file, as in tests
        pass


def say(text: str, error: bool = False) -> None:
    """Prints, falling back to the other stream, and never raises: what to do next must not be lost
    to a closed window or pipe."""
    for stream in (sys.stderr, sys.stdout) if error else (sys.stdout, sys.stderr):
        if id(stream) in SILENCED:
            continue
        try:
            print(text, file=stream, flush=True)
            return
        except Exception:  # noqa: BLE001 - any failure to print falls back to the other stream
            silence(stream)


def stream_tool(arguments: list[str], folder: pathlib.Path) -> identify.Result:
    """Runs one fastboot command from ``folder`` with no time limit, showing what it prints as it
    prints it and keeping all of it. fastboot runs in its own process group, so Ctrl-C in the
    console never stops it halfway through a write; it ends each command on its own.

    Never returns while fastboot is still running, even if showing its words fails."""
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" \
        else {"start_new_session": True}
    try:
        process = subprocess.Popen(arguments, cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, **group)
    except OSError as error:
        return 1, "", f"fastboot did not start: {error}"
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    kept, showing = bytearray(), True
    try:
        while chunk := process.stdout.read1(4096):
            kept += chunk
            if showing:
                try:
                    sys.stdout.write(decoder.decode(chunk))
                    sys.stdout.flush()
                except (OSError, ValueError):
                    showing = False
                    silence(sys.stdout)
    finally:
        code = process.wait()
        process.stdout.close()
    return code, kept.decode("utf-8", errors="replace"), ""


def hide_devices(output: str, serial: str) -> str:
    """A ``devices`` listing with each serial written as <unit> (this one) or <other>."""
    lines = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and not line.startswith(("List of", "*")):
            line = " ".join(["<unit>" if fields[0] == serial else "<other>", *fields[1:]])
        lines.append(line)
    return "\n".join(lines)


class UnitState:
    """What the unit holds as far as its journal knows: for each partition None (not written by the
    installation under way, or as it was), ``written``, or ``unknown`` (a write began and did not
    finish), and whether that installation restarted the unit. Only journal entries change it, the
    same way when a run makes them and when a later run replays them, so both always agree."""

    def __init__(self):
        self.partitions: dict = {partition: None for partition in WRITABLE}
        self.restarted = False
        self.pending: dict = {}

    def apply(self, entry: dict) -> None:
        event, partition = entry.get("event"), entry.get("partition")
        if event == "write" and partition in self.partitions:
            # What it held before, should the write turn out to have changed nothing.
            self.pending[partition] = (dict(self.partitions), self.restarted)
            self.partitions[partition] = UNKNOWN
            if partition == WRITABLE[0]:
                # A new installation: the userdata of an earlier one is not this one's.
                for later in WRITABLE[1:]:
                    self.partitions[later] = None
            self.restarted = False
        elif event in ("answer", "not sent") and partition in self.partitions:
            if partition not in self.pending:
                # Its write entry was lost (a damaged line): the answer still shows a write was sent.
                self.apply({"event": "write", "partition": partition})
            before = self.pending.pop(partition)
            if event == "not sent" or entry.get("outcome") == UNCHANGED:
                self.partitions, self.restarted = dict(before[0]), before[1]
            elif entry.get("outcome") == WRITTEN:
                self.partitions[partition] = WRITTEN
        elif event == "answer" and partition is None and entry.get("outcome") == RESTARTED:
            self.restarted = True


class UnitHeld(Exception):
    """Another flash stage on this computer holds the unit."""


def lock(handle, release: bool = False) -> None:
    """Takes (or gives back) an exclusive lock on an open file; raises OSError if another process holds it."""
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB)


class Journal:
    """The unit's journal: every command the flash stage sends and every answer, one JSON line each,
    flushed to disk before the next step, the unit's serial written as <unit> wherever it appears
    (but in the fields the replay reads, which never hold it). Opening it locks the unit for this run
    and replays the earlier runs into ``state``; lines that cannot be read (``damaged``, such as one
    cut off when a run was killed) are passed over, and ``report`` then never calls the unit clean."""

    # Fields the replay reads: written as they are, so hiding the serial can never change them.
    CONTROLLED = ("event", "partition", "outcome")

    def __init__(self, path: pathlib.Path, serial: str):
        self.path, self.serial, self.failed, self.damaged = path, serial, False, 0
        self.state = UnitState()
        new_folder = not path.parent.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        if new_folder:
            hand_back(path.parent)
        lock_path = path.with_name(path.name + ".lock")
        new_lock = not lock_path.exists()
        self.lock = lock_path.open("a+b")
        if new_lock:
            hand_back(self.lock.fileno())
        try:
            lock(self.lock)
        except OSError:
            self.lock.close()
            raise UnitHeld() from None
        try:
            new_file = not path.exists()
            text = path.read_bytes().decode("utf-8", errors="replace") if not new_file else ""
            for line in text.splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    entry = None
                if isinstance(entry, dict) and isinstance(entry.get("event"), str):
                    self.state.apply(entry)
                elif line.strip():
                    # Whatever this line was, it may have been a write of system that did not finish:
                    # count it as one, until a later installation completes.
                    self.damaged += 1
                    self.state.apply({"event": "write", "partition": WRITABLE[0]})
            self.stream = path.open("a", encoding="utf-8")
            if new_file:
                hand_back(self.stream.fileno())
            if new_file:
                # The file's name must survive a power cut as surely as its lines (Linux fsync(2)).
                sync_folder(path.parent)
                if new_folder:
                    sync_folder(path.parent.parent)
            if text and not text.endswith("\n"):
                self.stream.write("\n")  # the next entry starts a line of its own
        except BaseException:
            self.release()
            raise

    def hide(self, value):
        if isinstance(value, str):
            return value.replace(self.serial, "<unit>")
        if isinstance(value, list):
            return [self.hide(item) for item in value]
        if isinstance(value, dict):
            return {key: self.hide(item) for key, item in value.items()}
        return value

    def record(self, event: str, **fields) -> None:
        """Writes one entry and syncs it, then lets it change ``state``. An entry announcing a command
        that cannot be written stops the command from being sent, so it changes nothing; an answer
        that cannot be written is still known, so it changes ``state`` before the error is raised."""
        entry = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                 "event": event, **fields}
        shown = {key: value if key in self.CONTROLLED else self.hide(value) for key, value in entry.items()}
        try:
            self.stream.write(json.dumps(shown) + "\n")
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except OSError:
            self.failed = True
            if event not in ("write", "restart"):
                self.state.apply(entry)
            raise
        self.state.apply(entry)

    def release(self) -> None:
        for close in (lambda: self.stream.close(), lambda: lock(self.lock, release=True), lambda: self.lock.close()):
            try:
                close()
            except (OSError, AttributeError):
                pass


def recording(tools: identify.Tools, journal: Journal, serial: str) -> identify.Tools:
    """The same tools, with every command and its answer recorded before the answer is used."""
    def runner(arguments: list[str]) -> identify.Result:
        code, out, err = tools.runner(arguments)
        listing = arguments[-1:] == ["devices"]
        journal.record("read", command=arguments, exitCode=code,
                       output=hide_devices(out, serial) if listing else out,
                       error=hide_devices(err, serial) if listing else err)
        return code, out, err
    return identify.Tools(tools.adb_path, tools.fastboot_path, runner)


def invoking_home(environ=os.environ, euid: int | None = None, getpwnam=None) -> pathlib.Path:
    """The home folder of the person running the installer, also under sudo, whose HOME may be
    root's: every attempt on a unit then finds the same journal."""
    if euid is None:
        euid = os.geteuid() if hasattr(os, "geteuid") else -1
    if euid == 0 and environ.get("SUDO_USER"):
        if getpwnam is None:
            import pwd
            getpwnam = pwd.getpwnam
        return pathlib.Path(getpwnam(environ["SUDO_USER"]).pw_dir)
    return pathlib.Path.home()


def hand_back(target) -> None:
    """Under sudo, gives a file or folder this run has just made (an open descriptor, or a folder's
    path) back to the person who ran sudo, so that a later run without sudo can still write the
    unit's journal. Never used on anything that existed before."""
    if not (hasattr(os, "geteuid") and os.geteuid() == 0 and os.environ.get("SUDO_UID")):
        return
    try:
        uid, gid = int(os.environ["SUDO_UID"]), int(os.environ.get("SUDO_GID", "-1"))
        if isinstance(target, int):
            os.fchown(target, uid, gid)
        else:
            descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fchown(descriptor, uid, gid)
            finally:
                os.close(descriptor)
    except (OSError, ValueError):
        pass


def sync_folder(folder: pathlib.Path) -> None:
    """Syncs a folder's own entries to disk (POSIX; Windows offers no handle to a folder for it)."""
    if os.name == "nt":
        return
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def default_journal(serial: str) -> pathlib.Path:
    """The unit's journal: one per unit on this computer for the person running the installer,
    whichever folder its images come from, so that every run knows what earlier runs left on the
    unit. Named by a hash of the serial."""
    return invoking_home() / ".mirror-install" / f"flash-{hashlib.sha256(serial.encode()).hexdigest()[:16]}.jsonl"


def fastboot_running(posix: bool | None = None) -> bool | None:
    """Whether a fastboot program is running on this computer (any account), such as one left
    running when an earlier run was stopped; None if that cannot be told (the lister is missing,
    fails or times out). Only a lister that ran and found none answers False."""
    posix = os.name != "nt" if posix is None else posix
    command = ["pgrep", "-x", "fastboot"] if posix else \
        ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq fastboot.exe"]
    try:
        done = subprocess.run(command, text=True, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if posix:
        return {0: True, 1: False}.get(done.returncode)
    if done.returncode != 0:
        return None
    return any(line.lower().startswith('"fastboot.exe"') for line in done.stdout.splitlines())


def journal_problem(journal: pathlib.Path, folder: pathlib.Path) -> str | None:
    path = journal.resolve()
    if path == folder or folder in path.parents:
        return "The journal cannot be in the images folder: the folder may hold only the images and their hashes"
    if path.exists():
        if not path.is_file():
            return f"The journal {journal} is not a file"
        try:
            entries = list(folder.iterdir()) if folder.is_dir() else []
        except OSError:
            entries = []
        for entry in entries:
            try:
                if os.path.samefile(path, entry):
                    return f"The journal {journal} is the same file as {entry.name} in the images folder"
            except OSError:
                continue
    return None


def the_only_fastboot_unit(tools: identify.Tools, serial: str) -> bool:
    """Whether ``fastboot devices`` lists exactly one row, and it is this unit in fastboot (fastboot
    35.0.1 prints ``<serial>\\t fastboot``). A row of any other shape (a device without permissions,
    an interface name) counts as another device."""
    code, out, _ = tools.fastboot("devices")
    return code == 0 and [line.split() for line in out.splitlines() if line.strip()] == [[serial, "fastboot"]]


def remove_copies(folder: pathlib.Path) -> None:
    def writable(function, path, _):
        os.chmod(path, 0o600)
        function(path)
    shutil.rmtree(folder, **({"onexc": writable} if sys.version_info >= (3, 12) else {"onerror": writable}))


def private_copies(folder: pathlib.Path, images: dict) -> pathlib.Path:
    """The checked images copied into a new folder that only this run uses (beside the images folder,
    on the disk that already holds them), each hashed while it is read from the folder, made
    read-only, and read back and hashed again. fastboot writes from these copies, so the bytes it
    sends are the bytes that were checked, whatever happens to the images folder meanwhile."""
    copies = pathlib.Path(tempfile.mkdtemp(prefix=f".{folder.name}-flashing-", dir=folder.parent))
    try:
        for partition, image in images.items():
            copy = copies / f"{partition}.img"
            digest = hashlib.sha256()
            with (folder / f"{partition}.img").open("rb") as source, copy.open("xb") as target:
                if identity(os.fstat(source.fileno())) != image["identity"]:
                    raise ValueError(f"{partition}.img changed after it was checked")
                while chunk := source.read(1 << 20):
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if digest.hexdigest() != image["sha256"]:
                raise ValueError(f"{partition}.img changed after it was checked")
            copy.chmod(stat.S_IREAD)
            readback = hashlib.sha256()
            with copy.open("rb") as written:
                opened = identity(os.fstat(written.fileno()))
                while chunk := written.read(1 << 20):
                    readback.update(chunk)
            if readback.hexdigest() != image["sha256"]:
                raise ValueError(f"the copy of {partition}.img does not read back as it was written")
            image["copy"] = opened
    except BaseException:
        remove_copies(copies)
        raise
    return copies


def copies_unchanged(copies: pathlib.Path, images: dict) -> bool:
    try:
        return (sorted(entry.name for entry in copies.iterdir()) == sorted(f"{p}.img" for p in images)
                and all(identity((copies / f"{p}.img").stat()) == image["copy"] for p, image in images.items()))
    except OSError:
        return False


Writer = Callable[[list[str], pathlib.Path], identify.Result]
Confirm = Callable[[str], str]


def confirmation_text(result: dict) -> str:
    images = result["images"]
    lines = ["", "This writes the unit, in this order, each only after the one before has finished:"]
    for partition in WRITABLE:
        lines.append(f"  {partition} <- {partition}.img (sha256 {images[partition]['sha256']}, partition "
                     f"{result['partitionBytes'][partition]} bytes)")
    lines += ["then restarts it. Everything in the unit's userdata (pairings, layout, photos, films, Wi-Fi",
              "networks) is lost. Nothing else is written: not boot, aboot, recovery or any other partition.",
              "Ctrl-C does not stop a write under way: it stops this stage before its next step.",
              "",
              "silvering/FIRST-FLASH.md asks for these first, and this stage cannot check them:",
              "  - the last gate over ADB passed (/fstab.qcom: /system without verify, /data encryptable=footer;",
              "    ro.vold.forceencryption empty or 0)",
              "  - `install.py thermal --serial` answered review, and a person read and accepted its kernel lines",
              "  - the way back was shown by hand: fastboot reached with VOL- at power-on, and `fastboot reboot`",
              "    returned to Android",
              "  - the owner approved building the image without the files the unit would not give up",
              "",
              f"Type `{CONFIRMATION}` to go on; anything else stops here with nothing written: "]
    return "\n".join(lines)


def report(journal: Journal, sent_write: bool, restart: str | None, error: str | None, reason: str | None,
           restart_command: str) -> int:
    """What the unit holds now, from its journal (this run included), and what to do; the exit code.
    ``sent_write`` says whether this run sent a write; ``restart`` is this run's restart outcome, or
    None if it sent none."""
    held, restarted = journal.state.partitions, journal.state.restarted
    said = f" fastboot reported: {safe(error)}." if error else ""
    why = f" ({reason})" if reason else ""
    stay = ("Keep the unit in fastboot and run this stage again (it writes system, then userdata). If the "
            "cable came out, plug it back in; if the unit has left fastboot, power it off and hold VOL- while "
            "powering it on, so that it comes back to fastboot without starting the unfinished system.")
    if all(state is None for state in held.values()) or (restarted and restart != RESTARTED
                                                         and held["system"] == held["userdata"] == WRITTEN):
        code = EXIT_CODES[STOP]
        if restarted:
            text = (f"This run wrote nothing{why}. The journal shows that the last installation wrote system and "
                    f"userdata and restarted the unit.{said}")
        elif sent_write:
            text = (f"Nothing was written: fastboot stopped before writing{why}.{said} The journal records no "
                    "unfinished write on this unit, so it is as it was (unless it was written outside this "
                    "installer). If the bootloader refused the write, this unit cannot be flashed this way: "
                    f"restart it with `{restart_command}` and Stop (silvering/FIRST-FLASH.md, In the session, "
                    "step 5). If the cable or the transfer failed, this stage may be run again.")
        else:
            text = f"Nothing was written{why}. The journal records no write on this unit."
    elif UNKNOWN in held.values():
        partial = " and ".join(partition for partition in WRITABLE if held[partition] == UNKNOWN)
        code, text = WRITE_FAILED, f"{partial} may hold part of an image{why}.{said} {stay}"
    elif held["system"] != WRITTEN or held["userdata"] != WRITTEN:
        code, text = WRITE_FAILED, f"system is written and userdata is not{why}.{said} {stay}"
    elif restarted:
        code = 0
        text = ("system and userdata are written and the unit is restarting into Silvering. Next "
                "(silvering/FIRST-FLASH.md, In the session, step 6): `adb -s <serial> wait-for-device`, then wait "
                "for sys.boot_completed.")
    else:
        code = NOT_RESTARTED
        seen = ("the bootloader accepted the restart, but fastboot did not confirm it" if restart == ACKNOWLEDGED
                else "this run did not restart the unit")
        text = (f"system and userdata are written, but {seen}{why}.{said} Both are written, so the unit may start. "
                f"Check the cable, then `fastboot devices`: if it lists the unit, restart it with `{restart_command}`. "
                "If `adb devices` lists it instead, it is starting. If neither lists it, plug the cable in again "
                "and look again before holding the power button.")
    if journal.damaged:
        text += (f" {journal.damaged} line(s) of the journal could not be read; each counts as a write of system "
                 "that may not have finished, until a later installation completes. Keep the journal: it is the "
                 "only record of what earlier runs left on the unit.")
    if journal.failed:
        text += f" The journal could not be completed, so it lacks this run's last steps: {journal.path}."
        if code == 0:
            code = JOURNAL_INCOMPLETE
    else:
        text += f" Journal: {journal.path}"
    say("\n" + text)
    return code


class Stop(Exception):
    """Ends the flash stage before its next step; the report says why."""


def flash(tools: identify.Tools, writer: Writer, folder: pathlib.Path, serial: str, journal_path: pathlib.Path,
          confirm: Confirm, interactive: bool = True) -> int:
    """The plan, checked again; then, once a person types the confirmation, the plan's two writes and a
    restart. Returns the exit code (see ``report``)."""
    folder = folder.resolve()
    restart_command = render([tools.fastboot_path or "fastboot", *reboot_arguments(SERIAL_PLACEHOLDER)],
                             "powershell" if os.name == "nt" else "posix")
    problem = journal_problem(journal_path, folder)
    if problem:
        say(f"{problem}. This run sent the unit nothing.", error=True)
        return UNIT_HELD
    try:
        journal = Journal(journal_path, serial)
    except UnitHeld:
        say("Another flash stage on this computer is working on this unit: wait for it to end and follow its "
            "advice. This run sent the unit nothing.", error=True)
        return UNIT_HELD
    except (OSError, ValueError) as error:
        say(f"The unit's journal {journal_path} cannot be used ({error}). This run sent the unit nothing; what "
            "earlier runs left on it is in that journal.", error=True)
        return UNIT_HELD
    copies, sent_write, restart, error, reason = None, False, None, None, None
    interrupted: list[bool] = []
    previous, installed = None, False

    def step_problem() -> str | None:
        """Why the next step may not be sent: read-only checks, then Ctrl-C last, so that a Ctrl-C
        during the checks is still seen before the step is announced."""
        running = fastboot_running()
        problem = ("another fastboot program is running on this computer" if running
                   else "it cannot be told whether another fastboot program is running" if running is None
                   else "fastboot no longer lists this unit alone" if not the_only_fastboot_unit(tools, serial)
                   else None if copies_unchanged(copies, images) else "the checked copies changed")
        return problem or ("Ctrl-C" if interrupted else None)

    try:
        try:
            journal.record("flash stage", fastbootVersionRequired=FASTBOOT_VERSION, folder=str(folder))
            if not interactive:
                raise Stop("this stage writes only from a terminal, where a person types the confirmation")
            if fastboot_running() is not False:
                raise Stop("another fastboot program may be running on this computer, perhaps one left by an "
                           "earlier run that was stopped: wait until none is")
            tools = recording(tools, journal, serial)
            result, names, images = checked_plan(tools, folder, serial)
            print_plan(result, show_writes=False)
            journal.record("plan", verdict=result["verdict"], stops=result["stops"],
                           unresolved=result["unresolved"], images=result["images"],
                           partitionBytes=result["partitionBytes"])
            if result["verdict"] != READY:
                raise Stop(f"the plan is {result['verdict']}, not ready")
            if not the_only_fastboot_unit(tools, serial):
                raise Stop("fastboot must list this unit and no other")
            try:
                answer = confirm(confirmation_text(result))
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer != CONFIRMATION:
                journal.record("not confirmed")
                raise Stop("the confirmation was not typed")
            journal.record("confirmed", typed=CONFIRMATION)
            copies = private_copies(folder, images)
            # From here on Ctrl-C only asks this stage to stop before its next step; fastboot, in a
            # process group of its own, does not see it.
            previous, installed = signal.signal(signal.SIGINT, lambda *_: interrupted.append(True)), True
            for partition in WRITABLE:
                problem = step_problem()
                if problem:
                    journal.record("refused", partition=partition, reason=problem)
                    raise Stop(problem)
                say(f"\nWriting {partition} (do not unplug the unit or close this window):")
                arguments = [tools.fastboot_path, *flash_arguments(serial, partition, f"{partition}.img")]
                journal.record("write", partition=partition, command=arguments)
                sent_write = True
                code, output, note = writer(arguments, copies)
                outcome, error = classify_write(partition, code, output)
                journal.record("answer", partition=partition, exitCode=code, outcome=outcome, output=output,
                               note=note)
                if outcome != WRITTEN:
                    raise Stop(None)
            problem = step_problem()
            if problem:
                journal.record("refused", reason=problem)
                raise Stop(problem)
            arguments = [tools.fastboot_path, *reboot_arguments(serial)]
            journal.record("restart", command=arguments)
            restart = NOT_RESTARTED_YET
            code, output, note = writer(arguments, copies)
            restart = restart_outcome(code, output)
            journal.record("answer", exitCode=code, outcome=restart, output=output, note=note)
        except Stop as stop:
            reason = stop.args[0]
        except BaseException as caught:  # anything at all: report from what is known, never guess
            reason = f"stopped: {safe(str(caught).replace(serial, '<unit>')) if str(caught) else type(caught).__name__}"
        finally:
            if copies is not None:
                try:
                    remove_copies(copies)
                except BaseException:  # noqa: BLE001 - the report matters more than leftover copies
                    say(f"The private copies in {copies} could not be removed; remove them by hand.", error=True)
        if not installed:
            try:
                previous, installed = signal.signal(signal.SIGINT, lambda *_: interrupted.append(True)), True
            except ValueError:  # not the main thread, as when called from another program
                pass
        return report(journal, sent_write, restart, error and error.replace(serial, "<unit>"), reason,
                      restart_command)
    finally:
        journal.release()
        if installed and previous is not None:
            signal.signal(signal.SIGINT, previous)


def command_flash(arguments: argparse.Namespace) -> int:
    tools = identify.Tools(identify.find_tool("adb"), absolute_tool(identify.find_tool("fastboot")))
    return flash(tools, stream_tool, pathlib.Path(arguments.images), arguments.serial,
                 default_journal(arguments.serial), input, interactive=sys.stdin.isatty())


# ---------------------------------------------------------------------------
# The check after the first boot: the unit's /system, entry by entry, against system.img.
# ---------------------------------------------------------------------------

VERIFIED = "verified"
EXIT_CODES[VERIFIED] = 0
SYSTEM_MOUNT = "/system"
VERIFY_TIMEOUT = 1800
VERIFY_MARKER = "__mirror_verify_end="
# What make_ext4fs images hold; only such names can be told apart without doubt in the unit's answers.
COMPARABLE_NAME = re.compile(r"[A-Za-z0-9._+@,=~-]{1,255}")
COMPARABLE_TARGET = re.compile(r"[A-Za-z0-9._+@,=~/-]{1,1023}")
STAT_FORMAT = "%i %f %u %g %h %s %Y %n"
STAT_LINE = re.compile(r"(\d+) ([0-9a-f]+) (\d+) (\d+) (\d+) (\d+) (-?\d+) (/.+)")
LABEL_LINE = re.compile(r"([-dlcbps][-rwxsStT]{9}) +\S+ +\S+ +(\S+) (.+)")
HASH_LINE = re.compile(r"([0-9a-f]{40})  (/.+)")
FIND_ERROR = re.compile(r"find: (?:No )?(/.+?): ([^:]+)")
STAT_FIELDS = ("inode", "mode", "uid", "gid", "links", "size", "mtime")
# toolbox ls reads a link into 256 bytes and shows a longer target as its first 252 and "...".
LS_LINK_BYTES = 255
# Each pass prints only standard output: a buffered output and an unbuffered error stream would mix
# inside lines. This toybox (c96e424, Silvering's and the API 23 emulator's) never runs the command of
# `find -exec ... {} +`, hence xargs -0, and its `stat %N` prints a link target without ending it, so
# targets come from toolbox ls.
VERIFY_PASSES = {
    "names": f"find {SYSTEM_MOUNT} -xdev 2>/dev/null",
    "errors": f"find {SYSTEM_MOUNT} -xdev 2>&1 >/dev/null",
    "stat": f"find {SYSTEM_MOUNT} -xdev -print0 2>/dev/null | xargs -0 stat -c '{STAT_FORMAT}' 2>/dev/null",
    "labels": f"echo '@@ /'; ls -dZ {SYSTEM_MOUNT} 2>/dev/null; find {SYSTEM_MOUNT} -xdev -type d -print0 "
              "2>/dev/null | xargs -0 sh -c 'for d; do echo \"@@ $d\"; ls -aZ \"$d\" 2>/dev/null; done' sh",
    "hashes": f"find {SYSTEM_MOUNT} -xdev -type f -print0 2>/dev/null | xargs -0 sha1sum 2>/dev/null",
}


def verify_runner(arguments: list[str]) -> identify.Result:
    """Like identify.run_tool, with time to hash a whole system partition, and the unit's bytes read
    as UTF-8 so that no name it prints can stop the stage."""
    try:
        completed = subprocess.run(arguments, capture_output=True, timeout=VERIFY_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, "", str(error)
    return (completed.returncode, completed.stdout.decode("utf-8", "replace"),
            completed.stderr.decode("utf-8", "replace"))


def expected_system(folder: pathlib.Path) -> tuple[list[dict], str, str | None]:
    """system.img's entries as the unit must show them under /system, its sha256 and its build
    fingerprint. The image is hashed and read through one open handle; raises ValueError if it does not
    match its sha256 file or changes while it is read."""
    image, hash_file = folder / "system.img", folder / "system.img.sha256"
    expected = recorded_hash(hash_file, image.name)
    with image.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        digest = hashlib.sha256()
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError(f"system.img's sha256 is {digest.hexdigest()}, but {hash_file.name} records {expected}")
        file_system = ext4image.Ext4(ext4image.Unpacked(stream, opened.st_size))
        entries = file_system.walk(SYSTEM_MOUNT)
        fingerprint = None
        properties = [entry for entry in entries if entry["path"] == f"{SYSTEM_MOUNT}/build.prop"]
        if properties and stat.S_ISREG(properties[0]["mode"]):
            text = b"".join(file_system.data(file_system.inode(properties[0]["inode"]))).decode("utf-8", "replace")
            found = re.search(r"^ro\.build\.fingerprint=(.*)$", text, re.MULTILINE)
            fingerprint = found.group(1).strip() if found else None
        if identity(os.fstat(stream.fileno())) != identity(opened) or identity(image.stat()) != identity(opened) \
                or recorded_hash(hash_file, image.name) != expected:
            raise ValueError("system.img or its hash changed while it was being read")
    return entries, expected, fingerprint


def read_system_over_adb(tools: identify.Tools, serial: str) -> dict:
    """What adb's shell sees under /system: names, what find could not read, each entry's lstat, each
    entry's SELinux label and each regular file's sha1, hashed on the unit so that only digests cross
    the cable. Raises RuntimeError if a pass did not run to its end or printed a line it should not."""
    def run(name: str) -> list[str]:
        code, out, err = tools.adb("-s", serial, "shell", f"{VERIFY_PASSES[name]}; echo {VERIFY_MARKER}$?")
        body, marker, status = out.rpartition(VERIFY_MARKER)
        if code != 0 or not marker or not status.strip().isdigit():
            shown = safe((out + err).replace(serial, "<unit>")[-300:])
            raise RuntimeError(f"adb did not finish the {name} pass: {shown}")
        return [line.rstrip("\r") for line in body.split("\n") if line.rstrip("\r")]

    def strange(name: str, line: str) -> RuntimeError:
        return RuntimeError(f"the {name} pass printed a line this stage does not read: {safe(line)}")

    def under_mount(path: str) -> bool:
        return path == SYSTEM_MOUNT or path.startswith(SYSTEM_MOUNT + "/")

    def add(found: dict, name: str, path: str, value) -> None:
        if not under_mount(path) or path in found:
            raise RuntimeError(f"the {name} pass named {safe(path)} {'twice' if path in found else 'outside /system'}")
        found[path] = value

    seen: dict = {"names": {}, "errors": {}, "stat": {}, "labels": {}, "hashes": {}}
    for line in run("names"):
        add(seen["names"], "names", line, True)
    for line in run("errors"):
        match = FIND_ERROR.fullmatch(line)
        if not match:
            raise strange("errors", line)
        add(seen["errors"], "errors", match.group(1), match.group(2).strip())
    for line in run("stat"):
        match = STAT_LINE.fullmatch(line)
        if not match:
            raise strange("stat", line)
        inode, mode, uid, gid, links, size, mtime, path = match.groups()
        add(seen["stat"], "stat", path, {"inode": int(inode), "mode": int(mode, 16), "uid": int(uid), "gid": int(gid),
                                         "links": int(links), "size": int(size), "mtime": int(mtime)})
    folder = None
    for line in run("labels"):
        if line.startswith("@@ "):
            folder = line[3:]
            continue
        match = LABEL_LINE.fullmatch(line)
        if not match or folder is None:
            raise strange("labels", line)
        name, target = match.group(3), None
        if match.group(1).startswith("l"):
            name, arrow, target = name.partition(" -> ")
            if not arrow:
                raise strange("labels", line)
        add(seen["labels"], "labels", f"{folder.rstrip('/')}/{name}", {"label": match.group(2), "target": target})
    for line in run("hashes"):
        match = HASH_LINE.fullmatch(line)
        if not match:
            raise strange("hashes", line)
        add(seen["hashes"], "hashes", match.group(2), match.group(1))
    if SYSTEM_MOUNT not in seen["names"] or SYSTEM_MOUNT not in seen["stat"] \
            or SYSTEM_MOUNT not in seen["labels"]:
        raise RuntimeError(f"adb's shell did not list, stat and label {SYSTEM_MOUNT} itself, so its tools did "
                           "not run as this stage expects")
    return seen


def comparable(entry: dict) -> bool:
    """Whether every name on the entry's path, and its link target, can be told apart in the unit's answers."""
    names = entry["path"][len(SYSTEM_MOUNT):].split("/")[1:]
    return all(COMPARABLE_NAME.fullmatch(name) for name in names) \
        and ("target" not in entry or bool(COMPARABLE_TARGET.fullmatch(entry["target"])))


def compare_system(expected: list[dict], seen: dict) -> dict:
    """The unit's answers against the image: each entry that differs, and each that adb's shell could
    not read in full (with what was left unread)."""
    differences, unread = [], []
    hashed_bytes = file_bytes = full = 0
    paths = {entry["path"] for entry in expected}
    for entry in expected:
        path = entry["path"]
        regular = stat.S_ISREG(entry["mode"])
        file_bytes += entry["size"] if regular else 0
        what = {"path": path, "label": entry["label"], "size": entry["size"]}
        if not comparable(entry):
            unread.append({**what, "reason": "its name or link target holds characters this check cannot compare"})
            continue
        found = seen["stat"].get(path)
        if found is None:
            ancestors = [path[:index] for index in range(len(path)) if path[index] == "/"]
            if path in seen["errors"]:
                unread.append({**what, "reason": f"find: {seen['errors'][path]}"})
            elif path in seen["names"]:
                unread.append({**what, "reason": "listed, but stat printed nothing"})
            elif any(ancestor in seen["errors"] for ancestor in ancestors):
                unread.append({**what, "reason": "in a folder adb's shell may not open"})
            else:
                differences.append({"path": path, "what": "missing on the unit"})
            continue
        changed = [f"{field} {entry[field]!r} in the image, {found[field]!r} on the unit"
                   for field in STAT_FIELDS if entry[field] != found[field]]
        missing = []
        listed = seen["labels"].get(path)
        if listed is not None:
            if listed["label"] != entry["label"]:
                changed.append(f"label {entry['label']!r} in the image, {listed['label']!r} on the unit")
            target = entry.get("target")
            shown = target if target is None or len(target) <= LS_LINK_BYTES else target[:LS_LINK_BYTES - 3] + "..."
            if listed["target"] != shown:
                changed.append(f"target {target!r} in the image, {listed['target']!r} on the unit")
            elif shown != target:
                missing.append("the link target's end")
        else:
            missing.append("label" + (" and link target" if "target" in entry else ""))
        if regular:
            if path in seen["hashes"]:
                if seen["hashes"][path] != entry["sha1"]:
                    changed.append(f"sha1 {entry['sha1']} in the image, {seen['hashes'][path]} on the unit")
                else:
                    hashed_bytes += entry["size"]
            else:
                missing.append("content")
        if changed:
            differences.append({"path": path, "what": "; ".join(changed)})
        elif missing:
            unread.append({**what, "reason": f"{' and '.join(missing)} not read"})
        else:
            full += 1
    for source in ("names", "errors", "stat", "labels", "hashes"):
        for path in seen[source]:
            if path not in paths and not any(item["path"] == path for item in differences):
                differences.append({"path": path, "what": "on the unit, not in system.img"})
    verdict = STOP if differences else REVIEW if unread else VERIFIED
    return {"verdict": verdict, "entries": len(expected), "fullyChecked": full, "differences": differences,
            "unread": unread, "hashedBytes": hashed_bytes, "fileBytes": file_bytes}


def print_verification(result: dict) -> None:
    print(f"System check: {result['verdict'].upper()}")
    print(f"  system.img sha256 {result['sha256']}: {result['entries']} entries under {SYSTEM_MOUNT}")
    if result.get("fingerprint") or result.get("unitFingerprint"):
        print(f"  Image fingerprint {result.get('fingerprint') or 'not found'}; the unit reports "
              f"{result.get('unitFingerprint') or 'nothing'}")
    if "fullyChecked" in result:
        # Rounded down, so that a share short of every byte never reads as 100%.
        share = math.floor(10000 * result["hashedBytes"] / result["fileBytes"]) / 100 if result["fileBytes"] else 100
        print(f"  Read in full and equal: {result['fullyChecked']} of {result['entries']} entries; file content "
              f"hashed on the unit and equal: {result['hashedBytes']} of {result['fileBytes']} bytes "
              f"({share:.2f}%)")
    for item in result.get("differences", [])[:40]:
        print(f"  Differs: {item['path']}: {item['what']}")
    if len(result.get("differences", [])) > 40:
        print(f"  ... and {len(result['differences']) - 40} more differences (--json lists them all)")
    for item in result.get("unread", [])[:60]:
        print(f"  Not read: {item['path']} ({item['label']}, {item['size']} bytes): {item['reason']}")
    if len(result.get("unread", [])) > 60:
        print(f"  ... and {len(result['unread']) - 60} more not read (--json lists them all)")
    for line in result["advice"]:
        print(f"  {line}")


VERIFY_ADVICE = {
    VERIFIED: ["Every entry of system.img is on the unit as written. Go on with the session."],
    REVIEW: ["Every entry adb's shell could read is as written. Those listed were not read in full: go on only "
             "if they are entries the board's own SELinux policy keeps from adb's shell (on the project's unit, "
             "daemons whose labels the factory policy grants no reader), not a folder or file Mirror Home or "
             "the supervisor needs."],
    STOP: ["Stop: the unit's /system differs from system.img, in two readings. Do not go on with the session: "
           "restart it into fastboot (`adb reboot bootloader`), run `install.py flash` again with the same "
           "folder (it writes system, then userdata), and verify again after the first boot."],
}


def verify(tools: identify.Tools, folder: pathlib.Path, serial: str | None) -> tuple[dict, int]:
    """The whole stage; the result and the exit code. Sends the unit only `adb shell` reads."""
    try:
        expected, image_hash, fingerprint = expected_system(folder)
    except (OSError, ValueError) as error:
        return {"verdict": "unverified", "error": f"system.img cannot be used: {error}"}, 1
    result = {"sha256": image_hash, "entries": len(expected), "fingerprint": fingerprint}
    try:
        state, found = find_one_unit(tools, serial)
    except RuntimeError as error:
        return {**result, "verdict": "unverified", "error": str(error)}, 1
    if state != "android" or not found:
        advice = {
            "fastboot": "The unit is in fastboot: `fastboot reboot`, wait for Android, and run this stage again.",
            "android-unauthorized": "ADB does not accept this computer's key: the key in userdata.img is not "
                                    "this computer's (%USERPROFILE%\\.android\\adbkey.pub), or userdata was not "
                                    "written as built. Build userdata.img with this computer's key and flash again.",
        }.get(state, "No unit runs Android with ADB: connect the cable and wait for the first boot to bring up ADB.")
        return {**result, "verdict": INCOMPLETE, "advice": [advice]}, EXIT_CODES[INCOMPLETE]
    try:
        compared = compare_system(expected, read_system_over_adb(tools, found))
        if compared["verdict"] == STOP:
            # This computer's memory has flipped bits before: a difference counts only if a second
            # reading of both sides finds exactly the same.
            again = compare_system(expected_system(folder)[0], read_system_over_adb(tools, found))
            if again["differences"] != compared["differences"]:
                raise RuntimeError("two readings found different differences, so either this computer or the unit "
                                   "did not read the same bytes twice; nothing is concluded. Run this stage again; "
                                   "if it happens again, test this computer's memory")
        code, out, _ = tools.adb("-s", found, "shell", "getprop ro.build.fingerprint")
        unit_fingerprint = safe(out.strip()) if code == 0 and out.strip() else None
    except (RuntimeError, OSError, ValueError) as error:
        return {**result, "verdict": "unverified", "error": str(error).replace(found, "<unit>")}, 1
    result.update(compared)
    result["unitFingerprint"] = unit_fingerprint
    result["advice"] = []
    if fingerprint and unit_fingerprint and unit_fingerprint != fingerprint:
        result["advice"].append("The unit reports another build fingerprint than system.img's build.prop: first "
                                "check that --images names the folder that was written to this unit.")
    result["advice"] += VERIFY_ADVICE[compared["verdict"]]
    return result, EXIT_CODES[compared["verdict"]]


def command_verify(arguments: argparse.Namespace) -> int:
    tools = identify.Tools(identify.find_tool("adb"), identify.find_tool("fastboot"), runner=verify_runner)
    result, code = verify(tools, pathlib.Path(arguments.images), arguments.serial)
    if arguments.json:
        print(json.dumps({"schema": "mirror-install-verify/1", **result}, indent=1))
    elif code == 1:
        print(f"Could not verify: {result['error']}", file=sys.stderr)
    else:
        print_verification(result)
    return code


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
    flash_stage = stages.add_parser("flash", help="CHANGES THE UNIT: checks the plan again, then, once a person "
                                                  "types the confirmation, writes system, then userdata, and "
                                                  "restarts the unit")
    flash_stage.add_argument("--images", required=True, metavar="FOLDER",
                             help="the folder `plan` answered ready for")
    flash_stage.add_argument("--serial", required=True, help="the unit, as `fastboot devices` lists it")
    flash_stage.set_defaults(run=command_flash)
    verify_stage = stages.add_parser("verify", help="reads only: after the first boot, compares the unit's "
                                                    "/system over ADB with system.img, entry by entry")
    verify_stage.add_argument("--images", required=True, metavar="FOLDER",
                              help="the folder `flash` wrote from (system.img and its .sha256 are read)")
    verify_stage.add_argument("--serial", help="the unit, when more than one is connected")
    verify_stage.add_argument("--json", action="store_true", help="print the result as JSON")
    verify_stage.set_defaults(run=command_verify)
    arguments = parser.parse_args(argv)
    return arguments.run(arguments)


if __name__ == "__main__":
    sys.exit(main())
