import argparse
import contextlib
import hashlib
import http.server
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import screen_capture
import validate
from validate import Api, CheckFailed, CheckSkipped, Reply


MIRROR_FINGERPRINT = "mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/329:user/release-keys"
EMULATOR_FINGERPRINT = "Android/sdk_phone_x86_64/generic_x86_64:6.0/MASTER/4174734:userdebug/test-keys"
# 2026-10-01T12:00:00Z, a month before Los Angeles leaves daylight time.
OCTOBER_FIRST = 1_790_856_000_000
# 2026-11-01T09:00:00Z, when it does.
NOVEMBER_FIRST_CHANGE = 1_793_523_600_000


def healthy_report():
    return {
        "now": OCTOBER_FIRST,
        "debuggable": True,
        "process": {
            "pid": 2604, "runId": 7, "uptimeSeconds": 7200, "earlyStops": 0,
            "previousRun": {"runId": 6, "end": "update"},
        },
        "crashes": {"count": 0, "last": None},
        "memory": {
            "pssKb": 180_000, "javaHeapUsedKb": 9_000, "javaHeapMaxKb": 131_072, "nativeHeapKb": 60_000,
            "openFiles": 140, "threads": 40, "systemAvailableKb": 900_000, "trimEvents": 0,
            "systemLow": False,
        },
        "storage": {"dataFreeBytes": 4 << 30},
        "device": {
            "display": {"densityDpi": 240, "on": True}, "webView": {"versionName": "44.0.2403.119"},
            "bootId": "6f0f6f6e-boot", "uptimeSeconds": 518_400,
            "input": {"touchscreen": False, "keyboard": False, "navigation": False},
            "power": {
                "interactive": True, "keyguardLocked": False, "screenOffTimeoutSeconds": 600,
                "stayOnWhilePluggedIn": 1, "plugged": 0, "usbConnected": False, "usbConfigured": False,
            },
        },
        "activity": {
            "showing": True, "resumed": True, "creates": 1, "pauses": 0,
            "pausedForSeconds": 0, "unfocusedForSeconds": 0,
            "selectedHome": True, "front": "dev.mirror.repurpose/.MainActivity",
            "recovery": {
                "attended": False, "wakeUps": 0, "relaunches": 0,
                "lastAt": None, "lastReason": "", "lastFront": "",
            },
        },
        "dashboard": {
            "consoleErrors": 0, "recentConsoleErrors": [], "phase": "page-finished",
            "url": "http://127.0.0.1:8787/dashboard/custom.html",
            "pageComplete": True, "rendererPresent": True,
        },
        "api": {"unhandledErrors": 0},
        "clock": {"source": "bundled", "nextChange": {"at": NOVEMBER_FIRST_CHANGE, "utcOffsetMinutes": -480}},
        "pairing": {"open": False, "lockedForSeconds": 0, "wrongCodes": 0},
        "otaSupervisor": {"installed": True, "listening": True},
    }


def healthy_status():
    return {
        "deviceUptimeSeconds": 518_400,
        "wifi": {"connected": True, "ssid": "home"},
        "timeZone": "America/Los_Angeles",
        "utcOffsetMinutes": -420,
        "nextUtcOffsetChange": {"at": NOVEMBER_FIRST_CHANGE, "utcOffsetMinutes": -480},
        "automation": {"sleeping": False, "sleepReason": "", "motionEnabled": True, "motion": {"monitoring": True, "state": "watching"}},
        "ambientVideo": {"state": "playing", "droppedFramePercent": 0.01, "error": ""},
        "weather": {"stale": False},
    }


class FakeApi:
    """Answers by method and path; anything unscripted is a test error."""

    def __init__(self, replies=None):
        self.replies = dict(replies or {})
        self.calls = []
        self.token = "credential"
        self.port = 0

    def call(self, method, path, body=None, **_options):
        self.calls.append((method, path, body))
        reply = self.replies[(method, path)]
        if callable(reply):
            reply = reply(body)
        return reply if isinstance(reply, Reply) else Reply(200, reply, {})

    def expect(self, method, path, body=None, *, status=200, **options):
        reply = self.call(method, path, body, **options)
        if reply.status != status:
            raise CheckFailed(f"{method} {path} answered {reply.status}, expected {status}")
        return reply.body


def frame(peak, *, fill=0):
    """A small frame: ``fill`` everywhere, one pixel at ``peak``."""
    pixels = bytearray(bytes([fill, fill, fill, 255]) * 16)
    pixels[0:4] = bytes([peak, peak, peak, 255])
    return screen_capture.Screenshot(4, 4, bytes(pixels))


class FakeAdb:
    serial = "emulator-5580"
    executable = pathlib.Path("adb")

    def __init__(self, frames=()):
        self.frames = list(frames)
        self.commands = []

    def capture(self):
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]

    def shell(self, *arguments, **_options):
        self.commands.append(("shell", *arguments))
        return ""

    def answers(self, timeout=20.0):
        return True


class FakeClock:
    """Time that moves a second whenever it is read, so loops end at once."""

    def __init__(self):
        self.now = 1_000.0

    def monotonic(self):
        self.now += 1.0
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def fake_time(test):
    clock = FakeClock()
    for name in ("monotonic", "sleep"):
        patcher = mock.patch.object(validate.time, name, getattr(clock, name))
        patcher.start()
        test.addCleanup(patcher.stop)
    return clock


class ClockTextTest(unittest.TestCase):
    def test_twelve_hour_clock_matches_the_dashboard(self):
        midnight_utc = 1_790_812_800_000  # 2026-10-01T00:00:00Z
        self.assertEqual("12:00 AM", validate.clock_text(midnight_utc, 0, False))
        self.assertEqual("5:00 PM", validate.clock_text(midnight_utc, -420, False))
        self.assertEqual("12:00 PM", validate.clock_text(midnight_utc, 720, False))
        self.assertEqual("5:45 AM", validate.clock_text(midnight_utc, 345, False))
        self.assertEqual("12:59 PM", validate.clock_text(midnight_utc + 59 * 60_000, 720, False))

    def test_twenty_four_hour_clock_pads_the_hour(self):
        midnight_utc = 1_790_812_800_000
        self.assertEqual("00:00", validate.clock_text(midnight_utc, 0, True))
        self.assertEqual("17:00", validate.clock_text(midnight_utc, -420, True))
        self.assertEqual("05:45", validate.clock_text(midnight_utc, 345, True))

    def test_seconds_do_not_round_the_minute_up(self):
        self.assertEqual("00:00", validate.clock_text(1_790_812_800_000 + 59_999, 0, True))


class ZoneTimelineTest(unittest.TestCase):
    TABLE = {
        "zones": {"Test/Zone": 1, "Etc/UTC": 0},
        "rules": [[0, []], [-420, [[100, -480], [200, -420], [300, -480]]]],
    }

    def test_before_the_first_change_the_base_offset_applies(self):
        self.assertEqual(
            (-420, [
                {"at": 6_000_000, "utcOffsetMinutes": -480},
                {"at": 12_000_000, "utcOffsetMinutes": -420},
                {"at": 18_000_000, "utcOffsetMinutes": -480},
            ]),
            validate.zone_timeline(self.TABLE, "Test/Zone", 5_999_999),
        )

    def test_a_change_applies_from_its_first_millisecond(self):
        offset, upcoming = validate.zone_timeline(self.TABLE, "Test/Zone", 6_000_000)
        self.assertEqual(-480, offset)
        self.assertEqual([12_000_000, 18_000_000], [change["at"] for change in upcoming])

    def test_after_the_last_change_nothing_is_upcoming(self):
        self.assertEqual((-480, []), validate.zone_timeline(self.TABLE, "Test/Zone", 99_000_000))

    def test_a_zone_without_changes(self):
        self.assertEqual((0, []), validate.zone_timeline(self.TABLE, "Etc/UTC", 6_000_000))

    def test_reads_the_bundled_table_whenever_it_was_generated(self):
        table = json.loads(validate.ZONE_TABLE.read_text(encoding="utf-8"))
        start = table["from"] * 60_000
        offset, upcoming = validate.zone_timeline(table, "America/Los_Angeles", start)
        self.assertIn(offset, (-420, -480))
        self.assertGreaterEqual(len(upcoming), 8)
        previous = offset
        for earlier, later in zip([{"at": start}] + upcoming, upcoming):
            self.assertGreater(later["at"], earlier["at"])
            self.assertNotEqual(previous, later["utcOffsetMinutes"])
            previous = later["utcOffsetMinutes"]
        # Read from just after its first change, that change is no longer upcoming.
        offset, later = validate.zone_timeline(table, "America/Los_Angeles", upcoming[0]["at"])
        self.assertEqual(upcoming[0]["utcOffsetMinutes"], offset)
        self.assertEqual(upcoming[1:], later)


class NoonAndMidnightTest(unittest.TestCase):
    def test_offsets_put_the_local_hour_at_noon_and_midnight_all_day(self):
        for hour in range(24):
            now = 1_790_812_800_000 + hour * 3_600_000 + 17 * 60_000
            noon, midnight = validate.noon_and_midnight_offsets(now)
            self.assertEqual("12:17", validate.clock_text(now, noon, True), hour)
            self.assertEqual("00:17", validate.clock_text(now, midnight, True), hour)
            # Both must be offsets the control API accepts.
            for offset in (noon, midnight):
                self.assertEqual(0, offset % 60)
                self.assertTrue(-840 <= offset <= 840, (hour, offset))


class WaitForTest(unittest.TestCase):
    def test_returns_the_first_truthy_value(self):
        fake_time(self)
        seen = iter([None, 0, "", {"ready": True}])
        self.assertEqual({"ready": True}, validate.wait_for("readiness", lambda: next(seen), timeout=30))

    def test_keeps_trying_while_the_device_is_unreachable(self):
        fake_time(self)
        attempts = []

        def probe():
            attempts.append(len(attempts))
            if len(attempts) < 3:
                raise ConnectionRefusedError("not listening yet")
            return "up"

        self.assertEqual("up", validate.wait_for("the API", probe, timeout=30))
        self.assertEqual(3, len(attempts))

    def test_says_what_it_last_saw_when_time_runs_out(self):
        fake_time(self)
        with self.assertRaisesRegex(CheckFailed, r"Timed out after 5 s waiting for the clock; last saw \[\]"):
            validate.wait_for("the clock", lambda: [], timeout=5)

    def test_reports_the_last_error_when_the_device_never_answers(self):
        fake_time(self)

        def probe():
            raise TimeoutError("timed out")

        with self.assertRaisesRegex(CheckFailed, "last saw TimeoutError: timed out"):
            validate.wait_for("the API", probe, timeout=3)

    def test_other_errors_are_not_swallowed(self):
        fake_time(self)
        with self.assertRaises(KeyError):
            validate.wait_for("a field", lambda: {}["missing"], timeout=3)


class DescribeTest(unittest.TestCase):
    def test_shortens_long_values(self):
        self.assertEqual("abc", validate.describe("abc"))
        self.assertEqual('{"a": 1}', validate.describe({"a": 1}))
        shortened = validate.describe("x" * 1000, limit=20)
        self.assertEqual(20, len(shortened))
        self.assertTrue(shortened.endswith("..."))

    def test_describes_values_json_cannot_hold(self):
        self.assertIn("custom.html", validate.describe({"path": pathlib.PurePosixPath("/dashboard/custom.html")}))


class RunChecksTest(unittest.TestCase):
    class Context:
        details = {}

    def run_quietly(self, checks, **options):
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            results = validate.run_checks(checks, self.Context(), **options)
        return results, printed.getvalue()

    def test_records_each_outcome_and_keeps_going(self):
        def passes(context):
            context.details["measured"] = 3

        def fails(context):
            context.details["seen"] = "white"
            raise CheckFailed("the screen flashed")

        def asserts(_context):
            assert 1 == 2, "arithmetic"

        def skips(_context):
            raise CheckSkipped("not on this build")

        def breaks(_context):
            raise KeyError("missing")

        results, printed = self.run_quietly([
            ("one", "Passes", passes, False),
            ("two", "Fails", fails, False),
            ("three", "Asserts", asserts, False),
            ("four", "Skips", skips, False),
            ("five", "Breaks", breaks, False),
            ("six", "Still runs", passes, False),
        ])
        self.assertEqual(
            ["pass", "fail", "fail", "skip", "fail", "pass"],
            [result.status for result in results],
        )
        self.assertEqual({"measured": 3}, results[0].details)
        self.assertEqual("the screen flashed", results[1].message)
        self.assertEqual({"seen": "white"}, results[1].details)
        self.assertEqual("arithmetic", results[2].message)
        self.assertEqual("not on this build", results[3].message)
        self.assertEqual("KeyError: 'missing'", results[4].message)
        self.assertIn("KeyError", results[4].details["traceback"])
        # Details never leak from one check into the next.
        self.assertEqual({"measured": 3}, results[5].details)
        self.assertIn("FAIL  two", printed)
        self.assertIn("the screen flashed", printed)
        self.assertIn("PASS  six", printed)

    def test_quick_skips_slow_checks_and_only_selects_by_name(self):
        ran = []
        checks = [
            ("fast", "Fast", lambda context: ran.append("fast"), False),
            ("slow", "Slow", lambda context: ran.append("slow"), True),
            ("other", "Other", lambda context: ran.append("other"), False),
        ]
        results, _ = self.run_quietly(checks, quick=True)
        self.assertEqual(["fast", "other"], ran)
        self.assertEqual(["pass", "skip", "pass"], [result.status for result in results])
        del ran[:]
        results, _ = self.run_quietly(checks, only={"slow"})
        self.assertEqual(["slow"], ran)
        self.assertEqual(["skip", "pass", "skip"], [result.status for result in results])

    def test_report_counts_outcomes_and_failure_sets_the_exit_code(self):
        results = [
            validate.Result("a", "A", "pass", 1.5, "", {"k": 1}),
            validate.Result("b", "B", "fail", 0.2, "broke"),
            validate.Result("c", "C", "skip", 0.0, "not selected"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory)
            validate.write_report(output, "emulator", results, {"serial": "emulator-5580"})
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(1, validate.summarize(results, output))
                self.assertEqual(0, validate.summarize(results[:1], output))
        self.assertEqual(
            ("emulator", 1, 1, 1, "emulator-5580"),
            (report["target"], report["passed"], report["failed"], report["skipped"], report["serial"]),
        )
        self.assertEqual(
            {"name": "b", "title": "B", "status": "fail", "seconds": 0.2, "message": "broke", "details": {}},
            report["checks"][1],
        )
        self.assertIn("1 passed, 1 failed, 1 skipped", printed.getvalue())


class CheckListTest(unittest.TestCase):
    def test_check_names_are_unique_and_only_the_reboot_is_slow(self):
        names = [name for name, _, _, _ in validate.EMULATOR_CHECKS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(["reboot"], [name for name, _, _, slow in validate.EMULATOR_CHECKS if slow])
        self.assertEqual(
            ["reachable", "status", "health", "updater"],
            [name for name, _, _, _ in validate.MIRROR_CHECKS],
        )
        exercise = [name for name, _, _, _ in validate.EXERCISE_CHECKS]
        self.assertEqual(len(exercise), len(set(exercise)))
        self.assertEqual("health-after", exercise[-1])

    def test_the_run_ends_by_counting_script_errors(self):
        self.assertEqual("install", validate.EMULATOR_CHECKS[0][0])
        self.assertEqual("script-errors", validate.EMULATOR_CHECKS[-1][0])

    def test_the_guide_lists_every_check(self):
        guide = (REPO / "docs" / "validation.md").read_text(encoding="utf-8")
        for name, _, _, _ in validate.EMULATOR_CHECKS + validate.MIRROR_CHECKS + validate.EXERCISE_CHECKS:
            self.assertIn(f"`{name}`", guide, f"docs/validation.md does not describe {name}")


class SelectedChecksTest(unittest.TestCase):
    def test_everything_runs_unless_checks_are_named(self):
        self.assertIsNone(validate.selected_checks(None, validate.EMULATOR_CHECKS))
        self.assertIsNone(validate.selected_checks("", validate.EMULATOR_CHECKS))

    def test_named_checks_bring_the_pairing_they_depend_on(self):
        self.assertEqual(
            {"install", "first-pairing", "sleep-wake", "notes"},
            validate.selected_checks("sleep-wake, notes", validate.EMULATOR_CHECKS),
        )

    def test_the_prerequisites_are_real_checks(self):
        names = [name for name, _, _, _ in validate.EMULATOR_CHECKS]
        for name in validate.PREREQUISITE_CHECKS:
            self.assertIn(name, names)

    def test_a_misspelt_check_is_refused_with_the_choices(self):
        with self.assertRaisesRegex(CheckFailed, "Unknown check sleep; choose from install, setup-screen"):
            validate.selected_checks("sleep,notes", validate.EMULATOR_CHECKS)

    def test_a_misspelt_check_stops_the_run_before_an_emulator_starts(self):
        with mock.patch.object(validate.android_emulator, "Emulator") as emulator, \
                mock.patch.object(validate, "build_debug_apk") as build, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(1, validate.main(["emulator", "--only", "helth"]))
        emulator.assert_not_called()
        build.assert_not_called()
        self.assertIn("validate: Unknown check helth", errors.getvalue())


class RequireEmulatorTest(unittest.TestCase):
    class Device:
        def __init__(self, serial, qemu, fingerprint):
            self.serial = serial
            self.properties = {"ro.kernel.qemu": qemu, "ro.build.fingerprint": fingerprint}

        def property(self, name):
            return self.properties[name]

    def test_accepts_an_android_emulator(self):
        validate.require_emulator(self.Device("emulator-5580", "1", EMULATOR_FINGERPRINT))

    def test_refuses_a_mirror_reached_over_the_network(self):
        with self.assertRaisesRegex(CheckFailed, "never runs on a physical device"):
            validate.require_emulator(self.Device("10.0.0.196:5555", "", MIRROR_FINGERPRINT))

    def test_refuses_a_usb_device(self):
        with self.assertRaisesRegex(CheckFailed, "is not an Android emulator"):
            validate.require_emulator(self.Device("R58M12ABCDE", "", "samsung/a52/a52:13/TP1A/1:user/release-keys"))

    def test_refuses_an_emulator_serial_without_the_emulator_kernel(self):
        for qemu in ("", "0"):
            with self.assertRaises(CheckFailed):
                validate.require_emulator(self.Device("emulator-5580", qemu, EMULATOR_FINGERPRINT))

    def test_refuses_mirror_firmware_whatever_it_is_called(self):
        with self.assertRaisesRegex(CheckFailed, "mirror/mirror/msm8916_64"):
            validate.require_emulator(self.Device("emulator-5580", "1", MIRROR_FINGERPRINT))

    def test_the_suite_only_reads_properties_before_refusing_a_device(self):
        commands = []

        def run(arguments, **_options):
            commands.append(arguments[3:])
            name = arguments[-1]
            value = MIRROR_FINGERPRINT if name == "ro.build.fingerprint" else ""
            return subprocess.CompletedProcess(arguments, 0, stdout=value.encode("ascii"), stderr=b"")

        with tempfile.TemporaryDirectory() as directory:
            apk = pathlib.Path(directory) / "mirror-home-debug.apk"
            apk.write_bytes(b"apk")
            options = argparse.Namespace(
                output=pathlib.Path(directory) / "out", apk=apk, skip_build=True, serial="10.0.0.196:5555",
                keep_running=False, window=False, quick=False, only=None, density=160, upgrade_from=None,
                voice_model=apk,
            )
            with mock.patch.object(validate.subprocess, "run", side_effect=run), \
                    mock.patch.object(validate.android_emulator, "sdk_root", return_value=pathlib.Path(directory)), \
                    mock.patch.object(validate.android_emulator, "adb_path", return_value=pathlib.Path("adb")):
                with self.assertRaisesRegex(CheckFailed, "never runs on a physical device"):
                    validate.run_emulator(options)
        self.assertTrue(commands)
        for command in commands:
            self.assertEqual(["shell", "getprop"], command[:2], f"ran adb {' '.join(command)}")


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                cls.seen.append({
                    "method": self.command,
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                    "checksum": self.headers.get("X-Content-SHA256"),
                    "body": self.rfile.read(length),
                })
                if self.path == "/styles.css":
                    status, kind, body = 200, "text/css", b"body{}"
                elif self.path == "/api/v1/missing":
                    status, kind, body = 404, "application/json", b'{"error": "Not found"}'
                elif self.path == "/api/v1/empty":
                    status, kind, body = 200, "application/json", b""
                else:
                    status, kind, body = 200, "application/json; charset=utf-8", b'{"ok": true}'
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Retry-After", "30")
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = do_DELETE = answer

            def log_message(self, *_arguments):
                pass

        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        del self.seen[:]
        self.api = Api("127.0.0.1", self.server.server_address[1], "paired-credential")

    def test_sends_the_credential_and_reads_json(self):
        reply = self.api.call("PUT", "/api/v1/preferences", {"timeZone": "Etc/UTC"})
        self.assertEqual((200, {"ok": True}), (reply.status, reply.body))
        self.assertEqual("30", reply.headers["retry-after"])
        request = self.seen[0]
        self.assertEqual(("PUT", "/api/v1/preferences"), (request["method"], request["path"]))
        self.assertEqual("Bearer " + "paired-credential", request["authorization"])
        self.assertEqual("application/json", request["content_type"])
        self.assertEqual({"timeZone": "Etc/UTC"}, json.loads(request["body"]))

    def test_can_call_without_or_with_another_credential(self):
        self.api.call("GET", "/api/v1/bootstrap", token=None)
        self.api.call("GET", "/api/v1/clients", token="someone-else")
        self.assertIsNone(self.seen[0]["authorization"])
        self.assertEqual("Bearer " + "someone-else", self.seen[1]["authorization"])

    def test_sends_a_file_with_its_type_and_further_headers(self):
        self.api.call(
            "PUT", "/api/v1/voice/model", data=b"PK archive", content_type="application/zip",
            headers={"X-Content-SHA256": "abc123"},
        )
        request = self.seen[0]
        self.assertEqual(("application/zip", "abc123", b"PK archive"),
                         (request["content_type"], request["checksum"], request["body"]))
        self.assertEqual("Bearer " + "paired-credential", request["authorization"])

    def test_returns_other_content_as_bytes(self):
        self.assertEqual(b"body{}", self.api.call("GET", "/styles.css", token=None).body)

    def test_an_empty_json_body_is_an_empty_object(self):
        self.assertEqual({}, self.api.call("GET", "/api/v1/empty").body)

    def test_expect_returns_the_body_or_explains_the_status(self):
        self.assertEqual({"ok": True}, self.api.expect("GET", "/api/v1/status"))
        self.assertEqual({"error": "Not found"}, self.api.expect("GET", "/api/v1/missing", status=404))
        with self.assertRaisesRegex(CheckFailed, r"GET /api/v1/missing answered 404, expected 200: .*Not found"):
            self.api.expect("GET", "/api/v1/missing")


class SleepWakeVerdictTest(unittest.TestCase):
    def context(self, peaks, directory, *, dashboard_measured=True):
        api = FakeApi({
            ("POST", "/api/v1/automation/sleep"): {"sleeping": True},
            ("POST", "/api/v1/automation/wake"): {"sleeping": False},
            ("PUT", "/api/v1/automation"): {"sleeping": False},
            ("GET", "/api/v1/health"): healthy_report(),
        })
        ctx = validate.Context(FakeAdb([frame(peak) for peak in peaks]), api, pathlib.Path(directory))
        if dashboard_measured:
            ctx.awake_peak = 255
        return ctx

    def test_run_alone_it_first_waits_for_the_dashboard_to_fade_in(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([0, 60, 255, 255, 140, 0, 0, 255], directory, dashboard_measured=False)
            validate.check_sleep_wake(ctx)
            self.assertEqual(255, ctx.awake_peak)
            self.assertEqual([140, 0], ctx.details["sleepFadePeaks"])
            self.assertTrue((pathlib.Path(directory) / "awake-reference.png").is_file())

    def test_a_fade_through_intermediate_levels_passes(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([255, 200, 120, 30, 0, 0, 255], directory)
            validate.check_sleep_wake(ctx)
            self.assertEqual([200, 120, 30, 0], ctx.details["sleepFadePeaks"])
            self.assertEqual((255, 255), (ctx.details["awakePeak"], ctx.details["restoredPeak"]))
            self.assertTrue((pathlib.Path(directory) / "asleep.png").is_file())
            # The manual override is cleared so later schedule checks start clean.
            self.assertEqual("PUT", ctx.api.calls[-1][0])
            self.assertIs(False, ctx.api.calls[-1][2]["enabled"])

    def test_a_cut_to_black_fails_after_three_attempts(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([255, 0, 255, 0, 255, 0], directory)
            with self.assertRaisesRegex(CheckFailed, "cut to black"):
                validate.check_sleep_wake(ctx)
            sleeps = [call for call in ctx.api.calls if call[1] == "/api/v1/automation/sleep"]
            self.assertEqual(3, len(sleeps))

    def test_a_second_attempt_may_catch_the_fade(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([255, 0, 255, 128, 0, 0, 255], directory)
            validate.check_sleep_wake(ctx)
            self.assertEqual([128, 0], ctx.details["sleepFadePeaks"])

    def test_a_display_that_stays_lit_fails(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([255, 255], directory)
            with self.assertRaisesRegex(CheckFailed, "Sleep left a pixel lit at 255"):
                validate.check_sleep_wake(ctx)

    def test_a_dim_dashboard_cannot_show_a_fade(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([90], directory)
            with self.assertRaisesRegex(CheckFailed, "too dim to observe a fade"):
                validate.check_sleep_wake(ctx)

    def test_a_display_that_does_not_wake_fails(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([255, 120, 0, 0, 0], directory)
            with self.assertRaisesRegex(CheckFailed, "never became lit again; brightest pixel 0"):
                validate.check_sleep_wake(ctx)
            self.assertTrue((pathlib.Path(directory) / "awake-timeout.png").is_file())


class ScheduleApi(FakeApi):
    """A Mirror whose schedule puts the display to sleep while it is enabled."""

    def __init__(self, *, sleep_reason="schedule", clock_step=20_000):
        super().__init__()
        self.now = OCTOBER_FIRST
        self.clock_step = clock_step
        self.sleep_reason = sleep_reason
        self.schedule_enabled = False

    def call(self, method, path, body=None, **_options):
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/api/v1/health"):
            self.now += self.clock_step
            report = healthy_report()
            report["now"] = self.now
            return Reply(200, report, {})
        if (method, path) == ("PUT", "/api/v1/preferences"):
            return Reply(200, {"clockSource": "client"}, {})
        if (method, path) == ("PUT", "/api/v1/automation"):
            self.schedule_enabled = body["enabled"]
            return Reply(200, {"sleeping": False}, {})
        if (method, path) == ("GET", "/api/v1/automation"):
            reason = self.sleep_reason if self.schedule_enabled else ""
            return Reply(200, {"sleeping": self.schedule_enabled, "sleepReason": reason}, {})
        raise AssertionError(f"Unexpected {method} {path}")


class ScheduleSwitchVerdictTest(unittest.TestCase):
    def context(self, api, directory, peaks=(0, 255)):
        ctx = validate.Context(FakeAdb([frame(peak) for peak in peaks]), api, pathlib.Path(directory))
        ctx.awake_peak = 255
        return ctx

    def assert_left_awake_on_local_time(self, api):
        changes = [call for call in api.calls if call[0] == "PUT"]
        self.assertEqual("/api/v1/preferences", changes[-1][1])
        self.assertEqual("America/Los_Angeles", changes[-1][2]["timeZone"])
        self.assertEqual("/api/v1/automation", changes[-2][1])
        self.assertIs(False, changes[-2][2]["enabled"])

    def test_sleeping_on_schedule_after_the_change_passes(self):
        fake_time(self)
        api = ScheduleApi()
        with tempfile.TemporaryDirectory() as directory:
            validate.check_schedule_switch(self.context(api, directory))
            self.assertTrue((pathlib.Path(directory) / "schedule-asleep.png").is_file())
            self.assertTrue((pathlib.Path(directory) / "schedule-awake.png").is_file())
        clock = next(call[2] for call in api.calls if call[1] == "/api/v1/preferences")
        self.assertEqual("Etc/UTC", clock["timeZone"])
        # Noon now and midnight ten seconds later: the schedule must sleep then.
        self.assertEqual(720, abs(clock["utcOffsetMinutes"] - clock["utcOffsetChanges"][0]["utcOffsetMinutes"]))
        self.assert_left_awake_on_local_time(api)

    def test_sleeping_for_another_reason_fails_and_still_restores_the_mirror(self):
        fake_time(self)
        api = ScheduleApi(sleep_reason="manual")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CheckFailed, "Sleep reason is manual"):
                validate.check_schedule_switch(self.context(api, directory))
        self.assert_left_awake_on_local_time(api)

    def test_sleeping_before_the_change_fails(self):
        fake_time(self)
        api = ScheduleApi(clock_step=0)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CheckFailed, "slept before the clock change"):
                validate.check_schedule_switch(self.context(api, directory))
        self.assert_left_awake_on_local_time(api)

    def test_a_screen_that_stays_lit_while_asleep_fails(self):
        fake_time(self)
        api = ScheduleApi()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CheckFailed, "never became black; brightest pixel 255"):
                validate.check_schedule_switch(self.context(api, directory, peaks=(255,)))
        self.assert_left_awake_on_local_time(api)


class ReleaseBuildTest(unittest.TestCase):
    """A release build cannot be read over DevTools; the screen still can."""

    class Api(FakeApi):
        def __init__(self):
            super().__init__()
            self.web_page = ""

        def call(self, method, path, body=None, **_options):
            self.calls.append((method, path, body))
            if (method, path) == ("GET", "/api/v1/health"):
                report = healthy_report()
                report["debuggable"] = False
                if self.web_page:
                    report["dashboard"].update(
                        url="http://127.0.0.1:8787/dashboard/offline.html",
                        lastFailurePhase="load-error",
                    )
                return Reply(200, report, {})
            if (method, path) == ("PUT", "/api/v1/dashboard"):
                self.web_page = body["url"]
                return Reply(200, {"url": body["url"]}, {})
            raise AssertionError(f"Unexpected {method} {path}")

    def context(self, directory, peaks=(255,)):
        return validate.Context(
            FakeAdb([frame(peak) for peak in peaks]), self.Api(), pathlib.Path(directory)
        )

    def test_the_page_is_not_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            self.assertFalse(ctx.inspectable())
            with self.assertRaisesRegex(CheckSkipped, "release build"):
                with ctx.page():
                    self.fail("the page was opened")
            self.assertEqual([], ctx.adb.commands)

    def test_checks_that_need_the_page_skip_before_changing_anything(self):
        for check in (validate.check_clock_switch, validate.check_pairing_widget, validate.check_notes):
            with tempfile.TemporaryDirectory() as directory:
                ctx = self.context(directory)
                with self.assertRaises(CheckSkipped):
                    check(ctx)
                self.assertEqual({("GET", "/api/v1/health")}, {call[:2] for call in ctx.api.calls}, check.__name__)

    def test_the_dashboard_is_still_judged_by_the_screen(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            validate.check_dashboard(ctx)
            self.assertIs(False, ctx.details["pageInspected"])
            self.assertEqual(255, ctx.awake_peak)
            self.assertGreater(ctx.details["dashboardBlack"], 0.9)
            self.assertTrue((pathlib.Path(directory) / "dashboard.png").is_file())

    def test_a_dashboard_that_never_lights_fails_on_a_release_build_too(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CheckFailed, "waiting for the dashboard to fade in"):
                validate.check_dashboard(self.context(directory, peaks=(0,)))

    def test_the_offline_fallback_is_still_judged_by_the_screen(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            validate.check_offline_fallback(ctx)
            self.assertTrue((pathlib.Path(directory) / "offline-fallback.png").is_file())
            addresses = [call[2]["url"] for call in ctx.api.calls if call[0] == "PUT"]
            self.assertEqual(["http://127.0.0.1:9/unreachable", ""], addresses)


class UpgradeAdb(FakeAdb):
    """An emulator whose package manager reports the given versions in turn."""

    def __init__(self, versions, *, install="Success\n", window=""):
        super().__init__([frame(255)])
        self.versions = list(versions)
        self.install = install
        self.window = window

    def shell(self, *arguments, **_options):
        self.commands.append(("shell", *arguments))
        if arguments[:2] == ("dumpsys", "package"):
            name, code = self.versions.pop(0) if len(self.versions) > 1 else self.versions[0]
            return f"    versionCode={code} minSdk=23 targetSdk=35\n    versionName={name}\n"
        if arguments[0] == "cat":
            return self.window
        return ""

    def run(self, *arguments, **_options):
        self.commands.append(arguments)
        if arguments[0] == "install":
            if isinstance(self.install, Exception):
                raise self.install
            return self.install
        return ""


class UpgradeRehearsalTest(unittest.TestCase):
    SETUP_SCREEN = (
        '<hierarchy><node text="Ready to set up" /><node text="PAIRING CODE" />'
        '<node text="248 641" /></hierarchy>'
    )

    def health(self, **changes):
        report = healthy_report()
        report.update(appVersion="2.2.0-rc.1", versionCode=73)
        report["process"]["previousRun"] = None
        report.update(changes)
        return report

    def context(self, adb, api, directory):
        ctx = validate.Context(adb, api, pathlib.Path(directory))
        ctx.apk = pathlib.Path(directory) / "new.apk"
        ctx.kept["version"] = ("2.2.0-dev.1", 72)
        return ctx

    def test_the_awake_window_surrounds_now_and_wraps_past_midnight(self):
        noon_utc = OCTOBER_FIRST
        self.assertEqual(("03:00", "11:00"), validate.awake_window(noon_utc, -420))
        self.assertEqual(("10:00", "18:00"), validate.awake_window(noon_utc, 0))
        self.assertEqual(("21:30", "05:30"), validate.awake_window(noon_utc, 690))
        self.assertEqual(("22:00", "06:00"), validate.awake_window(noon_utc, -720))

    def test_upgrade_checks_are_named_once_and_described_in_the_guide(self):
        names = [name for name, _, _, _ in validate.UPGRADE_CHECKS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual("update", names[2])
        guide = (REPO / "docs" / "validation.md").read_text(encoding="utf-8")
        for name in names:
            self.assertIn(f"`{name}`", guide, f"docs/validation.md does not describe {name}")

    def test_the_pairing_code_is_read_from_the_setup_screen(self):
        fake_time(self)
        api = FakeApi({
            ("POST", "/api/v1/pair"): {"token": "paired", "clientId": "c1"},
            ("PUT", "/api/v1/preferences"): {},
            ("PUT", "/api/v1/automation"): {"sleeping": False},
            ("POST", "/api/v1/notes"): Reply(201, {"note": {"id": "n1"}}, {}),
            ("GET", "/api/v1/dashboard/layout"): {"widgets": [
                {"id": "date", "opacity": 70, "visible": True},
                {"id": "name", "opacity": 58, "visible": False},
            ]},
            ("PUT", "/api/v1/dashboard/layout"): {},
        })
        adb = UpgradeAdb([("2.2.0-dev.1", 72)], window=self.SETUP_SCREEN)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(adb, api, directory)
            validate.upgrade_owner_settings(ctx)
        sent = {call[1]: call[2] for call in api.calls if call[0] != "GET"}
        self.assertEqual("248641", sent["/api/v1/pair"]["code"])
        self.assertEqual("America/Los_Angeles", sent["/api/v1/pair"]["timeZone"])
        self.assertEqual("paired", api.token)
        # Earlier controls sent a zone and an offset, never a list of changes.
        self.assertNotIn("utcOffsetChanges", sent["/api/v1/preferences"])
        self.assertEqual(
            (ctx.kept["wake"], ctx.kept["sleep"]),
            (sent["/api/v1/automation"]["wakeTime"], sent["/api/v1/automation"]["sleepTime"]),
        )
        self.assertIs(True, sent["/api/v1/automation"]["enabled"])
        self.assertEqual("Kept across the update", sent["/api/v1/notes"]["text"])
        layout = {widget["id"]: widget for widget in sent["/api/v1/dashboard/layout"]["widgets"]}
        self.assertEqual((73, True), (layout["date"]["opacity"], layout["name"]["visible"]))

    def test_an_earlier_build_without_a_code_on_screen_is_asked_over_loopback(self):
        fake_time(self)
        api = FakeApi({
            ("GET", "/api/v1/dashboard/runtime"): {"pairingCode": "135790"},
            ("POST", "/api/v1/pair"): {"token": "paired", "clientId": "c1"},
            ("PUT", "/api/v1/preferences"): {},
            ("PUT", "/api/v1/automation"): {"sleeping": False},
            ("POST", "/api/v1/notes"): Reply(201, {"note": {"id": "n1"}}, {}),
            ("GET", "/api/v1/dashboard/layout"): {"widgets": []},
            ("PUT", "/api/v1/dashboard/layout"): {},
        })
        adb = UpgradeAdb([("2.2.0-dev.1", 72)], window='<hierarchy><node text="Ready to set up" /></hierarchy>')
        with tempfile.TemporaryDirectory() as directory:
            validate.upgrade_owner_settings(self.context(adb, api, directory))
        pair = next(call[2] for call in api.calls if call[1] == "/api/v1/pair")
        self.assertEqual("135790", pair["code"])

    def test_the_update_installs_over_the_earlier_build(self):
        fake_time(self)
        api = FakeApi({("GET", "/api/v1/health"): self.health()})
        adb = UpgradeAdb([("2.2.0-rc.1", 73)])
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(adb, api, directory)
            validate.upgrade_install(ctx)
            self.assertEqual("2.2.0-rc.1 (code 73)", ctx.details["to"])
        self.assertEqual(("install", "-r", "-g"), adb.commands[0][:3])
        # On a Mirror nobody starts the updated Home; it has to come back by itself.
        self.assertEqual([], [command for command in adb.commands if command[:3] == ("shell", "am", "start")])

    def test_an_update_that_costs_mirror_home_its_place_as_home_fails(self):
        fake_time(self)
        for selected, front in ((True, "dev.mirror.repurpose/.MainActivity"), (False, "")):
            report = self.health()
            report["activity"].update(selectedHome=selected, front=front)
            with tempfile.TemporaryDirectory() as directory:
                ctx = self.context(
                    UpgradeAdb([("2.2.0-rc.1", 73)]), FakeApi({("GET", "/api/v1/health"): report}), directory
                )
                if selected:
                    validate.upgrade_dashboard(ctx)
                    self.assertEqual(front, ctx.details["front"])
                else:
                    with self.assertRaisesRegex(CheckFailed, "cost Mirror Home its place as the HOME app"):
                        validate.upgrade_dashboard(ctx)

    def test_an_earlier_build_that_reports_no_home_selection_is_not_failed_for_it(self):
        fake_time(self)
        report = self.health()
        del report["activity"]["selectedHome"]
        del report["activity"]["front"]
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(
                UpgradeAdb([("2.2.0-rc.1", 73)]), FakeApi({("GET", "/api/v1/health"): report}), directory
            )
            validate.upgrade_dashboard(ctx)
            self.assertIsNone(ctx.details["front"])

    def test_the_update_must_raise_the_version_code(self):
        fake_time(self)
        api = FakeApi({("GET", "/api/v1/health"): self.health()})
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(UpgradeAdb([("2.2.0-rc.1", 72)]), api, directory)
            with self.assertRaisesRegex(CheckFailed, "does not exceed 72; the OTA supervisor would refuse it"):
                validate.upgrade_install(ctx)

    def test_a_build_signed_with_another_key_is_explained(self):
        refusal = CheckFailed("adb install -r -g new.apk failed: Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]")
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(UpgradeAdb([("2.2.0-dev.1", 72)], install=refusal), FakeApi(), directory)
            with self.assertRaisesRegex(CheckFailed, "signed with a different key"):
                validate.upgrade_install(ctx)

    def test_a_crash_or_a_wrong_ending_after_the_update_fails(self):
        fake_time(self)
        crashed = self.health(crashes={"count": 1, "last": {"exception": "java.lang.IllegalStateException"}})
        killed = self.health()
        killed["process"]["previousRun"] = {"runId": 1, "end": "killed"}
        for report, message in ((crashed, "The updated Home crashed"), (killed, "recorded as")):
            with tempfile.TemporaryDirectory() as directory:
                ctx = self.context(
                    UpgradeAdb([("2.2.0-rc.1", 73)]), FakeApi({("GET", "/api/v1/health"): report}), directory
                )
                with self.assertRaisesRegex(CheckFailed, message):
                    validate.upgrade_install(ctx)

    def settings_api(self, **preferences):
        table = json.loads(validate.ZONE_TABLE.read_text(encoding="utf-8"))
        now = table["from"] * 60_000
        offset, upcoming = validate.zone_timeline(table, "America/Los_Angeles", now)
        report = self.health(now=now)
        saved = {
            "timeZone": "America/Los_Angeles", "clock24Hour": True,
            "clockSource": "bundled", "utcOffsetMinutes": offset,
        }
        saved.update(preferences)
        return FakeApi({
            ("GET", "/api/v1/health"): report,
            ("GET", "/api/v1/preferences"): saved,
            ("GET", "/api/v1/status"): {"nextUtcOffsetChange": upcoming[0]},
            ("GET", "/api/v1/automation"): {
                "enabled": True, "wakeTime": "03:00", "sleepTime": "11:00",
                "wakeBrightness": 140, "sleeping": False,
            },
            ("GET", "/api/v1/notes"): {"notes": [{"text": "Kept across the update"}]},
            ("GET", "/api/v1/dashboard/layout"): {"widgets": [
                {"id": "date", "opacity": 73, "visible": True},
                {"id": "name", "opacity": 58, "visible": True},
            ]},
        }), upcoming[0]

    def test_settings_that_survive_pass_and_report_the_next_clock_change(self):
        api, change = self.settings_api()
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(UpgradeAdb([("2.2.0-rc.1", 73)]), api, directory)
            ctx.kept.update(wake="03:00", sleep="11:00")
            validate.upgrade_kept_settings(ctx)
            self.assertEqual(change, ctx.details["nextClockChange"])
        self.assertEqual({"GET"}, {call[0] for call in api.calls})

    def test_a_saved_zone_that_stays_on_a_fixed_offset_fails(self):
        api, _ = self.settings_api(clockSource="fixed")
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(UpgradeAdb([("2.2.0-rc.1", 73)]), api, directory)
            ctx.kept.update(wake="03:00", sleep="11:00")
            with self.assertRaisesRegex(CheckFailed, "followed as fixed, not from the bundled table"):
                validate.upgrade_kept_settings(ctx)

    def test_a_changed_schedule_fails(self):
        api, _ = self.settings_api()
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(UpgradeAdb([("2.2.0-rc.1", 73)]), api, directory)
            ctx.kept.update(wake="06:00", sleep="22:00")
            with self.assertRaisesRegex(CheckFailed, "The schedule changed"):
                validate.upgrade_kept_settings(ctx)

    def test_the_rehearsal_refuses_options_of_the_main_suite_and_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            earlier = pathlib.Path(directory) / "old.apk"
            for arguments, message in (
                (["emulator", "--upgrade-from", str(earlier)], "APK not found"),
                (["emulator", "--upgrade-from", str(earlier), "--quick"], "leave out --only and --quick"),
                (["emulator", "--upgrade-from", str(earlier), "--only", "health"], "leave out --only and --quick"),
            ):
                with mock.patch.object(validate.android_emulator, "Emulator") as emulator, \
                        mock.patch.object(validate, "build_debug_apk") as build, \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(1, validate.main(arguments))
                emulator.assert_not_called()
                build.assert_not_called()
                self.assertIn(message, errors.getvalue())


class RestartingMirror:
    """Mirror Home as the restart checks see it: Android starts its HOME app
    again whenever it is stopped, and each start is a new run."""

    def __init__(self, *, uptime=3600, counts_early_stops=True):
        self.run = 7
        self.long_run = 7
        self.uptime = uptime
        self.early = 0
        self.counts_early_stops = counts_early_stops
        self.api = FakeApi()
        self.api.call = self.call
        self.adb = FakeAdb([frame(255)])
        self.adb.shell = self.shell
        self.waited = False

    def shell(self, *arguments, **_options):
        self.adb.commands.append(("shell", *arguments))
        if arguments == ("am", "force-stop", validate.PACKAGE):
            lived_long = self.uptime > validate.EARLY_STOP_SECONDS
            if lived_long or not self.counts_early_stops:
                self.long_run, self.early = self.run, 0
            else:
                self.early += 1
            self.run += 1
            self.uptime = 1
        return ""

    def call(self, method, path, body=None, **_options):
        self.api.calls.append((method, path, body))
        if (method, path) == ("GET", "/api/v1/health"):
            report = healthy_report()
            report["process"].update(
                runId=self.run, uptimeSeconds=self.uptime, earlyStops=self.early,
                previousRun={"runId": self.long_run, "end": "killed"},
            )
            return Reply(200, report, {})
        if (method, path) == ("GET", "/api/v1/clients"):
            return Reply(200, {"clients": []}, {})
        raise AssertionError(f"Unexpected {method} {path}")

    def context(self, directory):
        ctx = validate.Context(self.adb, self.api, pathlib.Path(directory))
        ctx.awake_peak = 255
        return ctx


class RestartVerdictTest(unittest.TestCase):
    def test_a_stopped_process_becomes_the_previous_run(self):
        fake_time(self)
        mirror = RestartingMirror()
        with tempfile.TemporaryDirectory() as directory:
            validate.check_restart(mirror.context(directory))
        self.assertEqual(8, mirror.run)

    def test_the_check_first_lets_home_run_past_its_start(self):
        clock = fake_time(self)
        mirror = RestartingMirror(uptime=3)
        real_call = mirror.call

        def call(method, path, body=None, **options):
            if path == "/api/v1/health" and mirror.run == 7:
                mirror.uptime += 4
            return real_call(method, path, body, **options)

        mirror.api.call = call
        with tempfile.TemporaryDirectory() as directory:
            validate.check_restart(mirror.context(directory))
        self.assertEqual(8, mirror.run)
        self.assertEqual(7, mirror.long_run)
        self.assertGreater(clock.now, 1_000)

    def test_a_process_stopped_while_starting_is_counted(self):
        fake_time(self)
        mirror = RestartingMirror()
        with tempfile.TemporaryDirectory() as directory:
            ctx = mirror.context(directory)
            validate.check_quick_restart(ctx)
            self.assertLess(ctx.details["stoppedAfterSeconds"], validate.EARLY_STOP_SECONDS)
        self.assertEqual((9, 7, 1), (mirror.run, mirror.long_run, mirror.early))
        # Nothing but Android starts Home again, as on a Mirror.
        self.assertEqual(
            [("shell", "am", "force-stop", validate.PACKAGE)] * 2,
            mirror.adb.commands,
        )

    def test_reporting_it_as_the_previous_run_fails(self):
        fake_time(self)
        mirror = RestartingMirror(counts_early_stops=False)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CheckFailed, "stopped while starting is reported as the previous run"):
                validate.check_quick_restart(mirror.context(directory))

    def test_a_host_too_slow_to_stop_a_process_early_skips(self):
        clock = fake_time(self)
        mirror = RestartingMirror()
        real_shell = mirror.shell

        def slow_shell(*arguments, **options):
            clock.now += 4
            return real_shell(*arguments, **options)

        mirror.adb.shell = slow_shell
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CheckSkipped, "too slow to stop a process within its first seconds"):
                validate.check_quick_restart(mirror.context(directory))


CLIP_SENTENCES = {
    "mirror-go-to-sleep.wav": [("mirror go to sleep", 1.0)],
    "mirror-wake-up.wav": [("mirror wake up", 1.0)],
    # What the recogniser makes of "I am going to sleep early tonight".
    "talk-of-sleep.wav": [("[unk] go to sleep [unk]", 1.0), ("night", 1.0)],
}
SPOKEN = {
    "go to sleep": "sleep", "good night": "sleep", "wake up": "wake", "good morning": "wake",
    "brighter": "brighter", "dimmer": "dimmer",
}


def glass(*, dashboard=True, caption=False):
    """A small frame of the glass: one lit stroke of the dashboard, one of a caption."""
    width, height = 10, 50
    pixels = bytearray(bytes([0, 0, 0, 255]) * (width * height))
    for lit, column, row in ((dashboard, 1, 2), (caption, 5, 45)):
        if lit:
            offset = (row * width + column) * 4
            pixels[offset:offset + 3] = b"\xff\xff\xff"
    return screen_capture.Screenshot(width, height, bytes(pixels))


class SpeakingMirror:
    """Mirror Home's voice as the checks meet it: the API, the recogniser's
    process and the caption on the glass. ``faults`` name what a build gets wrong."""

    def __init__(self, test, *faults, test_hooks=True):
        self.clock = fake_time(test)
        self.faults = set(faults)
        self.test_hooks = test_hooks
        self.enabled = "on-from-the-start" in self.faults
        self.model = None
        self.permission = True
        self.sleeping = False
        self.wake_brightness = 180
        self.run = 7
        self.pid = 4100
        self.restarts = 0
        self.down = False
        self.installing = False
        self.has_listened = False
        self.planted = False
        self.counts = {"sentences": 0, "wakeWords": 0, "commands": 0, "notUnderstood": 0, "unsure": 0}
        self.recent = []
        self.last_command = None
        self.window_until = 0.0
        self.caption = ""
        self.caption_until = 0.0
        self.clips = {
            (validate.VOICE_CLIPS / name).read_bytes(): sentences
            for name, sentences in CLIP_SENTENCES.items()
        }
        self.api = FakeApi()
        self.api.call = self.call
        self.adb = FakeAdb()
        self.adb.shell = self.shell
        self.adb.capture = self.capture

    def context(self, directory, *, model=True):
        ctx = validate.Context(self.adb, self.api, pathlib.Path(directory))
        ctx.awake_peak = 255
        if model:
            ctx.voice_model = pathlib.Path(directory) / "model.zip"
            ctx.voice_model.write_bytes(validate.model_archive({
                f"vosk-model-test/{name}": b"weights" for name in validate.VOICE_MODEL_FILES
            }))
        return ctx

    def state(self):
        if not self.enabled:
            return "off"
        if self.model is None:
            return "no-model"
        if not self.permission:
            return "no-permission"
        if self.installing and "listens-through-installations" not in self.faults:
            return "paused"
        if self.model["name"] == "hollow-model":
            return "error"
        if self.down or "never-listens" in self.faults:
            return "starting"
        self.has_listened = True
        return "listening"

    def process_running(self):
        state = self.state()
        if state == "no-permission":
            return "runs-unpermitted" in self.faults
        if state in ("off", "no-model"):
            return "process-lingers" in self.faults and self.has_listened
        if state == "paused":
            return "keeps-its-memory" in self.faults
        return not self.down

    def voice(self):
        state = self.state()
        running = self.process_running()
        return {
            "enabled": self.enabled,
            "state": state,
            "detail": {"error": "The speech model could not be loaded"}.get(state, f"Voice is {state}"),
            "wakeWord": "mirror",
            "commands": [
                {"id": "sleep", "caption": "Sleeping", "say": ["mirror go to sleep", "mirror good night"]},
                {"id": "wake", "caption": "Awake", "say": ["mirror wake up", "mirror good morning"]},
            ],
            "model": self.model,
            "permissionGranted": self.permission,
            "process": {
                "pid": self.pid if running else None,
                "pssKb": (300_000 if "heavy" in self.faults else 120_000) if running else None,
                "restarts": self.restarts,
            },
            "recogniser": {
                "modelLoadMs": 1300, "cpuShare": 0.03 if state == "listening" else None,
                "behindMs": 0, "listenedSeconds": 5,
            },
            "microphone": {"levelDb": -120.0, "peakDb": -120.0, "silent": True},
            "counts": dict(self.counts),
            "lastCommand": self.last_command,
            "recent": list(self.recent),
            "testHooks": self.test_hooks,
        }

    def call(self, method, path, body=None, **options):
        self.api.calls.append((method, path, body))
        route = (method, path)
        if options.get("token") == "not-a-credential" and "open-to-all" not in self.faults:
            return Reply(401, {"error": "Unauthorized"}, {})
        if route == ("GET", "/api/v1/voice"):
            return Reply(200, self.voice(), {})
        if route == ("PUT", "/api/v1/voice"):
            if not isinstance(body.get("enabled"), bool):
                return Reply(400, {"error": "enabled must be true or false"}, {})
            self.enabled = body["enabled"]
            return Reply(200, self.voice(), {})
        if route == ("PUT", "/api/v1/voice/model"):
            return self.upload(options["data"], options.get("content_type") or "", options.get("headers") or {})
        if route == ("DELETE", "/api/v1/voice/model"):
            if "model-stays" not in self.faults:
                self.model = None
            return Reply(200, self.voice(), {})
        if route == ("POST", "/api/v1/voice/test/sentence"):
            if self.state() != "listening" and "hears-while-off" not in self.faults:
                return Reply(409, {"error": "Voice is not listening"}, {})
            self.hear(body["text"], body.get("confidence", 1.0))
            return Reply(202, {"accepted": True}, {})
        if route == ("POST", "/api/v1/voice/test/clip"):
            if self.state() != "listening":
                return Reply(409, {"error": "Voice is not listening"}, {})
            for text, confidence in self.clips[options["data"]]:
                self.hear(text, confidence)
            return Reply(202, {"accepted": True}, {})
        if route == ("GET", "/api/v1/automation"):
            return Reply(200, {"sleeping": self.sleeping, "wakeBrightness": self.wake_brightness}, {})
        if route == ("PUT", "/api/v1/automation"):
            self.sleeping = False
            self.wake_brightness = body["wakeBrightness"]
            return Reply(200, {"sleeping": False, "wakeBrightness": self.wake_brightness}, {})
        if route == ("GET", "/api/v1/health"):
            report = healthy_report()
            crashed = "crashes-on-a-bad-model" in self.faults and self.state() == "error"
            report["process"]["runId"] = self.run + (1 if crashed else 0)
            report["voice"] = self.voice()
            return Reply(200, report, {})
        if route == ("GET", "/api/v1/status"):
            return Reply(200, {"voice": {"enabled": self.enabled, "state": self.state()}}, {})
        raise AssertionError(f"Unexpected {method} {path}")

    def upload(self, data, content_type, headers):
        if not content_type.startswith("application/zip"):
            return Reply(415, {"error": "A zip Content-Type is required"}, {})
        checksum = hashlib.sha256(data).hexdigest()
        claimed = headers.get("X-Content-SHA256")
        if claimed and claimed != checksum and "trusts-any-checksum" not in self.faults:
            return Reply(400, {"error": "The upload does not match its SHA-256"}, {})
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return Reply(400, {"error": "The upload is not a zip archive"}, {})
        if any(".." in name for name in names):
            self.planted = "follows-paths" in self.faults
            return Reply(400, {"error": "The archive names a file outside itself"}, {})
        root = names[0].split("/")[0]
        if not all(f"{root}/{name}" in names for name in validate.VOICE_MODEL_FILES):
            return Reply(400, {"error": "This is not a speech model"}, {})
        self.model = {"name": root, "bytes": len(data), "files": len(names), "sha256": checksum, "installedAt": 0}
        return Reply(201, self.voice(), {})

    def hear(self, text, confidence=1.0):
        now = self.clock.now
        self.counts["sentences"] += 1
        words = text.split()
        named = bool(words) and words[0] == "mirror"
        while words and words[0] == "mirror":
            words.pop(0)
        rest = " ".join(words)
        sure = confidence >= 0.8 or "acts-when-unsure" in self.faults
        addressed = named or now <= self.window_until or "needs-no-name" in self.faults
        command = SPOKEN.get(rest)
        if command is None and "takes-talk-for-commands" in self.faults:
            command = next((name for wording, name in SPOKEN.items() if wording in rest), None)
            addressed = True
        if named and not rest:
            if sure:
                self.counts["wakeWords"] += 1
                self.window_until = now + (600 if "window-never-closes" in self.faults else 6)
                self.show("Listening", 6)
            else:
                self.counts["unsure"] += 1
        elif command and addressed:
            if not sure:
                self.counts["unsure"] += 1
                return
            self.window_until = 0.0
            self.counts["commands"] += 1
            shown = self.carry_out(command)
            self.last_command = {"id": command, "at": 0, "shown": shown}
            self.recent.append({"heard": text, "outcome": "command", "command": command})
            self.show(shown, 2.5)
        elif named:
            self.counts["notUnderstood"] += 1
            self.window_until = now + 6
            self.show("Didn\u2019t catch that", 6)

    def carry_out(self, command):
        if command == "sleep":
            self.sleeping = "deaf" not in self.faults
            return "Sleeping"
        was_sleeping = self.sleeping
        self.sleeping = False
        if command == "wake" or was_sleeping:
            return "Awake"
        step = 40 if command == "brighter" else -40
        self.wake_brightness = max(15, min(255, self.wake_brightness + step))
        return "Brighter" if step > 0 else "Dimmer"

    def show(self, caption, seconds):
        self.caption = "" if "no-caption" in self.faults else caption
        self.caption_until = self.clock.now + seconds

    def captioned(self):
        return bool(self.caption) and self.clock.now <= self.caption_until and not self.sleeping

    def capture(self):
        return glass(dashboard=not self.sleeping, caption=self.captioned())

    def shell(self, *arguments, **_options):
        self.adb.commands.append(("shell", *arguments))
        if arguments == ("ps",):
            lines = ["USER PID PPID VSIZE RSS WCHAN PC NAME", f"u0_a55 2604 1 1 1 0 0 S {validate.PACKAGE}"]
            if self.process_running():
                lines.append(f"u0_a55 {self.pid} 1 1 1 0 0 S {validate.VOICE_PROCESS}")
            return "\n".join(lines)
        if arguments[0] == "kill":
            if "dashboard-goes-too" in self.faults:
                self.run += 1
            if "stays-down" in self.faults:
                self.down = True
            else:
                self.pid += 1
                self.restarts += 1
        elif arguments[:3] == ("run-as", validate.PACKAGE, "ls"):
            return "planted.xml\n" if self.planted else "preferences.xml\n"
        elif arguments[:2] == ("pm", "revoke"):
            self.permission = False
            self.run += 1
        elif arguments[:2] == ("pm", "grant"):
            self.permission = "never-notices-the-grant" not in self.faults
        elif arguments[:2] == ("pm", "install-create"):
            if "no-installations" in self.faults:
                return "Error: java.lang.SecurityException"
            self.installing = True
            return "Success: created install session [1234]"
        elif arguments[:2] == ("pm", "install-abandon"):
            self.installing = "stays-away" in self.faults
            self.pid += 1
            if "counts-stepping-aside" in self.faults:
                self.restarts += 1
            if "dashboard-goes-too" in self.faults:
                self.run += 1
        elif arguments[0] == "cat":
            return f'<node text="{self.caption if self.captioned() else ""}" bounds="[0,0][1,1]" />'
        return ""


class VoiceVerdictTest(unittest.TestCase):
    NEED_LISTENING = (
        validate.check_voice_listens, validate.check_voice_commands, validate.check_voice_wake_word,
        validate.check_voice_recovers, validate.check_voice_steps_aside, validate.check_voice_permission,
        validate.check_voice_returns,
    )

    def run_check(self, check, *faults, **options):
        mirror = SpeakingMirror(self, *faults, **{k: v for k, v in options.items() if k == "test_hooks"})
        with tempfile.TemporaryDirectory() as directory:
            ctx = mirror.context(directory, model=options.get("model", True))
            if "prepare" in options:
                options["prepare"](mirror, ctx)
            check(ctx)
            return mirror, ctx

    def fails(self, check, fault, message, **options):
        with self.assertRaisesRegex(CheckFailed, message):
            self.run_check(check, fault, **options)

    def test_every_check_passes_on_a_build_that_behaves(self):
        for name, _, check, _ in validate.EMULATOR_CHECKS:
            if name.startswith("voice-"):
                with self.subTest(name):
                    self.run_check(check)

    def test_voice_is_off_until_switched_on_and_is_left_off(self):
        mirror, ctx = self.run_check(validate.check_voice_off)
        self.assertFalse(mirror.enabled)
        self.assertEqual("mirror go to sleep", ctx.details["canBeSaid"][0])
        self.fails(validate.check_voice_off, "on-from-the-start", "Voice is no-model before anyone switched it on")
        self.fails(validate.check_voice_off, "open-to-all", "Voice can be read without a pairing")
        self.fails(validate.check_voice_off, "hears-while-off", "A sentence was taken while nothing listens")

    def test_a_release_build_is_not_handed_a_sentence_while_off(self):
        mirror, _ = self.run_check(validate.check_voice_off, test_hooks=False)
        self.assertNotIn("/api/v1/voice/test/sentence", [path for _, path, _ in mirror.api.calls])

    def test_what_is_no_model_must_be_refused_and_leave_nothing_behind(self):
        mirror, ctx = self.run_check(validate.check_voice_model)
        self.assertEqual("The speech model could not be loaded", ctx.details["unloadableModel"])
        self.assertIsNone(mirror.model)
        self.assertFalse(mirror.enabled)
        self.fails(validate.check_voice_model, "trusts-any-checksum", "not what its checksum says was answered 201, not 400")
        self.fails(validate.check_voice_model, "follows-paths", "wrote a file outside the model's folder")
        self.fails(validate.check_voice_model, "crashes-on-a-bad-model", "did not carry on over a speech model that cannot be loaded")
        self.fails(validate.check_voice_model, "model-stays", "After removal")

    def test_a_model_that_cannot_be_loaded_is_switched_off_and_removed_even_when_the_check_fails(self):
        mirror = SpeakingMirror(self, "crashes-on-a-bad-model")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CheckFailed):
                validate.check_voice_model(mirror.context(directory))
        self.assertFalse(mirror.enabled)
        self.assertIsNone(mirror.model)

    def test_listening_installs_the_model_with_its_checksum_and_switches_voice_on(self):
        mirror, ctx = self.run_check(validate.check_voice_listens)
        self.assertEqual("vosk-model-test", ctx.details["model"])
        self.assertEqual(120_000, ctx.details["recogniserPssKb"])
        self.assertTrue(mirror.enabled and ctx.voice_left_on)
        self.assertEqual(mirror.model["sha256"], ctx.voice_checksum)
        uploads = [call for call in mirror.api.calls if call[:2] == ("PUT", "/api/v1/voice/model")]
        self.assertEqual(1, len(uploads))
        self.fails(validate.check_voice_listens, "never-listens", "Voice was not listening within 120 s; it is starting")
        self.fails(validate.check_voice_listens, "heavy", "The recogniser's process holds 300000 KB")

    def test_a_model_that_is_installed_already_is_not_sent_again(self):
        mirror = SpeakingMirror(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = mirror.context(directory)
            validate.check_voice_listens(ctx)
            validate.check_voice_recovers(ctx)
        uploads = [call for call in mirror.api.calls if call[:2] == ("PUT", "/api/v1/voice/model")]
        self.assertEqual(1, len(uploads))

    def test_checks_that_need_the_recogniser_skip_without_a_model(self):
        for check in self.NEED_LISTENING:
            with self.subTest(check.__name__):
                with self.assertRaisesRegex(CheckSkipped, "no speech model on this computer"):
                    self.run_check(check, model=False)

    def test_a_release_build_is_checked_as_far_as_it_can_be_without_speaking_to_it(self):
        for check in (validate.check_voice_commands, validate.check_voice_wake_word):
            with self.subTest(check.__name__):
                with self.assertRaisesRegex(CheckSkipped, "a release build cannot be given test speech"):
                    self.run_check(check, test_hooks=False)
        for check in (validate.check_voice_listens, validate.check_voice_recovers,
                      validate.check_voice_steps_aside, validate.check_voice_permission,
                      validate.check_voice_returns):
            with self.subTest(check.__name__):
                mirror, _ = self.run_check(check, test_hooks=False)
                spoken = [path for _, path, _ in mirror.api.calls if path.startswith("/api/v1/voice/test/")]
                self.assertEqual([], spoken)
        self.fails(validate.check_voice_recovers, "stays-down", "waiting for the recogniser to come back",
                   test_hooks=False)
        self.fails(validate.check_voice_returns, "process-lingers", "process to end once voice is off",
                   test_hooks=False)

    def test_spoken_commands_act_and_talk_does_not(self):
        mirror, ctx = self.run_check(validate.check_voice_commands)
        self.assertEqual(["mirror go to sleep", "mirror wake up"], ctx.details["heard"])
        self.assertEqual(4, ctx.details["sentences"])
        self.assertFalse(mirror.sleeping)
        self.fails(validate.check_voice_commands, "deaf", "waiting for the Mirror to sleep when told to")
        self.fails(validate.check_voice_commands, "takes-talk-for-commands", "Talk that holds a command's words was taken for a command")

    def test_a_mirror_left_asleep_by_a_failed_check_is_woken(self):
        mirror = SpeakingMirror(self, "takes-talk-for-commands")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CheckFailed):
                validate.check_voice_commands(mirror.context(directory))
        self.assertFalse(mirror.sleeping)

    def test_only_what_follows_the_name_for_sure_counts(self):
        mirror, ctx = self.run_check(validate.check_voice_wake_word)
        self.assertEqual({"commands": 4, "wakeWords": 3, "notUnderstood": 1, "unsure": 1}, ctx.details["counted"])
        self.assertEqual(180, mirror.wake_brightness)
        self.fails(validate.check_voice_wake_word, "needs-no-name", "A command without the Mirror's name was carried out")
        self.fails(validate.check_voice_wake_word, "no-caption", "The glass did not show that the Mirror listens")
        self.fails(validate.check_voice_wake_word, "acts-when-unsure", "A command the recogniser was unsure of was carried out")
        self.fails(validate.check_voice_wake_word, "window-never-closes", "carried out long after the Mirror's name")

    def test_the_caption_of_the_command_before_is_not_taken_for_the_next(self):
        # "Awake" is still on the glass when the name is said, unless the check waits.
        mirror = SpeakingMirror(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = mirror.context(directory)
            validate.check_voice_wake_word(ctx)
            self.assertTrue((pathlib.Path(directory) / "voice-listening.png").is_file())

    def test_a_stopped_recogniser_must_return_alone(self):
        mirror, ctx = self.run_check(validate.check_voice_recovers)
        self.assertEqual((4100, 4101), (ctx.details["stoppedProcess"], ctx.details["newProcess"]))
        self.assertIn(("shell", "kill", "4100"), mirror.adb.commands)
        self.fails(validate.check_voice_recovers, "stays-down", "waiting for the recogniser to come back")
        self.fails(validate.check_voice_recovers, "dashboard-goes-too", "did not carry on when its recogniser stopped")

    def test_voice_steps_aside_while_an_app_is_installed(self):
        mirror, ctx = self.run_check(validate.check_voice_steps_aside)
        self.assertEqual(1234, ctx.details["installation"])
        self.assertFalse(mirror.installing)
        self.assertEqual(0, mirror.restarts)
        self.fails(validate.check_voice_steps_aside, "no-installations", "Android began no installation: Error")
        self.fails(validate.check_voice_steps_aside, "listens-through-installations",
                   "Voice was not paused within 20 s; it is listening")
        self.fails(validate.check_voice_steps_aside, "keeps-its-memory", "process to end while an app is installed")
        self.fails(validate.check_voice_steps_aside, "stays-away", "Voice was not listening within 120 s; it is paused")
        self.fails(validate.check_voice_steps_aside, "counts-stepping-aside", "counted as a recogniser that stopped")
        self.fails(validate.check_voice_steps_aside, "dashboard-goes-too", "did not carry on while its recogniser stepped aside")

    def test_the_installation_is_given_up_when_the_check_fails(self):
        mirror = SpeakingMirror(self, "listens-through-installations")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CheckFailed):
                validate.check_voice_steps_aside(mirror.context(directory))
        self.assertEqual(("shell", "pm", "install-abandon", "1234"), mirror.adb.commands[-1])

    def test_voice_waits_for_the_microphone_permission(self):
        mirror, _ = self.run_check(validate.check_voice_permission)
        self.assertTrue(mirror.permission)
        self.fails(validate.check_voice_permission, "runs-unpermitted", "runs without the microphone permission")
        self.fails(validate.check_voice_permission, "never-notices-the-grant", "it is no-permission")

    def test_the_permission_is_given_back_when_the_check_fails(self):
        mirror = SpeakingMirror(self, "runs-unpermitted")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CheckFailed):
                validate.check_voice_permission(mirror.context(directory))
        self.assertEqual(("shell", "pm", "grant", validate.PACKAGE, validate.RECORD_AUDIO), mirror.adb.commands[-1])

    def test_after_the_restarts_voice_must_still_be_on_and_end_switched_off(self):
        mirror, ctx = self.run_check(validate.check_voice_returns)
        self.assertFalse(mirror.enabled or ctx.voice_left_on)
        self.assertIsNone(mirror.model)
        self.fails(validate.check_voice_returns, "process-lingers", "process to end once voice is off")

        def switched_on_earlier(_mirror, ctx):
            ctx.voice_left_on = True

        with self.assertRaisesRegex(CheckFailed, "switched on with a speech model before the restarts, and is not after them: off"):
            self.run_check(validate.check_voice_returns, prepare=switched_on_earlier)

    def test_the_voice_checks_come_before_the_restarts_and_the_last_after_them(self):
        names = [name for name, _, _, _ in validate.EMULATOR_CHECKS]
        voice_checks = [name for name in names if name.startswith("voice-")]
        self.assertEqual(
            ["voice-off", "voice-model", "voice-listens", "voice-commands", "voice-wake-word",
             "voice-recovers", "voice-steps-aside", "voice-permission", "voice-returns"],
            voice_checks,
        )
        self.assertLess(names.index("voice-permission"), names.index("restart"))
        self.assertGreater(names.index("voice-returns"), names.index("reboot"))
        self.assertEqual("voice-off", validate.UPGRADE_CHECKS[-1][0])

    def test_the_clips_are_sixteen_kilohertz_mono_as_the_recogniser_takes_them(self):
        import wave

        for name in CLIP_SENTENCES:
            with wave.open(str(validate.VOICE_CLIPS / name), "rb") as clip:
                self.assertEqual((1, 2, 16_000), (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()), name)
                self.assertLess(clip.getnframes() / clip.getframerate(), 4, name)


class SpeechModelTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.kept = pathlib.Path(self.directory.name) / "kept-model.zip"
        patcher = mock.patch.object(validate.voice, "DEFAULT_MODEL", self.kept)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_model_that_is_named_must_be_there(self):
        named = pathlib.Path(self.directory.name) / "named.zip"
        with self.assertRaisesRegex(CheckFailed, "Speech model not found"):
            validate.speech_model(named)
        named.write_bytes(b"any model")
        self.assertEqual(named, validate.speech_model(named))

    def test_without_one_the_checks_that_need_it_are_skipped_and_the_run_says_so(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(validate.speech_model(None))
        self.assertIn("python tools/voice.py fetch-model", output.getvalue())

    def test_the_kept_model_is_used_only_if_it_is_the_known_one(self):
        self.kept.write_bytes(b"the model")
        with self.assertRaisesRegex(CheckFailed, "is not the speech model this suite knows"):
            validate.speech_model(None)
        with mock.patch.object(validate.voice, "MODEL_SHA256", hashlib.sha256(b"the model").hexdigest()):
            self.assertEqual(self.kept, validate.speech_model(None))

    def test_a_missing_model_stops_the_run_before_anything_is_built_or_started(self):
        with mock.patch.object(validate.android_emulator, "Emulator") as emulator, \
                mock.patch.object(validate, "build_debug_apk") as build, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(1, validate.main(["emulator", "--voice-model", str(self.kept)]))
        emulator.assert_not_called()
        build.assert_not_called()
        self.assertIn("validate: Speech model not found", errors.getvalue())

    def test_a_model_archive_holds_the_files_it_is_given(self):
        archive = zipfile.ZipFile(io.BytesIO(validate.model_archive({"m/am/final.mdl": b"a", "m/conf/x.conf": b"b"})))
        self.assertEqual(["m/am/final.mdl", "m/conf/x.conf"], archive.namelist())
        self.assertEqual(b"b", archive.read("m/conf/x.conf"))


class NoteTextTest(unittest.TestCase):
    def mirror(self, *, garbles=False):
        notes = []
        sent = []

        def call(method, path, body=None, **options):
            sent.append((method, path, options))
            if (method, path) == ("POST", "/api/v1/notes"):
                text = json.loads(options["data"].decode("ascii" if garbles else "utf-8", errors="replace"))["text"]
                notes.append({"id": "n1", "text": text})
                return Reply(201, {"note": notes[-1]}, {})
            if (method, path) == ("GET", "/api/v1/notes"):
                return Reply(200, {"notes": list(notes)}, {})
            if (method, path) == ("DELETE", "/api/v1/notes/n1"):
                notes.clear()
                return Reply(200, {}, {})
            raise AssertionError(f"Unexpected {method} {path}")

        api = FakeApi()
        api.call = call
        return api, notes, sent

    def test_a_note_is_sent_as_the_controls_send_it_and_must_come_back_unchanged(self):
        api, notes, sent = self.mirror()
        with tempfile.TemporaryDirectory() as directory:
            validate.check_note_text(validate.Context(FakeAdb([frame(255)]), api, pathlib.Path(directory)))
        self.assertEqual([], notes)
        self.assertEqual("application/json", sent[0][2]["content_type"])
        self.assertIn("\u00e9".encode("utf-8"), sent[0][2]["data"])

    def test_a_build_that_reads_the_note_as_ascii_fails_and_the_note_is_deleted(self):
        api, notes, _ = self.mirror(garbles=True)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CheckFailed, "The note was answered as"):
                validate.check_note_text(validate.Context(FakeAdb([frame(255)]), api, pathlib.Path(directory)))
        self.assertEqual([], notes)


class ScriptErrorsTest(unittest.TestCase):
    ERROR = {"message": "Uncaught TypeError: undefined is not a function", "source": "custom.js", "line": 61}

    def context(self, directory, *, errors=0, recent=()):
        self.report = healthy_report()
        self.report["dashboard"].update(consoleErrors=errors, recentConsoleErrors=list(recent))
        api = FakeApi({("GET", "/api/v1/health"): lambda _body: self.report})
        return validate.Context(FakeAdb([frame(255)]), api, pathlib.Path(directory))

    def test_a_run_without_script_errors_passes_and_keeps_the_last_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            ctx.restart_home()
            validate.check_script_errors(ctx)
            self.assertEqual([], ctx.details["scriptErrors"])
            self.assertTrue((pathlib.Path(directory) / "final.png").is_file())

    def test_an_error_that_a_restarted_process_took_along_is_still_counted_and_named(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory, errors=1, recent=[self.ERROR])
            ctx.restart_home()
            self.assertIn(("shell", "am", "force-stop", validate.PACKAGE), ctx.adb.commands)
            # The process that Android starts in its place knows of no error.
            self.report = healthy_report()
            with self.assertRaisesRegex(CheckFailed, "logged 1 script errors during the run: .*Uncaught TypeError"):
                validate.check_script_errors(ctx)

    def test_errors_of_every_process_of_the_run_add_up(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory, errors=2, recent=[self.ERROR, self.ERROR])
            ctx.keep_console_errors(self.report)
            self.report = healthy_report()
            self.report["dashboard"].update(consoleErrors=1, recentConsoleErrors=[dict(self.ERROR, line=7)])
            with self.assertRaisesRegex(CheckFailed, "logged 3 script errors"):
                validate.check_script_errors(ctx)
            self.assertEqual([61, 61, 7], [error["line"] for error in ctx.details["scriptErrors"]])


class SystemDialogTest(unittest.TestCase):
    CRASHED = (
        "  mCurrentFocus=Window{2f0a1b u0 Application Error: com.android.carrierconfig}\n"
        "  mFocusedApp=AppWindowToken{a2eeac2 token=Token{773b86a}}\n"
    )
    HOME = "  mCurrentFocus=Window{713493b u0 dev.mirror.repurpose/dev.mirror.repurpose.MainActivity}\n"
    DIALOG = (
        '<hierarchy><node text="Unfortunately, com.android.carrierconfig has stopped." bounds="[180,900][900,960]" />'
        '<node index="0" text="OK" resource-id="android:id/button1" class="android.widget.Button" '
        'package="android" bounds="[780,990][900,1062]" /></hierarchy>'
    )

    class Adb(FakeAdb):
        def __init__(self, windows, hierarchy):
            super().__init__([frame(255)])
            self.windows = list(windows)
            self.hierarchy = hierarchy

        def shell(self, *arguments, **_options):
            self.commands.append(("shell", *arguments))
            if arguments[:2] == ("dumpsys", "window"):
                return self.windows.pop(0) if len(self.windows) > 1 else self.windows[0]
            if arguments[0] == "cat":
                return self.hierarchy
            return ""

    def context(self, adb, directory):
        return validate.Context(adb, FakeApi(), pathlib.Path(directory))

    def taps(self, adb):
        return [command for command in adb.commands if command[:3] == ("shell", "input", "tap")]

    def test_closes_the_dialog_of_one_of_the_emulators_own_apps(self):
        fake_time(self)
        adb = self.Adb([self.CRASHED, self.HOME], self.DIALOG)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as printed:
            ctx = self.context(adb, directory)
            ctx.before_check()
        self.assertEqual([("shell", "input", "tap", "840", "1026")], self.taps(adb))
        self.assertEqual(["Application Error: com.android.carrierconfig"], ctx.dismissed)
        self.assertIn("closed the emulator's own dialog", printed.getvalue())

    def test_leaves_the_dashboard_alone(self):
        adb = self.Adb([self.HOME], self.DIALOG)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(adb, directory)
            ctx.before_check()
        self.assertEqual([], self.taps(adb))
        self.assertEqual([], ctx.dismissed)

    def test_never_closes_a_dialog_about_mirror_home(self):
        crashed = "  mCurrentFocus=Window{2f0a1b u0 Application Error: dev.mirror.repurpose}\n"
        adb = self.Adb([crashed], self.DIALOG)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(adb, directory)
            ctx.before_check()
        self.assertEqual([], self.taps(adb))
        self.assertEqual([], ctx.dismissed)

    def test_gives_up_on_a_dialog_that_will_not_close(self):
        fake_time(self)
        adb = self.Adb([self.CRASHED], self.DIALOG)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            ctx = self.context(adb, directory)
            ctx.before_check()
        self.assertEqual(4, len(self.taps(adb)))

    def test_every_check_starts_with_a_clear_screen(self):
        order = []

        class Context:
            details = {}

            def before_check(self):
                order.append("clear")

        checks = [("one", "One", lambda context: order.append("one"), False),
                  ("two", "Two", lambda context: order.append("two"), False)]
        with contextlib.redirect_stdout(io.StringIO()):
            validate.run_checks(checks, Context())
        self.assertEqual(["clear", "one", "clear", "two"], order)


class WindowTest(unittest.TestCase):
    FOCUS = (
        "  mCurrentFocus=Window{{713493b u0 {window}}}\n"
        "  mFocusedApp=AppWindowToken{{a2eeac2 token=Token{{773b86a}}}}\n"
    )

    def adb(self, output):
        adb = FakeAdb()
        adb.shell = lambda *arguments, **_options: output
        return adb

    def test_names_the_component_or_system_window_in_front(self):
        for window in (
            "dev.mirror.repurpose/dev.mirror.repurpose.MainActivity",
            "android/com.android.internal.app.ResolverActivity",
            "Application Error: com.android.carrierconfig",
        ):
            self.assertEqual(window, validate.focused_window(self.adb(self.FOCUS.format(window=window))))

    def test_nothing_in_front_is_an_empty_name(self):
        self.assertEqual("", validate.focused_window(self.adb("  mCurrentFocus=null\n")))
        self.assertEqual("", validate.focused_window(self.adb("")))

    def test_finds_the_middle_of_a_view_by_its_text_or_identifier(self):
        hierarchy = SelectHomeTest.CHOOSER
        self.assertEqual((364, 1772), validate.node_center(hierarchy, "text", "Mirror Home"))
        self.assertEqual((824, 1867), validate.node_center(hierarchy, "resource-id", "android:id/button_always"))
        self.assertIsNone(validate.node_center(hierarchy, "text", "Mirror"))
        self.assertIsNone(validate.node_center("", "text", "Mirror Home"))


class SelectHomeTest(unittest.TestCase):
    """What Android 6 shows when asked for HOME with two HOME apps installed."""

    CHOOSER = (
        '<hierarchy rotation="0">'
        '<node index="0" text="Select a Home app" resource-id="android:id/title" class="android.widget.TextView" '
        'package="android" content-desc="" bounds="[180,1563][900,1647]" />'
        '<node index="0" text="Launcher3" resource-id="android:id/text1" class="android.widget.TextView" '
        'package="android" content-desc="" bounds="[288,1670][415,1707]" />'
        '<node index="0" text="Mirror Home" resource-id="android:id/text1" class="android.widget.TextView" '
        'package="android" content-desc="" bounds="[288,1754][440,1791]" />'
        '<node index="0" text="Just once" resource-id="android:id/button_once" class="android.widget.Button" '
        'package="android" content-desc="" bounds="[617,1827][766,1908]" />'
        '<node index="1" text="Always" resource-id="android:id/button_always" class="android.widget.Button" '
        'package="android" content-desc="" bounds="[766,1827][882,1908]" />'
        '</hierarchy>'
    )
    CHOOSING = "android/com.android.internal.app.ResolverActivity"
    DASHBOARD = "dev.mirror.repurpose/dev.mirror.repurpose.MainActivity"
    LAUNCHER = "com.android.launcher3/com.android.launcher3.Launcher"

    class Adb(FakeAdb):
        def __init__(self, focus, hierarchy=""):
            super().__init__()
            self.focus = list(focus)
            self.hierarchy = hierarchy

        def shell(self, *arguments, **_options):
            self.commands.append(("shell", *arguments))
            if arguments[:2] == ("dumpsys", "window"):
                focus = self.focus.pop(0) if len(self.focus) > 1 else self.focus[0]
                return WindowTest.FOCUS.format(window=focus)
            if arguments[0] == "cat":
                return self.hierarchy
            return ""

    def issued(self, adb, *prefix):
        return [command[1:] for command in adb.commands if command[1:len(prefix) + 1] == prefix]

    def test_chooses_mirror_home_and_always_as_an_owner_does(self):
        fake_time(self)
        adb = self.Adb([self.CHOOSING, self.DASHBOARD], self.CHOOSER)
        validate.select_home(adb)
        self.assertEqual(
            [("input", "tap", "364", "1772"), ("input", "tap", "824", "1867")],
            self.issued(adb, "input", "tap"),
        )
        self.assertEqual(2, len(self.issued(adb, *validate.HOME_REQUEST)))
        # A Mirror's factory launcher is installed but not running.
        self.assertEqual(("shell", "am", "force-stop", "com.android.launcher3"), adb.commands[-1])

    def test_an_emulator_where_mirror_home_already_is_home_is_left_as_it_is(self):
        fake_time(self)
        adb = self.Adb([self.DASHBOARD])
        validate.select_home(adb)
        self.assertEqual([], self.issued(adb, "input", "tap"))
        self.assertEqual(1, len(self.issued(adb, *validate.HOME_REQUEST)))

    def test_a_home_request_never_starts_mirror_home_by_its_component(self):
        fake_time(self)
        adb = self.Adb([self.CHOOSING, self.DASHBOARD], self.CHOOSER)
        validate.select_home(adb)
        self.assertEqual([], [command for command in adb.commands if "-n" in command])

    def test_says_what_is_in_front_when_mirror_home_cannot_become_home(self):
        fake_time(self)
        adb = self.Adb([self.LAUNCHER])
        with self.assertRaisesRegex(
            CheckFailed, "could not be made the emulator's HOME app; com.android.launcher3/.* is in front"
        ):
            validate.select_home(adb)
        self.assertEqual(4, len(self.issued(adb, *validate.HOME_REQUEST)))


class PrepareDeviceTest(unittest.TestCase):
    def test_gives_the_emulator_a_mirrors_power_settings_and_unlocks_it(self):
        adb = FakeAdb()
        validate.prepare_device(adb)
        self.assertEqual(
            [
                ("shell", "settings", "put", "secure", "immersive_mode_confirmations", "confirmed"),
                # Android never sees mains power on a Mirror, so nothing but a
                # window, or ten minutes without one, decides about its display.
                ("shell", "settings", "put", "global", "stay_on_while_plugged_in", "0"),
                ("shell", "settings", "put", "system", "screen_off_timeout", "600000"),
                ("shell", "input", "keyevent", "224"),
                ("shell", "input", "keyevent", "82"),
            ],
            adb.commands,
        )


class EmulatorHealthTest(unittest.TestCase):
    def report(self):
        report = healthy_report()
        report["debuggable"] = False
        report["device"].update(sdk=23)
        report["device"]["display"].update(
            realWidthPixels=1080, realHeightPixels=1920, widthPixels=1080, heightPixels=1920
        )
        report["clock"]["bundledTzdata"] = "2026d"
        report["otaSupervisor"] = {"installed": False}
        return report

    def check(self, report):
        api = FakeApi({
            ("GET", "/api/v1/clients"): Reply(401, {"error": "Unauthorized"}, {}),
            ("GET", "/api/v1/health"): report,
        })
        with tempfile.TemporaryDirectory() as directory:
            ctx = validate.Context(FakeAdb(), api, pathlib.Path(directory))
            validate.check_health(ctx)
            return ctx

    def test_an_emulator_shaped_like_a_mirror_passes(self):
        ctx = self.check(self.report())
        self.assertEqual(240, ctx.details["densityDpi"])
        self.assertEqual(131_072, ctx.details["javaHeapMaxKb"])
        self.assertIs(False, ctx.details["power"]["usbConfigured"])

    def test_an_emulator_that_differs_from_a_mirror_is_refused(self):
        faults = {
            "Mirror Home is not the HOME app Android would start": lambda report: report["activity"].update(
                selectedHome=False
            ),
            "has input devices, which a Mirror does not": lambda report: report["device"]["input"].update(
                touchscreen=True
            ),
            "The display is reported off": lambda report: report["device"]["display"].update(on=False),
            "The dashboard is not in front": lambda report: report["activity"].update(showing=False),
            # What the emulator gives a panel this size unless it is told otherwise.
            "gives an app a 256 MB heap, not a Mirror's 128 MB": lambda report: report["memory"].update(
                javaHeapMaxKb=262_144
            ),
        }
        for message, break_it in faults.items():
            report = self.report()
            break_it(report)
            with self.assertRaisesRegex(CheckFailed, message):
                self.check(report)


class Glass:
    """An emulator as the foreground checks see it: what is in front of the
    dashboard, whether Android sleeps, and what Mirror Home does about either."""

    DASHBOARD = "dev.mirror.repurpose/dev.mirror.repurpose.MainActivity"
    LAUNCHER = "com.android.launcher3"

    def __init__(
        self, clock, *, returns_after=13, wakes_after=3, attended=False, stock_launcher=True,
        root=True, by_keeper=True, duplicates=False, keeps_focus=False, wakefulness=None,
    ):
        self.clock = clock
        self.returns_after = returns_after
        self.wakes_after = wakes_after
        self.attended = attended
        self.stock_launcher = stock_launcher
        self.root = root
        self.by_keeper = by_keeper
        self.duplicates = duplicates
        self.keeps_focus = keeps_focus
        self.wakefulness = wakefulness
        self.running = True
        self.front = ""
        self.awake = True
        self.since = 0.0
        self.creates = 1
        self.pauses = 0
        self.relaunches = 0
        self.wake_ups = 0
        self.reason = ""
        self.last_front = ""
        self.locked = False
        self.api = FakeApi()
        self.api.call = self.call
        self.adb = FakeAdb([frame(255)])
        self.adb.shell = self.shell

    def context(self, directory):
        ctx = validate.Context(self.adb, self.api, pathlib.Path(directory))
        ctx.awake_peak = 255
        return ctx

    def issued(self, *command):
        return [issued for issued in self.adb.commands if issued[1:len(command) + 1] == command]

    def cover(self, package):
        self.front = package
        self.since = self.clock.now
        self.pauses += 1

    def shell(self, *arguments, **_options):
        self.adb.commands.append(("shell", *arguments))
        if arguments == ("pm", "path", self.LAUNCHER):
            return "package:/system/priv-app/Launcher3/Launcher3.apk\n" if self.stock_launcher else ""
        if arguments == (*validate.HOME_REQUEST, self.LAUNCHER):
            self.cover(self.LAUNCHER)
        elif arguments == ("am", "start", "-a", "android.settings.SETTINGS"):
            self.cover("com.android.settings")
        elif arguments == ("am", "force-stop", validate.PACKAGE):
            self.running = False
        elif arguments[:2] == ("am", "force-stop") and arguments[2] == self.front:
            self.front = ""
        elif arguments == ("am", "start", "-n", validate.ACTIVITY):
            # A new process: its dashboard is an ordinary task in front.
            self.running, self.front = True, ""
            self.creates, self.pauses, self.relaunches, self.reason = 1, 0, 0, ""
        elif arguments[:2] == ("dumpsys", "window"):
            front = self.LAUNCHER if self.keeps_focus else self.front
            window = f"{front}/{front}.Launcher" if front else self.DASHBOARD
            return WindowTest.FOCUS.format(window=window)
        elif arguments[:2] == ("dumpsys", "power"):
            return f"  mWakefulness={self.wakefulness or ('Awake' if self.awake else 'Asleep')}\n"
        elif arguments == ("input", "keyevent", validate.KEY_SLEEP):
            self.awake = False
            self.since = self.clock.now
            self.pauses += 1
        elif arguments == ("input", "keyevent", validate.KEY_WAKEUP):
            self.awake = True
        elif "/sys/power/wake_lock" in arguments[0]:
            self.locked = self.root
            return f"{validate.KERNEL_WAKE_LOCK}\n" if self.root else ""
        elif "/sys/power/wake_unlock" in arguments[0]:
            self.locked = False
        return ""

    def keep(self):
        """What Mirror Home has done by now, on a device nobody attends."""
        waited = self.clock.now - self.since
        if self.attended or not self.running:
            return
        if not self.awake and self.wakes_after is not None and waited >= self.wakes_after:
            self.awake = True
            self.wake_ups += 1
            self.reason = "asleep"
        if self.awake and self.front and self.returns_after is not None and waited >= self.returns_after:
            self.last_front = f"{self.front}/.Launcher, dev.mirror.repurpose/.MainActivity"
            self.front = ""
            if self.by_keeper:
                self.relaunches += 1
                self.reason = "covered"
            if self.duplicates:
                self.creates += 1

    def call(self, method, path, body=None, **_options):
        self.api.calls.append((method, path, body))
        if (method, path) != ("GET", "/api/v1/health"):
            raise AssertionError(f"Unexpected {method} {path}")
        if not self.running:
            raise ConnectionRefusedError("Mirror Home is not running")
        self.keep()
        showing = self.awake and not self.front
        report = healthy_report()
        report["activity"].update(
            showing=showing, resumed=showing, creates=self.creates, pauses=self.pauses,
        )
        report["activity"]["recovery"].update(
            attended=self.attended, relaunches=self.relaunches, wakeUps=self.wake_ups,
            lastReason=self.reason, lastFront=self.last_front,
        )
        report["device"]["power"]["interactive"] = self.awake
        report["device"]["display"]["on"] = self.awake
        return Reply(200, report, {})


class ReturnsToFrontTest(unittest.TestCase):
    def run_check(self, **behaviour):
        glass = Glass(fake_time(self), **behaviour)
        with tempfile.TemporaryDirectory() as directory:
            ctx = glass.context(directory)
            try:
                validate.check_returns_to_front(ctx)
            finally:
                self.evidence = sorted(path.name for path in pathlib.Path(directory).iterdir())
        return glass, ctx

    def test_the_dashboard_returns_from_under_the_other_home_app_both_ways(self):
        glass, ctx = self.run_check()
        self.assertGreaterEqual(ctx.details["returnedAfterSeconds"], validate.COVERED_GRACE_SECONDS)
        self.assertGreaterEqual(ctx.details["returnedAfterUpdateSeconds"], validate.COVERED_GRACE_SECONDS)
        self.assertEqual("com.android.launcher3", ctx.details["coveredBy"])
        self.assertIn("com.android.launcher3/.Launcher", ctx.details["lastFront"])
        self.assertIn("returns-to-front-covered.png", self.evidence)
        self.assertIn("returns-after-update-covered.png", self.evidence)

    def test_the_second_time_the_tasks_are_arranged_as_an_update_leaves_them(self):
        glass, _ = self.run_check()
        covers = [index for index, command in enumerate(glass.adb.commands)
                  if command[1:] == (*validate.HOME_REQUEST, Glass.LAUNCHER)]
        stopped = glass.adb.commands.index(("shell", "am", "force-stop", validate.PACKAGE))
        started = glass.adb.commands.index(("shell", "am", "start", "-n", validate.ACTIVITY))
        # The other HOME app comes up, Mirror Home goes and starts its own
        # dashboard, and only then does the launcher's next screen cover it.
        self.assertEqual(3, len(covers))
        self.assertTrue(covers[1] < stopped < started < covers[2])

    def test_the_other_home_app_is_stopped_again_as_it_is_on_a_mirror(self):
        glass, _ = self.run_check()
        self.assertEqual(2, len(glass.issued("am", "force-stop", Glass.LAUNCHER)))
        self.assertEqual("", glass.front)

    def test_an_attended_emulator_skips_before_anything_is_covered(self):
        with self.assertRaisesRegex(CheckSkipped, "leaves another screen alone"):
            self.run_check(attended=True)

    def test_a_build_that_leaves_the_other_screen_in_front_fails_and_is_uncovered(self):
        glass = Glass(fake_time(self), returns_after=None)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CheckFailed, "waiting for the dashboard to return to the front by itself"):
                validate.check_returns_to_front(glass.context(directory))
        self.assertEqual(1, len(glass.issued("am", "force-stop", Glass.LAUNCHER)))
        self.assertEqual("", glass.front)

    def test_a_screen_that_only_passes_by_must_be_given_a_moment(self):
        with self.assertRaisesRegex(CheckFailed, "took the display back after 3 s"):
            self.run_check(returns_after=3)

    def test_a_cover_that_closes_itself_proves_nothing(self):
        with self.assertRaisesRegex(CheckFailed, "not because Mirror Home brought it back"):
            self.run_check(by_keeper=False)

    def test_a_second_dashboard_is_not_a_return(self):
        with self.assertRaisesRegex(CheckFailed, "A second dashboard was created"):
            self.run_check(duplicates=True)

    def test_android_must_agree_that_the_dashboard_has_the_focus(self):
        with self.assertRaisesRegex(CheckFailed, "Android gives the focus to com.android.launcher3/"):
            self.run_check(keeps_focus=True)

    def test_an_emulator_without_another_home_app_is_covered_by_settings_once(self):
        glass, ctx = self.run_check(stock_launcher=False)
        self.assertEqual("com.android.settings", ctx.details["coveredBy"])
        self.assertNotIn("returnedAfterUpdateSeconds", ctx.details)
        self.assertEqual([], glass.issued("am", "force-stop", validate.PACKAGE))


class WakesDisplayTest(unittest.TestCase):
    def run_check(self, **behaviour):
        glass = Glass(fake_time(self), **behaviour)
        with tempfile.TemporaryDirectory() as directory:
            ctx = glass.context(directory)
            validate.check_wakes_display(ctx)
        return glass, ctx

    def failing(self, kind, message, **behaviour):
        glass = Glass(fake_time(self), **behaviour)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(kind, message):
                validate.check_wakes_display(glass.context(directory))
        return glass

    def test_mirror_home_wakes_a_display_that_android_put_to_sleep(self):
        glass, ctx = self.run_check()
        self.assertEqual(1, ctx.details["wakeUps"])
        self.assertGreater(ctx.details["wokeAfterSeconds"], 0)
        self.assertEqual(1, len(glass.issued("input", "keyevent", validate.KEY_SLEEP)))

    def test_the_processor_is_kept_running_only_for_the_check(self):
        glass, _ = self.run_check()
        self.assertFalse(glass.locked)
        locks = [index for index, command in enumerate(glass.adb.commands) if "/sys/power/wake_lock" in command[1]]
        sleeps = [index for index, command in enumerate(glass.adb.commands)
                  if command[1:] == ("input", "keyevent", validate.KEY_SLEEP)]
        self.assertLess(locks[0], sleeps[0])

    def test_an_attended_emulator_is_never_put_to_sleep(self):
        glass = self.failing(CheckSkipped, "leaves a display that was turned off alone", attended=True)
        self.assertEqual([], glass.issued("input", "keyevent"))

    def test_an_emulator_that_would_stop_answering_is_never_put_to_sleep(self):
        glass = self.failing(CheckSkipped, "cannot keep the processor running", root=False)
        self.assertEqual([], glass.issued("input", "keyevent"))

    def test_a_display_left_asleep_fails_and_the_emulator_is_woken_for_the_next_check(self):
        glass = self.failing(CheckFailed, "waiting for Mirror Home to wake the display", wakes_after=None)
        self.assertEqual(1, len(glass.issued("input", "keyevent", validate.KEY_WAKEUP)))
        self.assertTrue(glass.awake)
        self.assertFalse(glass.locked)

    def test_android_must_agree_that_it_is_awake(self):
        self.failing(CheckFailed, "Android reports itself Asleep", wakefulness="Asleep")


class FrozenEmulatorTest(unittest.TestCase):
    """A frozen emulator keeps its ADB connection and answers nothing."""

    def adb(self):
        return validate.Adb(pathlib.Path("adb"), "emulator-5580")

    def hanging(self, printed=None):
        return mock.patch.object(
            validate.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(["adb"], 120, output=printed),
        )

    def answering(self, printed, code=0):
        return mock.patch.object(
            validate.subprocess, "run",
            return_value=subprocess.CompletedProcess(["adb"], code, stdout=printed, stderr=b""),
        )

    def test_a_command_that_never_finishes_fails_its_check_and_names_the_device(self):
        with self.hanging(), self.assertRaisesRegex(
            CheckFailed,
            "adb shell am force-stop dev.mirror.repurpose did not finish within 120 s; "
            "emulator-5580 has stopped answering",
        ):
            self.adb().shell("am", "force-stop", validate.PACKAGE)

    def test_a_command_whose_failure_is_tolerated_gives_what_it_printed(self):
        with self.hanging(b"10-01 16:08:28 partial\n"):
            self.assertEqual("10-01 16:08:28 partial\n", self.adb().run("logcat", "-d", check=False))
        with self.hanging():
            self.assertEqual("", self.adb().shell("am", "force-stop", "x", check=False))

    def test_a_screen_capture_that_never_finishes_fails_its_check(self):
        with self.hanging(), self.assertRaisesRegex(
            CheckFailed, "could not capture the screen within 60 s; emulator-5580 has stopped answering"
        ):
            self.adb().capture()

    def test_a_device_answers_if_it_runs_a_command(self):
        with self.answering(b"answering\r\n"):
            self.assertTrue(self.adb().answers())
        with self.answering(b"", code=1):
            self.assertFalse(self.adb().answers())
        with self.hanging():
            self.assertFalse(self.adb().answers())

    class Device:
        serial = "emulator-5580"

        def __init__(self, answering):
            self.answering = answering
            self.commands = []

        def answers(self):
            return self.answering

        def run(self, *arguments, **_options):
            self.commands.append(arguments)
            return "10-01 03:17:00.000 I/ControlServerService: listening\n"

        def shell(self, *arguments, **_options):
            self.commands.append(("shell", *arguments))
            return f"{arguments[-1]}: none\n"

    def test_logs_and_crash_records_are_kept_while_the_emulator_answers(self):
        device = self.Device(answering=True)
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(validate.save_logs(device, pathlib.Path(directory)))
            self.assertIn("ControlServerService", (pathlib.Path(directory) / "logcat.txt").read_text(encoding="utf-8"))
            crashes = (pathlib.Path(directory) / "crashes.txt").read_text(encoding="utf-8")
        for record in validate.CRASH_RECORDS:
            self.assertIn(f"{record}: none", crashes)

    def test_a_frozen_emulator_is_not_asked_for_logs_and_the_run_says_why(self):
        device = self.Device(answering=False)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertFalse(validate.save_logs(device, pathlib.Path(directory)))
            self.assertEqual([], list(pathlib.Path(directory).iterdir()))
        self.assertEqual([], device.commands)
        self.assertIn("emulator-5580 has stopped answering", printed.getvalue())
        self.assertIn("say nothing about Mirror Home", printed.getvalue())

    def test_the_guide_has_the_section_the_run_points_to(self):
        guide = (REPO / "docs" / "validation.md").read_text(encoding="utf-8")
        self.assertIn("### When the emulator stops answering", guide)

    class Process:
        """The emulator's process: running until it is told it has ended."""

        def __init__(self):
            self.code = None

        def poll(self):
            return self.code

    class SilentAdb(FakeAdb):
        """An emulator that still has a connection and answers nothing."""

        def answers(self, timeout=20.0):
            self.commands.append(("answers",))
            return False

    def context(self, directory, adb=None):
        ctx = validate.Context(adb or FakeAdb([frame(255)]), FakeApi(), pathlib.Path(directory))
        ctx.emulator = mock.Mock()
        ctx.emulator.exited.return_value = False
        return ctx

    def test_a_check_does_not_start_once_the_emulators_process_has_ended(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            ctx.emulator.exited.return_value = True
            with self.assertRaisesRegex(CheckFailed, "froze or exited during this run"):
                ctx.before_check()
            self.assertTrue(ctx.emulator_lost)
            self.assertEqual([], ctx.adb.commands)

    def test_an_emulator_that_answers_nothing_is_found_before_the_next_check(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory, self.SilentAdb([frame(255)]))
            with self.assertRaisesRegex(CheckFailed, "says nothing about Mirror Home"):
                ctx.before_check()
            asked = len(ctx.adb.commands)
            # It is asked once; every later check fails without waiting for it again.
            with self.assertRaisesRegex(CheckFailed, "says nothing about Mirror Home"):
                ctx.before_check()
            self.assertEqual(asked, len(ctx.adb.commands))

    def test_a_window_that_names_no_focus_is_not_mistaken_for_a_frozen_emulator(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context(directory)
            ctx.before_check()
            self.assertFalse(ctx.emulator_lost)

    def test_every_check_after_the_emulator_is_lost_fails_with_the_reason(self):
        ran = []
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            ctx = self.context(directory)

            def loses_it(context):
                context.emulator.exited.return_value = True
                raise CheckFailed("Timed out after 60 s waiting for run 4 of Mirror Home to answer")

            checks = [
                ("restart", "Restart", loses_it, False),
                ("quick-restart", "Quick restart", lambda context: ran.append("quick-restart"), False),
                ("script-errors", "Script errors", lambda context: ran.append("script-errors"), False),
            ]
            results = validate.run_checks(checks, ctx)
        self.assertEqual([], ran)
        self.assertEqual(["fail"] * 3, [result.status for result in results])
        self.assertIn("run 4 of Mirror Home", results[0].message)
        for result in results[1:]:
            self.assertEqual(validate.EMULATOR_LOST, result.message)


class RebootWithALostEmulatorTest(unittest.TestCase):
    def context(self, directory, *, exits):
        report = healthy_report()
        api = FakeApi({
            ("GET", "/api/v1/health"): report,
            ("GET", "/api/v1/preferences"): {"timeZone": "America/Los_Angeles"},
        })
        adb = FakeAdb([frame(255)])
        adb.run = lambda *arguments, **_options: adb.commands.append(arguments) or ""
        ctx = validate.Context(adb, api, pathlib.Path(directory))
        ctx.emulator = mock.Mock()
        ctx.emulator.exited.return_value = exits
        return ctx

    def test_an_emulator_that_exits_on_reboot_fails_the_check_at_once_and_is_not_blamed_on_home(self):
        clock = fake_time(self)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(validate.android_emulator, "boot_completed", return_value=False):
            ctx = self.context(directory, exits=True)
            started = clock.now
            with self.assertRaisesRegex(
                CheckFailed, "emulator exited when Android rebooted.*not of Mirror Home"
            ):
                validate.check_reboot(ctx)
            self.assertLess(clock.now - started, 30)
            self.assertTrue(ctx.emulator_lost)

    def test_an_emulator_that_never_boots_again_is_given_up_on_for_the_rest_of_the_run(self):
        clock = fake_time(self)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(validate.android_emulator, "boot_completed", return_value=False):
            ctx = self.context(directory, exits=False)
            started = clock.now
            with self.assertRaisesRegex(CheckFailed, "waiting for Android to boot again"):
                validate.check_reboot(ctx)
            self.assertLess(clock.now - started, validate.REBOOT_SECONDS + 60)
            self.assertTrue(ctx.emulator_lost)
            with self.assertRaisesRegex(CheckFailed, "says nothing about Mirror Home"):
                ctx.before_check()


class LiveMirror(FakeApi):
    """A paired Mirror that behaves, with switches that make it misbehave."""

    SETTINGS = {
        "enabled": False, "wakeTime": "07:30", "sleepTime": "22:30", "wakeBrightness": 190,
        "ambientEnabled": False, "ambientMinimum": 20, "ambientMaximum": 220,
        "motionEnabled": True, "motionTimeoutSeconds": 300, "motionSensitivity": 6,
    }

    def __init__(self, *faults):
        super().__init__()
        self.faults = set(faults)
        self.token = "owner"
        self.credentials = {"owner-id": "owner"}
        self.pairing_open = False
        self.window = None
        self.notes = []
        self.notes_version = 4
        self.board = {}
        self.board_version = 9
        self.settings = dict(self.SETTINGS)
        self.sleeping = False
        self.manual = False
        self.brightness = 190
        self.media = "idle"
        self.playing = True
        self.frames = 9_000
        self.url = ""
        self.weather_at = 100
        self.weather_error = None
        self.run_id = 7

    def call(self, method, path, body=None, *, token=True, **_options):
        self.calls.append((method, path, body))
        bearer = self.token if token is True else token
        public = path in ("/api/v1/bootstrap", "/api/v1/pair")
        if not public and bearer not in self.credentials.values():
            return Reply(401, {"error": "Authentication required"}, {})
        handler = getattr(self, method.lower() + path.replace("/api/v1", "").replace("/", "_").replace("-", "_"), None)
        if handler is None and path.startswith("/api/v1/notes/"):
            self.notes = [note for note in self.notes if note["id"] != path.rsplit("/", 1)[1]]
            self.notes_version += 1
            return Reply(200, {}, {})
        if handler is None and path.startswith("/api/v1/board/items/"):
            return self.board_item(method, path.rsplit("/", 1)[1], body or {})
        if handler is None:
            raise AssertionError(f"Unexpected {method} {path}")
        reply = handler(body or {})
        return reply if isinstance(reply, Reply) else Reply(200, reply, {})

    def get_bootstrap(self, _body):
        return {"apiVersion": 1, "paired": True, "pairingOpen": self.pairing_open or self.window is not None}

    def get_clients(self, _body):
        return {"clients": [{"id": client, "name": client} for client in self.credentials]}

    def post_pair_window(self, _body):
        self.window = "424242"
        return {"code": self.window, "expiresInSeconds": 600}

    def post_pair(self, body):
        if self.window is None and not self.pairing_open and "guess-examined" not in self.faults:
            return Reply(403, {"reason": "closed"}, {})
        if body["code"] != self.window:
            return Reply(401, {"reason": "wrong-code"}, {})
        self.window = None
        self.credentials["new-id"] = "new-credential"
        return {"token": "new-credential", "clientId": "new-id"}

    def post_clients_revoke(self, body):
        if "revoke-ignored" not in self.faults:
            self.credentials.pop(body["id"], None)
        return {}

    def get_status(self, _body):
        if self.playing and not self.sleeping:
            self.frames += 45
        board = {} if "no-board" in self.faults else {"boardVersion": self.board_version}
        return {
            **board,
            "appVersion": "2.2.0", "notesVersion": self.notes_version,
            "brightness": 0 if self.sleeping else self.brightness,
            "media": {"state": self.media},
            "ambientVideo": {
                "enabled": True, "playing": self.playing, "renderedFrames": self.frames,
                "droppedFramePercent": 0.0, "error": None, "decoderName": "OMX.qcom.video.decoder.avc",
            },
        }

    def get_notes(self, _body):
        return {"notes": list(self.notes), "maxNotes": 50}

    def post_notes(self, body):
        note = {"id": f"n{len(self.notes) + 1}", "text": body["text"]}
        self.notes.insert(0, note)
        if "notes-version-stuck" not in self.faults:
            self.notes_version += 1
        return Reply(201, {"note": note}, {})

    def get_board(self, _body):
        return {
            "version": self.board_version, "items": list(self.board.values()),
            "counts": {"total": len(self.board)}, "glass": {"showsBoard": False},
        }

    def board_item(self, method, name, body):
        if method == "PUT":
            if "board-full" in self.faults:
                return Reply(409, {"error": "The board holds at most 100 items"}, {})
            self.board[name] = {"id": name, "title": body["title"], "state": "open"}
        elif name not in self.board:
            return Reply(404, {"error": "The board has no such item"}, {})
        elif method == "PATCH":
            self.board[name]["state"] = "open" if "board-done-ignored" in self.faults else "done"
        elif method == "DELETE" and "board-delete-ignored" not in self.faults:
            del self.board[name]
        if method != "GET" and "board-version-stuck" not in self.faults:
            self.board_version += 1
        return Reply(201 if method == "PUT" else 200, {"item": self.board.get(name)}, {})

    def get_automation(self, _body):
        return dict(self.settings, sleeping=self.sleeping, manualOverride=self.manual, sleepReason="none")

    def put_automation(self, body):
        self.settings = dict(body)
        self.manual = False
        return self.get_automation(None)

    def post_automation_sleep(self, _body):
        self.sleeping, self.manual = True, True
        if "video-keeps-playing" not in self.faults:
            self.playing = False
        return {"sleeping": True}

    def post_automation_wake(self, _body):
        self.sleeping, self.manual, self.playing = False, True, True
        self.brightness = self.settings["wakeBrightness"]
        return {"sleeping": False}

    def post_control_brightness(self, body):
        if "brightness-stuck" in self.faults:
            return Reply(503, {"changed": False, "value": body["value"]}, {})
        self.brightness = body["value"]
        return {"changed": True, "value": body["value"]}

    def get_dashboard(self, _body):
        return {"url": self.url}

    def put_dashboard(self, body):
        self.url = body["url"]
        return {"url": self.url}

    def get_health(self, _body):
        report = healthy_report()
        report["process"]["runId"] = self.run_id
        if self.url == validate.UNREACHABLE_PAGE and "no-fallback" not in self.faults:
            report["dashboard"].update(
                url="http://127.0.0.1:8787/dashboard/offline.html", lastFailurePhase="load-error"
            )
        elif self.url:
            report["dashboard"].update(url=self.url, rendererPresent=False)
        return report

    def get_weather(self, _body):
        return {
            "config": {"enabled": True}, "updatedAt": self.weather_at, "refreshing": False,
            "stale": False, "state": "error" if self.weather_error else "ready",
            "error": self.weather_error,
        }

    def post_weather_refresh(self, _body):
        if "weather-fails" in self.faults:
            self.weather_error = "Unable to resolve host"
        else:
            self.weather_at += 60_000
        return Reply(202, {}, {})


class ExerciseTest(unittest.TestCase):
    def context(self, mirror):
        fake_time(self)
        ctx = validate.MirrorContext(mirror)
        ctx.health = healthy_report()
        return ctx

    def sent(self, mirror, method, path):
        return [call[2] for call in mirror.calls if call[:2] == (method, path)]

    def test_the_whole_exercise_passes_and_leaves_the_mirror_as_it_was(self):
        mirror = LiveMirror()
        ctx = self.context(mirror)
        ctx.health = None
        with contextlib.redirect_stdout(io.StringIO()):
            results = validate.run_checks(validate.MIRROR_CHECKS[2:3] + validate.EXERCISE_CHECKS, ctx)
        self.assertEqual(["pass"] * 8, [result.status for result in results], [r.message for r in results])
        self.assertEqual({"owner-id": "owner"}, mirror.credentials)
        self.assertEqual([], mirror.notes)
        self.assertEqual({}, mirror.board)
        self.assertEqual(LiveMirror.SETTINGS, mirror.settings)
        self.assertEqual((False, 190, ""), (mirror.manual, mirror.brightness, mirror.url))

    def test_an_older_mirror_home_is_not_exercised(self):
        mirror = LiveMirror()
        ctx = self.context(mirror)
        ctx.health = None
        for _, _, check, _ in validate.EXERCISE_CHECKS:
            with self.assertRaisesRegex(CheckSkipped, "too old to be exercised"):
                check(ctx)
        self.assertEqual([], mirror.calls)

    def test_a_guess_examined_while_pairing_is_closed_fails(self):
        mirror = LiveMirror("guess-examined")
        with self.assertRaisesRegex(CheckFailed, "A guess was examined while no code was on display: 401"):
            validate.exercise_pairing(self.context(mirror))
        self.assertEqual({"owner-id": "owner"}, mirror.credentials)

    def test_with_a_code_on_display_no_wrong_code_is_sent(self):
        mirror = LiveMirror()
        mirror.pairing_open = True
        ctx = self.context(mirror)
        validate.exercise_pairing(ctx)
        self.assertIn("not probed", ctx.details["closedPairing"])
        self.assertEqual(["424242"], [body["code"] for body in self.sent(mirror, "POST", "/api/v1/pair")])

    def test_a_revoked_credential_that_still_works_fails(self):
        with self.assertRaisesRegex(CheckFailed, "A revoked credential still works"):
            validate.exercise_pairing(self.context(LiveMirror("revoke-ignored")))

    def test_a_note_that_would_not_reach_the_glass_fails_and_is_still_deleted(self):
        mirror = LiveMirror("notes-version-stuck")
        with self.assertRaisesRegex(CheckFailed, "did not change notesVersion"):
            validate.exercise_notes(self.context(mirror))
        self.assertEqual([], mirror.notes)

    def test_a_full_note_list_is_left_alone(self):
        mirror = LiveMirror()
        mirror.notes = [{"id": f"kept{index}", "text": "kept"} for index in range(50)]
        with self.assertRaisesRegex(CheckSkipped, "as many notes as it can"):
            validate.exercise_notes(self.context(mirror))
        self.assertEqual(50, len(mirror.notes))

    def test_the_board_item_says_who_posted_it_and_would_clear_itself(self):
        mirror = LiveMirror()
        ctx = self.context(mirror)
        validate.exercise_board(ctx)
        (path, posted), = [(call[1], call[2]) for call in mirror.calls if call[0] == "PUT"]
        self.assertRegex(path, r"^/api/v1/board/items/validation-\d+$")
        self.assertEqual(
            {"kind": "todo", "title": "Validation note", "ttlSeconds": 120, "source": "Validation (temporary)"},
            posted,
        )
        self.assertEqual({}, mirror.board)
        self.assertEqual({"boardOnGlass": False, "boardItems": 0}, ctx.details)

    def test_a_mirror_home_from_before_the_board_is_skipped(self):
        mirror = LiveMirror("no-board")
        with self.assertRaisesRegex(CheckSkipped, "has no board"):
            validate.exercise_board(self.context(mirror))
        self.assertEqual([("GET", "/api/v1/status", None)], mirror.calls)

    def test_a_full_board_is_left_alone(self):
        mirror = LiveMirror("board-full")
        with self.assertRaisesRegex(CheckSkipped, "as many items as it can"):
            validate.exercise_board(self.context(mirror))
        self.assertEqual({}, mirror.board)

    def test_a_board_change_the_glass_would_not_hear_of_fails(self):
        mirror = LiveMirror("board-version-stuck")
        with self.assertRaisesRegex(CheckFailed, "did not change boardVersion"):
            validate.exercise_board(self.context(mirror))
        self.assertEqual({}, mirror.board)

    def test_an_item_that_does_not_become_done_fails(self):
        mirror = LiveMirror("board-done-ignored")
        with self.assertRaisesRegex(CheckFailed, "Marking the item done left it open"):
            validate.exercise_board(self.context(mirror))
        self.assertEqual({}, mirror.board)

    def test_an_item_that_is_not_removed_fails(self):
        mirror = LiveMirror("board-delete-ignored")
        with self.assertRaisesRegex(CheckFailed, "The removed item can still be read"):
            validate.exercise_board(self.context(mirror))

    def test_the_display_is_left_alone_while_something_plays(self):
        mirror = LiveMirror()
        mirror.media = "playing"
        with self.assertRaisesRegex(CheckSkipped, "something is playing"):
            validate.exercise_display(self.context(mirror))
        self.assertEqual([], self.sent(mirror, "POST", "/api/v1/automation/sleep"))

    def test_a_video_that_plays_on_behind_a_dark_display_fails_and_the_schedule_is_put_back(self):
        mirror = LiveMirror("video-keeps-playing")
        mirror.settings["wakeTime"] = "06:15"
        with self.assertRaisesRegex(CheckFailed, "background video to stop once the display is dark"):
            validate.exercise_display(self.context(mirror))
        self.assertEqual("06:15", mirror.settings["wakeTime"])
        self.assertIs(False, mirror.manual)

    def test_a_brightness_that_cannot_be_set_fails(self):
        mirror = LiveMirror("brightness-stuck")
        with self.assertRaisesRegex(CheckFailed, "The brightness could not be set: 503"):
            validate.exercise_display(self.context(mirror))
        self.assertIs(False, mirror.manual)

    def test_the_brightness_an_awake_mirror_had_is_restored(self):
        mirror = LiveMirror()
        mirror.brightness = 120
        ctx = self.context(mirror)
        validate.exercise_display(ctx)
        self.assertEqual(190, ctx.details["wakeBrightness"])
        self.assertEqual(120, mirror.brightness)
        self.assertEqual([150, 190, 120], [body["value"] for body in self.sent(mirror, "POST", "/api/v1/control/brightness")])

    def test_an_owner_who_put_the_display_to_sleep_finds_it_asleep_again(self):
        mirror = LiveMirror()
        mirror.sleeping, mirror.manual, mirror.playing = True, True, False
        validate.exercise_display(self.context(mirror))
        self.assertEqual((True, True), (mirror.sleeping, mirror.manual))

    def test_a_page_that_never_gives_way_to_the_offline_clock_fails_and_the_address_is_restored(self):
        mirror = LiveMirror("no-fallback")
        mirror.url = "http://homeassistant.local:8123/"
        with self.assertRaisesRegex(CheckFailed, "offline clock to replace a page that cannot load"):
            validate.exercise_offline_fallback(self.context(mirror))
        self.assertEqual("http://homeassistant.local:8123/", mirror.url)

    def test_a_web_dashboard_is_restored_after_the_fallback(self):
        mirror = LiveMirror()
        mirror.url = "http://homeassistant.local:8123/"
        validate.exercise_offline_fallback(self.context(mirror))
        self.assertEqual(
            [validate.UNREACHABLE_PAGE, "http://homeassistant.local:8123/"],
            [body["url"] for body in self.sent(mirror, "PUT", "/api/v1/dashboard")],
        )

    def test_weather_that_cannot_refresh_fails_with_its_reason(self):
        with self.assertRaisesRegex(CheckFailed, "The weather did not refresh: Unable to resolve host"):
            validate.exercise_weather(self.context(LiveMirror("weather-fails")))

    def test_a_restart_during_the_exercise_fails(self):
        mirror = LiveMirror()
        ctx = self.context(mirror)
        mirror.run_id = 8
        with self.assertRaisesRegex(CheckFailed, "Mirror Home restarted during the exercise"):
            validate.exercise_health_after(ctx)


class UpdaterCheckTest(unittest.TestCase):
    STATUS = {
        "updaterVersion": "1.1.0", "deviceOwner": True, "state": "succeeded", "active": False,
        "deviceFingerprint": MIRROR_FINGERPRINT, "supportedFingerprint": MIRROR_FINGERPRINT,
        "homeVersionName": "2.2.0", "homeVersionCode": 74,
        "knownGoodVersionName": "2.1.0", "knownGoodVersionCode": 71,
    }

    def check(self, status, *, home="2.2.0"):
        import otactl

        with tempfile.TemporaryDirectory() as directory:
            credential = pathlib.Path(directory) / "mirror-ota.json"
            credential.write_text(json.dumps({"host": "10.0.0.196", "token": "t" * 43}), encoding="utf-8")
            ctx = validate.MirrorContext(FakeApi(), credential)
            ctx.status = {"appVersion": home}
            outcome = status if isinstance(status, Exception) else None
            with mock.patch.object(
                otactl.OtaClient, "status", side_effect=outcome, return_value=None if outcome else status
            ):
                validate.mirror_updater(ctx)
            return ctx

    def test_a_ready_supervisor_passes(self):
        ctx = self.check(dict(self.STATUS))
        self.assertEqual("2.2.0 (code 74)", ctx.details["home"])
        self.assertEqual("2.1.0 (code 71)", ctx.details["knownGood"])

    def test_without_a_credential_file_the_check_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            ctx = validate.MirrorContext(FakeApi(), pathlib.Path(directory) / "absent.json")
            with self.assertRaisesRegex(CheckSkipped, "no OTA credential"):
                validate.mirror_updater(ctx)

    def test_each_fault_is_named(self):
        import otactl

        faults = {
            "not the device owner": {"deviceOwner": False},
            "does not support this firmware": {"deviceFingerprint": "other/device:6.0.1/X/1:user/release-keys"},
            "needs recovery: Home did not answer": {"state": "recovery_required", "message": "Home did not answer"},
            "the supervisor sees Home 2.1.0, Home reports 2.2.0": {"homeVersionName": "2.1.0"},
            r"the supervisor crashed 2 time\(s\); last java.lang.IllegalStateException: disk full": {"health": {
                "process": {"runId": 9, "uptimeSeconds": 60, "previousRun": {"end": "crash"}},
                "crashes": {"count": 2, "last": {"exception": "java.lang.IllegalStateException", "message": "disk full"}},
            }},
        }
        for message, change in faults.items():
            with self.assertRaisesRegex(CheckFailed, message):
                self.check(dict(self.STATUS, **change))
        with self.assertRaisesRegex(CheckFailed, "did not answer a signed request: Unable to reach OTA supervisor"):
            self.check(otactl.OtaUnreachableError("Unable to reach OTA supervisor: timed out"))

    def test_a_supervisor_that_reports_its_own_health_is_summarized(self):
        ctx = self.check(dict(self.STATUS, health={
            "process": {"runId": 12, "uptimeSeconds": 7200, "previousRun": {"end": "killed"}},
            "crashes": {"count": 0, "last": None},
        }))
        self.assertEqual({"runId": 12, "uptimeHours": 2.0, "previousEnd": "killed"}, ctx.details["supervisorRun"])


class DashboardFrameTest(unittest.TestCase):
    """After a restart the screen must show the dashboard, not merely be bright."""

    def context(self, frames, directory):
        ctx = validate.Context(FakeAdb(frames), FakeApi(), pathlib.Path(directory))
        ctx.awake_peak = 255
        return ctx

    def test_another_apps_bright_screen_is_not_the_dashboard(self):
        fake_time(self)
        wallpaper = frame(255, fill=180)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([wallpaper, wallpaper, frame(255)], directory)
            self.assertEqual(255, ctx.wait_lit("restart"))
            # Both bright frames were passed over; the frame kept is the dashboard's.
            self.assertEqual([frame(255)], ctx.adb.frames)
            self.assertEqual(
                screen_capture.to_png(frame(255)), (pathlib.Path(directory) / "restart.png").read_bytes()
            )

    def test_a_screen_that_stays_bright_all_over_fails_and_says_how_much_is_lit(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([frame(255, fill=180)], directory)
            with self.assertRaisesRegex(
                CheckFailed, "never became the dashboard again; brightest pixel 255; 100% of it is lit"
            ):
                ctx.wait_lit("restart")
            self.assertTrue((pathlib.Path(directory) / "restart-timeout.png").is_file())

    def test_a_sleeping_screen_is_still_judged_by_brightness_alone(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([frame(0)], directory)
            self.assertEqual(0, ctx.wait_peak("black", lambda peak: peak <= validate.BLACK_PEAK, "asleep"))


class ColdStartVerdictTest(unittest.TestCase):
    def context(self, frames, directory):
        health = healthy_report()
        api = FakeApi({("GET", "/api/v1/health"): health})
        return validate.Context(FakeAdb(frames), api, pathlib.Path(directory))

    def test_a_dark_start_passes(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([frame(0), frame(255), frame(255)], directory)
            validate.check_cold_start(ctx)
            self.assertLess(ctx.details["brightestStartupFrame"], 20)
            self.assertGreaterEqual(ctx.details["startupFrames"], 3)
            self.assertEqual([("shell", "am", "force-stop", "dev.mirror.repurpose")], ctx.adb.commands)
            self.assertFalse((pathlib.Path(directory) / "cold-start-bright.png").exists())

    def test_a_white_starting_window_fails_and_is_kept_as_evidence(self):
        fake_time(self)
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.context([frame(0), frame(255, fill=250), frame(255)], directory)
            with self.assertRaisesRegex(CheckFailed, "lit the whole screen .*bright flash"):
                validate.check_cold_start(ctx)
            self.assertTrue((pathlib.Path(directory) / "cold-start-bright.png").is_file())


class MirrorStatusTest(unittest.TestCase):
    def check(self, status):
        ctx = validate.MirrorContext(FakeApi({("GET", "/api/v1/status"): status}))
        validate.mirror_status(ctx)
        return ctx

    def test_a_working_mirror_passes_and_reports_its_clock(self):
        ctx = self.check(healthy_status())
        self.assertEqual(6.0, ctx.details["uptimeDays"])
        self.assertEqual("home", ctx.details["wifi"])
        self.assertEqual(-480, ctx.details["clock"]["nextUtcOffsetChange"]["utcOffsetMinutes"])

    def test_a_release_without_clock_changes_says_so(self):
        status = healthy_status()
        del status["nextUtcOffsetChange"]
        self.assertEqual("not reported", self.check(status).details["clock"]["nextUtcOffsetChange"])

    def test_each_fault_fails_the_check(self):
        faults = {
            "Wi-Fi is not connected": lambda status: status["wifi"].update(connected=False),
            "Background video reports an error": lambda status: status["ambientVideo"].update(error="decoder died"),
            r"dropped 2\.50% of its frames": lambda status: status["ambientVideo"].update(droppedFramePercent=2.5),
            "Presence sensing is on but not monitoring: camera-error": lambda status: status["automation"].update(
                motion={"monitoring": False, "state": "camera-error"}
            ),
            "weather shown is stale": lambda status: status["weather"].update(stale=True),
        }
        for message, break_it in faults.items():
            status = healthy_status()
            break_it(status)
            with self.assertRaisesRegex(CheckFailed, message):
                self.check(status)

    def test_presence_sensing_that_is_off_need_not_monitor(self):
        status = healthy_status()
        status["automation"].update(motionEnabled=False, motion={"monitoring": False, "state": "off"})
        self.check(status)


class MirrorHealthTest(unittest.TestCase):
    def check(self, report):
        reply = report if isinstance(report, Reply) else Reply(200, report, {})
        ctx = validate.MirrorContext(FakeApi({("GET", "/api/v1/health"): reply}))
        validate.mirror_health(ctx)
        return ctx

    def test_a_healthy_mirror_passes_and_reports_what_the_emulator_must_match(self):
        ctx = self.check(healthy_report())
        self.assertEqual(
            {"runId": 7, "uptimeHours": 2.0, "previousEnd": "update", "earlyStops": 0},
            ctx.details["process"],
        )
        self.assertEqual({"densityDpi": 240, "on": True}, ctx.details["display"])
        self.assertEqual("bundled", ctx.details["clockSource"])
        # What the emulator's power settings and its second HOME app are modelled on.
        self.assertEqual((600, 0), (
            ctx.details["power"]["screenOffTimeoutSeconds"], ctx.details["power"]["plugged"],
        ))
        self.assertEqual("dev.mirror.repurpose/.MainActivity", ctx.details["front"])
        self.assertEqual(0, ctx.details["recovery"]["relaunches"])

    def test_a_report_from_before_the_dashboard_was_kept_in_front_is_read(self):
        report = healthy_report()
        del report["device"]["power"]
        for key in ("selectedHome", "front", "recovery"):
            del report["activity"][key]
        ctx = self.check(report)
        self.assertIsNone(ctx.details["power"])
        self.assertIsNone(ctx.details["recovery"])

    def test_a_dashboard_that_mirror_home_is_bringing_back_is_not_a_fault(self):
        fake_time(self)
        covered = healthy_report()
        covered["activity"].update(
            showing=False, resumed=False, pausedForSeconds=4,
            front="com.mirror.launcher/.oobe.OutOfBoxActivity, dev.mirror.repurpose/.MainActivity",
        )
        back = healthy_report()
        back["activity"]["recovery"].update(relaunches=1, lastReason="covered")
        answers = [covered, covered, back]
        api = FakeApi({("GET", "/api/v1/health"): lambda _body: answers.pop(0) if len(answers) > 1 else answers[0]})
        ctx = validate.MirrorContext(api)
        validate.mirror_health(ctx)
        self.assertIs(True, ctx.details["dashboardReturned"])
        self.assertEqual(1, ctx.details["recovery"]["relaunches"])

    def test_a_dashboard_that_stays_covered_names_what_is_in_front(self):
        fake_time(self)
        covered = healthy_report()
        covered["activity"].update(
            showing=False, resumed=False, pausedForSeconds=8400,
            front="com.mirror.launcher/.oobe.OutOfBoxActivity, dev.mirror.repurpose/.MainActivity",
        )
        with self.assertRaisesRegex(
            CheckFailed, r"not in front \(paused 8400 s, unfocused 0 s\); in front: com.mirror.launcher/.oobe"
        ):
            self.check(covered)

    def test_a_covered_dashboard_on_an_earlier_build_fails_without_waiting(self):
        covered = healthy_report()
        covered["activity"].update(showing=False, pausedForSeconds=8400)
        for key in ("selectedHome", "front", "recovery"):
            del covered["activity"][key]
        api = FakeApi({("GET", "/api/v1/health"): covered})
        with self.assertRaisesRegex(CheckFailed, r"not in front \(paused 8400 s, unfocused 0 s\)$"):
            validate.mirror_health(validate.MirrorContext(api))
        self.assertEqual(1, len(api.calls))

    def test_a_first_run_has_no_previous_run(self):
        report = healthy_report()
        report["process"]["previousRun"] = None
        self.assertIsNone(self.check(report).details["process"]["previousEnd"])

    def test_the_early_stop_android_makes_during_an_update_is_not_a_fault(self):
        report = healthy_report()
        report["process"]["earlyStops"] = 1
        self.assertEqual(1, self.check(report).details["process"]["earlyStops"])

    def test_a_report_from_before_early_stops_were_counted_is_read(self):
        report = healthy_report()
        del report["process"]["earlyStops"]
        self.assertEqual(0, self.check(report).details["process"]["earlyStops"])

    def test_an_older_mirror_home_is_skipped_not_failed(self):
        with self.assertRaisesRegex(CheckSkipped, "predates the health report"):
            self.check(Reply(404, {"error": "Not found"}, {}))

    def test_a_refused_credential_fails(self):
        with self.assertRaisesRegex(CheckFailed, "Health answered 401"):
            self.check(Reply(401, {"error": "Unauthorized"}, {}))

    def test_each_fault_is_named(self):
        fake_time(self)
        faults = {
            "Home was stopped 3 times while starting before this run": lambda report: report["process"].update(
                earlyStops=3
            ),
            r"2 crash\(es\); last java.lang.IllegalStateException: boom": lambda report: report["crashes"].update(
                count=2, last={"exception": "java.lang.IllegalStateException", "message": "boom"}
            ),
            r"the dashboard is not in front \(paused 0 s, unfocused 5400 s\)": lambda report: report["activity"].update(
                showing=False, unfocusedForSeconds=5400
            ),
            r"3 dashboard script error\(s\)": lambda report: report["dashboard"].update(consoleErrors=3),
            r"1 unhandled API error\(s\)": lambda report: report["api"].update(unhandledErrors=1),
            "Android reports low memory": lambda report: report["memory"].update(systemLow=True),
            "less than 256 MiB of storage is free": lambda report: report["storage"].update(dataFreeBytes=100 << 20),
            "OTA supervisor is installed but has not accepted connections for 30 s": lambda report: report[
                "otaSupervisor"
            ].update(listening=False),
            "fixed offset and will not follow daylight saving": lambda report: report["clock"].update(source="fixed"),
            "pairing is locked after wrong codes": lambda report: report["pairing"].update(lockedForSeconds=25),
            "Mirror Home is not Android's HOME app": lambda report: report["activity"].update(selectedHome=False),
            "Android has put the display to sleep": lambda report: report["device"]["power"].update(
                interactive=False
            ),
        }
        for message, break_it in faults.items():
            report = healthy_report()
            break_it(report)
            with self.assertRaisesRegex(CheckFailed, message):
                self.check(report)

    def test_a_supervisor_that_android_is_restarting_is_not_a_fault(self):
        fake_time(self)
        down = healthy_report()
        down["otaSupervisor"].update(listening=False, unreachableSince=OCTOBER_FIRST)
        answers = [down, down, healthy_report()]
        api = FakeApi({("GET", "/api/v1/health"): lambda _body: answers.pop(0) if len(answers) > 1 else answers[0]})
        ctx = validate.MirrorContext(api)
        validate.mirror_health(ctx)
        self.assertIs(True, ctx.details["otaSupervisorRestarting"])
        self.assertEqual(3, len(api.calls))

    def test_several_faults_are_reported_together(self):
        report = healthy_report()
        report["memory"]["systemLow"] = True
        report["clock"]["source"] = "fixed"
        with self.assertRaisesRegex(CheckFailed, "low memory; the clock keeps a fixed offset"):
            self.check(report)

    def test_a_mirror_without_the_supervisor_is_not_a_fault(self):
        report = healthy_report()
        report["otaSupervisor"] = {"installed": False}
        self.check(report)


class MirrorConfigTest(unittest.TestCase):
    def test_reads_host_and_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "mirror.json"
            path.write_text(json.dumps({"host": "10.0.0.196", "port": 8787, "token": "t"}), encoding="utf-8")
            self.assertEqual("10.0.0.196", validate.load_mirror_config(path)["host"])

    def test_explains_a_missing_or_incomplete_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "mirror.json"
            with self.assertRaisesRegex(CheckFailed, "Unable to read the Mirror credential file"):
                validate.load_mirror_config(path)
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(CheckFailed, "Unable to read the Mirror credential file"):
                validate.load_mirror_config(path)
            path.write_text(json.dumps({"host": "10.0.0.196"}), encoding="utf-8")
            with self.assertRaisesRegex(CheckFailed, "needs host and token"):
                validate.load_mirror_config(path)

    def test_the_command_fails_cleanly_without_a_credential_file(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = pathlib.Path(directory) / "missing.json"
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(1, validate.main(["mirror", "--config", str(missing)]))
            self.assertIn("validate: Unable to read the Mirror credential file", errors.getvalue())

    def test_the_mirror_check_only_reads(self):
        api = FakeApi({
            ("GET", "/api/v1/bootstrap"): {"apiVersion": 1, "appVersion": "2.2.0", "displayName": "Mirror", "pairingOpen": False},
            ("GET", "/api/v1/status"): healthy_status(),
            ("GET", "/api/v1/health"): healthy_report(),
        })
        with contextlib.redirect_stdout(io.StringIO()):
            results = validate.run_checks(validate.MIRROR_CHECKS, validate.MirrorContext(api))
        self.assertEqual(["pass", "pass", "pass", "skip"], [result.status for result in results])
        self.assertEqual("this computer holds no OTA credential for the Mirror", results[3].message)
        self.assertEqual({"GET"}, {method for method, _, _ in api.calls})


if __name__ == "__main__":
    unittest.main()
