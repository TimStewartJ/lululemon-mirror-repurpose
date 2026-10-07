import pathlib
import sys
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "tests"))

import validate
from test_validate import FakeAdb, FakeApi, fake_time, frame, healthy_report
from validate import CheckFailed, Reply

NOON = 1_791_100_000_000


class WritingMirror:
    """Mirror Home as the journal check meets it: it writes a line when it
    starts and when it finds Wi-Fi, keeps what it wrote when it is stopped
    and started again, and copies Android's log when asked: its own lines
    until it has been given READ_LOGS and has started again, all of them after.

    ``faults`` name what a broken Mirror Home would do.
    """

    def __init__(self, *faults):
        self.faults = set(faults)
        self.run = 7
        self.granted = False
        self.reads_whole_log = False
        self.said = []
        self.copies = {}
        self.lines = []
        self.clock = NOON
        self.api = FakeApi()
        self.api.call = self.call
        self.adb = FakeAdb([frame(255)])
        self.adb.shell = self.shell
        self.start("update")

    def write(self, kind, what, more=None):
        self.clock += 1_000
        line = {"at": self.clock, "up": self.clock - NOON, "boot": "d517b087", "run": self.run, "kind": kind, "what": what}
        if more is not None:
            line["more"] = more
        self.lines.append(line)

    def start(self, previous_end):
        if "silent-start" not in self.faults:
            self.write("home", "started", {
                "version": "9.9.9" if "wrong-version" in self.faults else "2.3.0-dev.23",
                "code": 99,
                "previousEnd": "" if "forgets-the-end" in self.faults else previous_end,
                "deviceUpSeconds": 3600,
            })
        if "silent-wifi" not in self.faults:
            self.write("wifi", "connected", {"wifi": "on"})

    # -- what adb does ------------------------------------------------------

    def shell(self, *arguments, **_options):
        self.adb.commands.append(("shell", *arguments))
        if arguments == ("am", "force-stop", validate.PACKAGE):
            self.run += 1
            if "forgets-on-restart" in self.faults:
                self.lines = []
            self.reads_whole_log = self.granted and "stays-deaf" not in self.faults
            self.start("killed")
        elif arguments[:2] == ("pm", "grant"):
            assert arguments[2:] == (validate.PACKAGE, "android.permission.READ_LOGS"), arguments
            self.granted = True
        elif arguments[:3] == ("log", "-t", "wpa_supplicant"):
            self.said.append(arguments[3])
        return ""

    # -- what Mirror Home answers --------------------------------------------

    def keeper(self):
        return {
            "state": "rejoining" if "keeper-busy" in self.faults else "connected",
            "supported": "no-keeper" not in self.faults, "withoutNetworkSeconds": None, "since": None,
            "rejoins": 1 if "keeper-acts" in self.faults else 0, "wifiRestarts": 0, "outages": 0,
            "lastOutage": None, "lastAction": None, "looks": 3,
        }

    def summary(self):
        return {
            "lines": 0 if "counts-nothing" in self.faults else len(self.lines),
            "bytes": 120 * len(self.lines), "oldestAt": self.lines[0]["at"] if self.lines else None,
            "newestAt": self.lines[-1]["at"] if self.lines else None, "writtenThisRun": 2, "leftOut": 0,
            "failures": 0,
        }

    def copy(self, reason):
        self.clock += 1_000
        label = validate.re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")
        name = f"log-{self.clock}-{label}.txt"
        whole = self.reads_whole_log
        text = "# Mirror Home 2.3.0-dev.23 (99)\n"
        text += "# Android's whole log.\n" if whole else "# Only Mirror Home's own lines: it has not been given READ_LOGS.\n"
        if whole or "leaks" in self.faults:
            text += "".join(f"10-06 20:40:12.000  1234  1234 I wpa_supplicant: {word}\n" for word in self.said)
        if "bare-copy" in self.faults:
            text = text.split("\n", 1)[1]
        self.copies[name] = text
        described = {"name": name, "at": self.clock, "reason": label, "bytes": len(text)}
        if "unwritten-copy" not in self.faults:
            self.write("log", "copied", {**described, "whole": whole})
        return {**described, "whole": whole}

    def call(self, method, path, body=None, *, token=True, **_options):
        self.api.calls.append((method, path, body))
        if (method, path) == ("GET", "/api/v1/health"):
            report = healthy_report()
            report["appVersion"] = "2.3.0-dev.23"
            report["process"].update(runId=self.run, uptimeSeconds=3600)
            report["wifi"] = {"connected": True, "keeper": self.keeper()}
            report["journal"] = {**self.summary(), "logs": len(self.copies), "wholeLog": self.reads_whole_log}
            return Reply(200, report, {})
        if method == "GET" and path.split("?")[0] == validate.JOURNAL:
            if token is not True and "open-to-all" not in self.faults:
                return Reply(401, {"error": "Authentication required"}, {})
            query = dict(part.split("=") for part in path.partition("?")[2].split("&") if part)
            if "takes-any-query" not in self.faults:
                if query.get("limit") == "0" or not query.get("since", "0").isdigit():
                    return Reply(400, {"error": "since and limit must be whole numbers"}, {})
            since = 0 if "ignores-since" in self.faults else int(query.get("since", "0") or 0)
            lines = [
                line for line in self.lines
                if line["at"] >= since and query.get("kind") in (None, line["kind"])
            ]
            logs = [] if "lists-no-copies" in self.faults else [
                {"name": name, "bytes": len(text)} for name, text in self.copies.items()
            ]
            return Reply(200, {**self.summary(), "events": lines, "logs": logs, "wholeLog": self.reads_whole_log}, {})
        if (method, path) == ("POST", validate.JOURNAL + "/logs"):
            if not isinstance(body.get("reason"), str) and "takes-any-reason" not in self.faults:
                return Reply(400, {"error": "reason must be text"}, {})
            return Reply(201, self.copy(str(body.get("reason"))), {})
        if method == "GET" and path.startswith(validate.JOURNAL + "/logs/"):
            name = path.rsplit("/", 1)[1]
            if name in self.copies:
                return Reply(200, self.copies[name].encode("utf-8"), {"content-type": "text/plain; charset=utf-8"})
            if "gives-any-file" in self.faults:
                return Reply(200, b"{}", {"content-type": "text/plain; charset=utf-8"})
            return Reply(404, {"error": "There is no such copy of the log"}, {})
        raise AssertionError(f"Unexpected {method} {path}")

    def context(self, directory):
        ctx = validate.Context(self.adb, self.api, pathlib.Path(directory))
        ctx.awake_peak = 255
        # The dashboard is not this check's subject.
        ctx.wait_dashboard = lambda *_arguments, **_options: {}
        ctx.wait_lit = lambda *_arguments, **_options: None
        return ctx


class JournalCheckTest(unittest.TestCase):
    def run_check(self, *faults):
        fake_time(self)
        mirror = WritingMirror(*faults)
        with tempfile.TemporaryDirectory() as directory:
            ctx = mirror.context(directory)
            validate.check_journal(ctx)
        return mirror, ctx

    def test_a_mirror_home_that_writes_and_keeps_what_it_wrote_passes(self):
        mirror, ctx = self.run_check()
        self.assertEqual("connected", ctx.details["wifiKeeper"])
        self.assertEqual(2, ctx.details["journal"]["logs"])
        self.assertTrue(ctx.details["journal"]["wholeLog"])
        # Stopped twice: once to see that the journal lasts, once for READ_LOGS to take effect.
        self.assertEqual(9, mirror.run)
        self.assertEqual(
            2, sum(command == ("shell", "am", "force-stop", validate.PACKAGE) for command in mirror.adb.commands)
        )
        self.assertIn(
            ("shell", "pm", "grant", validate.PACKAGE, "android.permission.READ_LOGS"), mirror.adb.commands
        )

    def test_each_fault_is_named(self):
        faults = {
            "open-to-all": "The journal is given to whoever asks",
            "no-keeper": "the Wi-Fi keeper reports",
            "keeper-busy": "the Wi-Fi keeper reports",
            "keeper-acts": "The Wi-Fi keeper did something where nothing was lost",
            "silent-start": "Mirror Home did not write that run 7 started",
            "wrong-version": "The line for the start of run 7 reads",
            "silent-wifi": "Nothing was written of how Mirror Home found Wi-Fi when it started",
            "ignores-since": "Asked for what happened since, the journal gave what happened before",
            "takes-any-query": "No lines asked of the journal answered 200, expected 400",
            "forgets-on-restart": "After Mirror Home started again, the line of the run before reads null",
            "forgets-the-end": "The new run does not say how the one before ended",
            "takes-any-reason": "A reason that is no text answered 201, expected 400",
            "bare-copy": "The copy of the log begins",
            "leaks": "Without READ_LOGS, a copy of the log holds another program's lines",
            "gives-any-file": "The copy of the log 'log-1-nothing.txt' answered 200, expected 404",
            "stays-deaf": "Given READ_LOGS and started again, Mirror Home still reads only its own lines",
            "lists-no-copies": "The journal lists the copies of the log as",
            "unwritten-copy": "That a copy of the log was made is not written in the journal",
            "counts-nothing": "The health report says of the journal",
        }
        for fault, message in faults.items():
            with self.subTest(fault=fault):
                with self.assertRaisesRegex(CheckFailed, message):
                    self.run_check(fault)

    def test_what_the_wifi_said_is_looked_for_in_the_copy(self):
        fake_time(self)
        mirror = WritingMirror()
        with tempfile.TemporaryDirectory() as directory:
            validate.check_journal(mirror.context(directory))
        quiet, said = mirror.said
        first, second = mirror.copies.values()
        self.assertNotIn(quiet, first)
        self.assertIn(said, second)


class LiveMirrorJournalTest(unittest.TestCase):
    """What the read-only check of a real Mirror says of the journal and the keeper."""

    KEEPER = {
        "state": "connected", "supported": True, "withoutNetworkSeconds": None, "since": None, "rejoins": 0,
        "wifiRestarts": 0, "outages": 1, "looks": 40, "lastAction": None,
        "lastOutage": {"lostAt": NOON, "backAt": NOON + 190_000, "seconds": 190, "rejoins": 1, "wifiRestarts": 0},
    }
    JOURNAL = {
        "lines": 212, "bytes": 31_000, "oldestAt": NOON - 86_400_000, "newestAt": NOON, "writtenThisRun": 9,
        "leftOut": 0, "failures": 0, "logs": 3, "wholeLog": True,
    }

    def check(self, change=None):
        report = healthy_report()
        report["wifi"] = {"connected": True, "keeper": dict(self.KEEPER)}
        report["journal"] = dict(self.JOURNAL)
        if change:
            change(report)
        ctx = validate.MirrorContext(FakeApi({("GET", "/api/v1/health"): Reply(200, report, {})}))
        validate.mirror_health(ctx)
        return ctx

    def test_an_outage_that_ended_is_shown_and_is_no_fault(self):
        ctx = self.check()
        self.assertEqual(190, ctx.details["wifiKeeper"]["lastOutage"]["seconds"])
        self.assertEqual(212, ctx.details["journal"]["lines"])

    def test_a_mirror_home_from_before_the_journal_is_read(self):
        def earlier(report):
            del report["wifi"]["keeper"]
            del report["journal"]

        ctx = self.check(earlier)
        self.assertNotIn("journal", ctx.details)
        self.assertNotIn("wifiKeeper", ctx.details)

    def test_a_journal_that_cannot_be_written_is_a_fault(self):
        with self.assertRaisesRegex(CheckFailed, "the journal could not be written 4 times in this run"):
            self.check(lambda report: report["journal"].update(failures=4))


if __name__ == "__main__":
    unittest.main()
