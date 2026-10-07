import argparse
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import journal
import validate
from validate import Reply

NOON = 1_791_100_000_000


def line(offset, kind, what, more=None, *, boot="d517b087", run=61):
    entry = {"at": NOON + offset, "up": 1_000 + offset, "boot": boot, "run": run, "kind": kind, "what": what}
    if more is not None:
        entry["more"] = more
    return entry


class Mirror:
    """The three calls of a Mirror that keeps a journal."""

    def __init__(self):
        self.lines = [
            line(0, "wifi", "lost", {"doing": "SCANNING", "display": "off", "before": {"rssi": -45, "secondsAgo": 12}}),
            line(60_000, "wifi", "rejoin", {"attempt": 1, "inRange": {"seen": True, "frequenciesMhz": [2437, 5220]}, "held": None}),
            line(600_000, "home", "started", {"version": "2.3.0", "previousEnd": "reboot"}, boot="0a1b2c3d", run=62),
        ]
        self.copies = {"log-1791100000000-wifi-lost.txt": b"# Mirror Home 2.3.0 (99)\nwpa_supplicant: reason=3\n"}
        self.whole = True
        self.calls = []

    def call(self, method, path, body=None, **_options):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith(validate.JOURNAL + "?"):
            return Reply(200, {
                "lines": len(self.lines), "bytes": 400, "oldestAt": NOON, "newestAt": NOON + 600_000,
                "events": self.lines, "wholeLog": self.whole,
                "logs": [{"name": name, "at": NOON, "reason": "wifi-lost", "bytes": len(text)}
                         for name, text in self.copies.items()],
            }, {})
        if method == "GET" and path.startswith(validate.JOURNAL + "/logs/"):
            name = path.rsplit("/", 1)[1]
            if name in self.copies:
                return Reply(200, self.copies[name], {"content-type": "text/plain; charset=utf-8"})
            return Reply(404, {"error": "There is no such copy of the log"}, {})
        if (method, path) == ("POST", validate.JOURNAL + "/logs"):
            return Reply(201, {"name": "log-1791100600000-before-i-restart-it.txt", "bytes": 512, "whole": self.whole}, {})
        raise AssertionError(f"Unexpected {method} {path}")


class JournalToolTest(unittest.TestCase):
    def run_tool(self, *arguments, mirror=None):
        mirror = mirror or Mirror()
        printed = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            config = pathlib.Path(directory) / "mirror.json"
            config.write_text(json.dumps({"host": "10.0.0.196", "port": 8787, "token": "credential"}), encoding="utf-8")
            with mock.patch.object(validate, "Api", lambda host, port, token: self.remember(mirror, host, port)):
                with contextlib.redirect_stdout(printed):
                    code = journal.main(["--config", str(config), *arguments])
        self.assertEqual(0, code)
        return mirror, printed.getvalue()

    def remember(self, mirror, host, port):
        self.address = (host, port)
        return mirror

    def test_a_span_is_minutes_hours_or_days(self):
        self.assertEqual(1_800, journal.span_seconds("30m"))
        self.assertEqual(21_600, journal.span_seconds("6h"))
        self.assertEqual(172_800, journal.span_seconds("2D"))
        for wrong in ("", "2", "2w", "h", "-1h"):
            with self.assertRaises(argparse.ArgumentTypeError):
                journal.span_seconds(wrong)

    def test_a_line_is_a_row_with_its_details_flattened(self):
        row = journal.render(Mirror().lines[1])
        self.assertRegex(row, r"^\d\d-\d\d \d\d:\d\d:\d\d  run 61   wifi  rejoin +attempt=1  ")
        self.assertIn("inRange={seen=yes frequenciesMhz=[2437,5220]}", row)
        self.assertTrue(row.endswith("held=-"))
        self.assertNotIn("\n", row)

    def test_the_rows_show_where_the_mirror_was_restarted(self):
        mirror, printed = self.run_tool("--all")
        rows = printed.splitlines()
        self.assertIn("lost", rows[0])
        self.assertIn("the Mirror was restarted", rows[2])
        self.assertIn("started", rows[3])
        self.assertIn("3 of 3 lines, back to ", rows[-1])
        self.assertIn("1 copies of Android's log", rows[-1])
        self.assertEqual(("10.0.0.196", 8787), self.address)
        self.assertIn("since=0&", mirror.calls[0][1])

    def test_it_asks_for_two_days_unless_told_otherwise_and_passes_the_kind_on(self):
        with mock.patch.object(journal.time, "time", lambda: 2_000_000.0):
            mirror, _ = self.run_tool("--kind", "wifi")
            self.assertIn(f"since={(2_000_000 - 172_800) * 1000}&kind=wifi&limit=5000", mirror.calls[0][1])
            mirror, _ = self.run_tool("--since", "6h", "--limit", "20")
            self.assertIn(f"since={(2_000_000 - 21_600) * 1000}&kind=&limit=20", mirror.calls[0][1])

    def test_a_mirror_without_its_network_is_read_over_usb(self):
        self.run_tool("--host", "127.0.0.1", "--port", "18787")
        self.assertEqual(("127.0.0.1", 18787), self.address)

    def test_the_copies_of_the_log_are_listed_and_said_to_be_partial_without_the_permission(self):
        _, printed = self.run_tool("logs")
        self.assertIn("log-1791100000000-wifi-lost.txt  50 bytes", printed)
        self.assertNotIn("READ_LOGS", printed)
        mirror = Mirror()
        mirror.whole = False
        _, printed = self.run_tool("logs", mirror=mirror)
        self.assertIn("READ_LOGS", printed)

    def test_saving_writes_the_whole_journal_and_every_copy(self):
        mirror = Mirror()
        with tempfile.TemporaryDirectory() as directory:
            target = pathlib.Path(directory) / "kept"
            _, printed = self.run_tool("save", str(target), mirror=mirror)
            self.assertEqual(
                ["journal.json", "journal.txt", "log-1791100000000-wifi-lost.txt"],
                sorted(path.name for path in target.iterdir()),
            )
            self.assertEqual(mirror.lines, json.loads((target / "journal.json").read_text(encoding="utf-8"))["events"])
            self.assertIn("the Mirror was restarted", (target / "journal.txt").read_text(encoding="utf-8"))
            self.assertEqual(mirror.copies["log-1791100000000-wifi-lost.txt"], (target / "log-1791100000000-wifi-lost.txt").read_bytes())
        self.assertEqual(3, len(printed.splitlines()))
        self.assertIn("since=0&", mirror.calls[0][1])

    def test_a_copy_is_asked_for_with_its_reason(self):
        mirror, printed = self.run_tool("copy", "before I restart it")
        self.assertEqual(("POST", validate.JOURNAL + "/logs", {"reason": "before I restart it"}), mirror.calls[0])
        self.assertIn("log-1791100600000-before-i-restart-it.txt  512 bytes", printed)

    def test_raw_json_is_what_the_mirror_answered(self):
        mirror, printed = self.run_tool("--json", "--all")
        self.assertEqual(mirror.lines, json.loads(printed)["events"])

    def test_an_older_mirror_home_is_named_as_such(self):
        mirror = Mirror()
        mirror.call = lambda *_arguments, **_options: Reply(404, {"error": "Not found"}, {})
        with self.assertRaisesRegex(SystemExit, "keeps no journal"):
            self.run_tool(mirror=mirror)


if __name__ == "__main__":
    unittest.main()
