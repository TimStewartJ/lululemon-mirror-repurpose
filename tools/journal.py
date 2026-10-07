#!/usr/bin/env python3
"""Read what a Mirror wrote down: its journal and its copies of Android's log.

Mirror Home keeps a journal on the Mirror's own storage, which a restart
does not erase: when it started and how the run before ended, when Wi-Fi was
lost and came back, what Android said of the network in between, and what
Mirror Home did about it. At the moment of a loss it also copies Android's
log. This reads both over the network, afterwards.

    python tools/journal.py                    what happened in the last two days
    python tools/journal.py --since 6h --kind wifi
    python tools/journal.py logs               the copies of Android's log
    python tools/journal.py save build/journal the journal and every copy, as files
    python tools/journal.py copy "before I restart it"
                                               copy Android's log now

It uses the credential that ``tools/background-video.ps1 pair`` saves. A
Mirror that has lost its network can still be read over USB: ``adb forward
tcp:18787 tcp:8787``, then ``--host 127.0.0.1 --port 18787``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys
import time
import urllib.parse

import validate

SPANS = {"m": 60, "h": 3600, "d": 86400}


def span_seconds(text: str) -> int:
    """How many seconds "30m", "6h" or "2d" are."""
    match = re.fullmatch(r"(\d+)([mhd])", text.strip().lower())
    if not match:
        raise argparse.ArgumentTypeError(f"{text!r} is not a span such as 30m, 6h or 2d")
    return int(match.group(1)) * SPANS[match.group(2)]


def brief(value: object) -> str:
    """A value of a line's details, short enough to stand in a row."""
    if isinstance(value, dict):
        return "{" + " ".join(f"{key}={brief(inner)}" for key, inner in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(brief(inner) for inner in value) + "]"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "-" if value is None else str(value)


def render(line: dict) -> str:
    """One line of the journal as a row: when, in which run, about what, and its details."""
    when = datetime.datetime.fromtimestamp(line["at"] / 1000).strftime("%m-%d %H:%M:%S")
    details = "  ".join(f"{key}={brief(value)}" for key, value in (line.get("more") or {}).items())
    return f"{when}  run {line.get('run', '?'):<4} {line.get('kind', ''):<5} {line.get('what', ''):<22} {details}".rstrip()


def render_all(lines: list[dict]) -> list[str]:
    """The rows, with a rule wherever Android itself started anew."""
    rows = []
    boot = None
    for line in lines:
        if boot is not None and line.get("boot") != boot:
            rows.append("-" * 20 + " the Mirror was restarted " + "-" * 20)
        boot = line.get("boot")
        rows.append(render(line))
    return rows


def read(api: validate.Api, since: int, kind: str, limit: int) -> dict:
    query = urllib.parse.urlencode({"since": since, "kind": kind, "limit": limit})
    reply = api.call("GET", f"{validate.JOURNAL}?{query}")
    if reply.status == 404:
        raise SystemExit("This Mirror Home keeps no journal; it is older than 2.3.0.")
    if reply.status != 200:
        raise SystemExit(f"The Mirror answered {reply.status}: {validate.describe(reply.body)}")
    return reply.body


def copy_text(api: validate.Api, name: str) -> bytes:
    reply = api.call("GET", f"{validate.JOURNAL}/logs/{urllib.parse.quote(name)}")
    if reply.status != 200:
        raise SystemExit(f"The copy {name} answered {reply.status}: {validate.describe(reply.body)}")
    return reply.body


def save(api: validate.Api, journal: dict, directory: pathlib.Path) -> list[pathlib.Path]:
    """Writes the journal and every copy of the log into a directory; what it wrote."""
    directory.mkdir(parents=True, exist_ok=True)
    written = [directory / "journal.json", directory / "journal.txt"]
    written[0].write_text(json.dumps(journal, indent=1), encoding="utf-8")
    written[1].write_text("\n".join(render_all(journal["events"])) + "\n", encoding="utf-8")
    for copy in journal["logs"]:
        path = directory / copy["name"]
        path.write_bytes(copy_text(api, copy["name"]))
        written.append(path)
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=pathlib.Path, default=validate.DEFAULT_MIRROR_CONFIG, help="JSON file with host, port and token")
    parser.add_argument("--host", help="another address than the file's, such as 127.0.0.1 over USB")
    parser.add_argument("--port", type=int, help="another port than the file's")
    parser.add_argument("--since", type=span_seconds, default=span_seconds("2d"), help="how far back: 30m, 6h, 2d (default 2d)")
    parser.add_argument("--all", action="store_true", help="everything the journal holds")
    parser.add_argument("--kind", default="", help="only lines of this kind: home, wifi, log")
    parser.add_argument("--limit", type=int, default=5000, help="at most this many lines, the newest")
    parser.add_argument("--json", action="store_true", help="print what the Mirror answered")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("logs", help="list the copies of Android's log")
    saving = commands.add_parser("save", help="write the journal and every copy into a directory")
    saving.add_argument("directory", type=pathlib.Path)
    copying = commands.add_parser("copy", help="copy Android's log now")
    copying.add_argument("reason", nargs="?", default="asked")
    return parser


def main(arguments: list[str] | None = None) -> int:
    options = build_parser().parse_args(arguments)
    try:
        config = validate.load_mirror_config(options.config)
    except validate.CheckFailed as error:
        raise SystemExit(str(error)) from error
    api = validate.Api(
        options.host or config["host"],
        options.port or int(config.get("port", validate.DEVICE_PORT)),
        config["token"],
    )
    if options.command == "copy":
        reply = api.call("POST", validate.JOURNAL + "/logs", {"reason": options.reason})
        if reply.status != 201:
            raise SystemExit(f"The Mirror answered {reply.status}: {validate.describe(reply.body)}")
        print(f"{reply.body['name']}  {reply.body['bytes']} bytes")
        if not reply.body["whole"]:
            print("It holds only Mirror Home's own lines: Mirror Home has not been given READ_LOGS (docs/user-guide.md).")
        return 0
    since = 0 if options.all or options.command == "save" else int((time.time() - options.since) * 1000)
    journal = read(api, since, options.kind, options.limit)
    if options.command == "save":
        for path in save(api, journal, options.directory):
            print(path)
        return 0
    if options.json:
        print(json.dumps(journal, indent=1))
        return 0
    if options.command == "logs":
        for copy in journal["logs"]:
            when = datetime.datetime.fromtimestamp(copy["at"] / 1000).strftime("%m-%d %H:%M:%S")
            print(f"{when}  {copy['name']}  {copy['bytes']} bytes")
        if not journal["wholeLog"]:
            print("New copies hold only Mirror Home's own lines: it has not been given READ_LOGS (docs/user-guide.md).")
        return 0
    for row in render_all(journal["events"]):
        print(row)
    reach = "" if not journal.get("oldestAt") else (
        ", back to " + datetime.datetime.fromtimestamp(journal["oldestAt"] / 1000).strftime("%Y-%m-%d %H:%M")
    )
    print(f"\n{len(journal['events'])} of {journal['lines']} lines{reach}; {len(journal['logs'])} copies of Android's log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
