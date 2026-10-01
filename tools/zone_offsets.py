#!/usr/bin/env python3
"""Generate Mirror Home's bundled table of upcoming UTC-offset changes.

The MIRROR firmware has no usable time-zone rules, so Mirror Home cannot ask
Android when daylight saving starts or ends. This tool writes the offset each
IANA zone uses now and every change over the following years to
``android/mirror-home/src/main/assets/zone-offsets.json``. Mirror Home follows
that table whenever a paired browser has not supplied fresher changes.

Regenerate before a release with the newest IANA data available:

    python -m pip install --upgrade tzdata
    python tools/zone_offsets.py

``--check`` recomputes the committed window and reports any zone whose changes
differ, which means newer rules were published since the table was written.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
from typing import Callable, Iterable


REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPO / "android" / "mirror-home" / "src" / "main" / "assets" / "zone-offsets.json"
)
FORMAT_VERSION = 1
HORIZON_DAYS = 3653
MINUTES_PER_DAY = 24 * 60
MAX_OFFSET_MINUTES = 14 * 60

OffsetReader = Callable[[int], int]


class ZoneOffsetError(RuntimeError):
    pass


def changes(
    offset_at: OffsetReader,
    start_minute: int,
    end_minute: int,
    step_minutes: int = MINUTES_PER_DAY,
) -> list[list[int]]:
    """Offset changes after ``start_minute`` up to ``end_minute`` inclusive.

    Times are whole minutes since the Unix epoch. Each entry is the first
    minute at which a new offset applies and that offset, in minutes east of
    UTC. Two changes closer together than ``step_minutes`` may be missed.
    """
    if step_minutes < 1:
        raise ValueError("step_minutes must be positive")
    found: list[list[int]] = []
    cursor = start_minute
    current = offset_at(cursor)
    while cursor < end_minute:
        probe = min(cursor + step_minutes, end_minute)
        if offset_at(probe) == current:
            cursor = probe
            continue
        low, high = cursor, probe
        while high - low > 1:
            middle = (low + high) // 2
            if offset_at(middle) == current:
                low = middle
            else:
                high = middle
        current = offset_at(high)
        found.append([high, current])
        cursor = high
    return found


def zone_reader(name: str) -> OffsetReader:
    import zoneinfo

    zone = zoneinfo.ZoneInfo(name)

    def offset_at(minute: int) -> int:
        moment = datetime.datetime.fromtimestamp(minute * 60, zone)
        seconds = moment.utcoffset().total_seconds()
        if seconds % 60:
            raise ZoneOffsetError(f"{name} uses an offset that is not a whole minute")
        return int(seconds // 60)

    return offset_at


def build_table(
    names: Iterable[str],
    start_minute: int,
    end_minute: int,
    reader: Callable[[str], OffsetReader] = zone_reader,
) -> dict:
    """Zones share one rule entry whenever their offsets and changes agree."""
    rules: list[list] = []
    rule_index: dict[str, int] = {}
    zones: dict[str, int] = {}
    for name in sorted(names):
        offset_at = reader(name)
        base = offset_at(start_minute)
        rule = [base, changes(offset_at, start_minute, end_minute)]
        for offset in [base, *(change[1] for change in rule[1])]:
            if abs(offset) > MAX_OFFSET_MINUTES:
                raise ZoneOffsetError(f"{name} uses an out-of-range offset: {offset}")
        key = json.dumps(rule, separators=(",", ":"))
        if key not in rule_index:
            rule_index[key] = len(rules)
            rules.append(rule)
        zones[name] = rule_index[key]
    return {"rules": rules, "zones": zones}


def render(table: dict, *, tzdata: str, start_minute: int, end_minute: int) -> str:
    """One rule and one zone per line keeps regenerated diffs reviewable."""
    lines = [
        "{",
        f'"version": {FORMAT_VERSION},',
        f'"tzdata": {json.dumps(tzdata)},',
        f'"from": {start_minute},',
        f'"until": {end_minute},',
        '"rules": [',
    ]
    rules = table["rules"]
    for index, rule in enumerate(rules):
        suffix = "," if index < len(rules) - 1 else ""
        lines.append(json.dumps(rule, separators=(",", ":")) + suffix)
    lines.append("],")
    lines.append('"zones": {')
    names = sorted(table["zones"])
    for index, name in enumerate(names):
        suffix = "," if index < len(names) - 1 else ""
        lines.append(f"{json.dumps(name)}: {table['zones'][name]}{suffix}")
    lines.append("}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def tzdata_version() -> str:
    try:
        import tzdata
    except ImportError:
        return "system"
    return str(getattr(tzdata, "IANA_VERSION", "unknown"))


def available_zones() -> list[str]:
    try:
        import zoneinfo

        names = sorted(zoneinfo.available_timezones())
    except ImportError as error:
        raise ZoneOffsetError("Python 3.9 or newer is required") from error
    if not names:
        raise ZoneOffsetError(
            "No IANA time-zone data is available; run: python -m pip install tzdata"
        )
    return names


def today_minute() -> int:
    today = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(today.timestamp() // 60)


def differing_zones(committed: dict, fresh: dict) -> list[str]:
    different = []
    for name in sorted(set(committed["zones"]) | set(fresh["zones"])):
        old = committed["zones"].get(name)
        new = fresh["zones"].get(name)
        if old is None or new is None:
            different.append(name)
        elif committed["rules"][old] != fresh["rules"][new]:
            different.append(name)
    return different


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the committed table with the installed IANA data; write nothing",
    )
    options = parser.parse_args(arguments)
    try:
        names = available_zones()
        if options.check:
            committed = json.loads(options.output.read_text(encoding="utf-8"))
            fresh = build_table(names, committed["from"], committed["until"])
            different = differing_zones(committed, fresh)
            if different:
                print(
                    f"{len(different)} zone(s) differ from IANA {tzdata_version()}: "
                    + ", ".join(different[:12])
                    + (" ..." if len(different) > 12 else "")
                )
                return 1
            print(
                f"{len(committed['zones'])} zones match IANA {tzdata_version()} "
                f"(table written from {committed['tzdata']})"
            )
            return 0
        start = today_minute()
        end = start + HORIZON_DAYS * MINUTES_PER_DAY
        table = build_table(names, start, end)
        options.output.write_text(
            render(table, tzdata=tzdata_version(), start_minute=start, end_minute=end),
            encoding="utf-8",
            newline="\n",
        )
        print(
            f"Wrote {len(table['zones'])} zones sharing {len(table['rules'])} rules "
            f"from IANA {tzdata_version()} to {options.output}"
        )
        return 0
    except (OSError, ValueError, KeyError, ZoneOffsetError) as error:
        print(f"zone_offsets: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
