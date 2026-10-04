import pathlib
import sys
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "tests"))

import validate
from test_validate import FakeAdb, FakeApi, fake_time, frame, healthy_report, healthy_status
from validate import CheckFailed, CheckSkipped, Reply

SUPERVISOR_APK = pathlib.Path("ota-updater-debug.apk")


class LastingMirror:
    """Mirror Home on Android 6 as the two checks meet it: Android has a switch
    for scanning while connected and forgets nothing until it restarts; it
    starts its HOME app again whenever that is stopped; and it ranks a
    supervisor that Mirror Home holds with what the display needs.

    ``faults`` name what a broken Mirror Home, supervisor or Android would do.
    """

    def __init__(self, *faults):
        self.faults = set(faults)
        self.run = 7
        self.uptime = 3600
        # Android's switch, and what Mirror Home's settings say.
        self.scanning = True
        self.wanted = "guard-on-at-start" in self.faults
        self.applied = 0
        # The supervisor: its process, if it runs, and whether Mirror Home holds it.
        self.installed = "already-installed" in self.faults
        self.pid = 4000 if self.installed else None
        self.held = False
        self.api = FakeApi()
        self.api.call = self.call
        self.adb = FakeAdb([frame(255)])
        self.adb.shell = self.shell
        self.adb.run = self.adb_run

    # -- what adb does ------------------------------------------------------

    def start_supervisor(self):
        self.pid = (self.pid or 4000) + 100
        self.held = "never-holds" not in self.faults

    def shell(self, *arguments, **_options):
        self.adb.commands.append(("shell", *arguments))
        if arguments == ("am", "force-stop", validate.PACKAGE):
            self.run += 1
            self.uptime = 1
            if "forgets-on-restart" in self.faults:
                self.wanted = False
            if "sets-again" in self.faults:
                self.applied = 1
            else:
                self.applied = 0
            if "dies-with-home" in self.faults:
                self.pid = None
            elif "restarted-with-home" in self.faults and self.pid:
                self.start_supervisor()
        elif arguments[:2] == ("kill", "-9"):
            assert arguments[2] == str(self.pid), arguments
            if "stays-dead" in self.faults:
                self.pid, self.held = None, False
            else:
                self.start_supervisor()
        elif arguments == ("ps",):
            lines = ["u0_a55 2904 1266 1456580 111236 ep_poll 0 S dev.mirror.repurpose"]
            if self.pid:
                lines.append(f"u0_a56 {self.pid} 1266 739116 31044 ep_poll 0 S {validate.SUPERVISOR}")
            return "\n".join(lines) + "\n"
        return ""

    def adb_run(self, *arguments, **_options):
        self.adb.commands.append(arguments)
        if arguments[:2] == ("install", "-r"):
            if "install-fails" in self.faults:
                return "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]\n"
            replacing = self.installed
            self.installed = True
            if replacing and "lets-go-when-replaced" in self.faults:
                self.pid += 100
                self.held = False
            else:
                self.start_supervisor()
            return "Success\n"
        if arguments[:1] == ("uninstall",):
            if "lingers" not in self.faults:
                self.installed, self.pid, self.held = False, None, False
            return "Success\n"
        raise AssertionError(f"Unexpected adb {arguments}")

    # -- what Mirror Home answers --------------------------------------------

    def guard(self):
        state = "applied" if self.wanted and not self.scanning else "off"
        if self.wanted and self.scanning:
            state = "error"
        return {
            "enabled": self.wanted, "supported": "no-switch" not in self.faults, "state": state,
            "detail": "Android went on scanning" if state == "error" else "",
            "scanningWhileConnected": self.scanning, "appliedAt": 1_791_100_000_000 if state == "applied" else None,
            "applied": self.applied, "checks": 1, "checkedAt": 1_791_100_000_000,
            **({} if "counts-no-scans" in self.faults else {
                "scans": {"whileConnected": 0, "sinceApplied": 0, "lastAt": None},
            }),
        }

    def supervisor(self):
        if not self.installed:
            return {"installed": False}
        held = self.held and self.pid is not None
        return {
            "installed": True, "versionName": "1.3.0", "listening": self.pid is not None,
            "hold": {
                "state": "held" if held else "waiting", "detail": "", "since": 1 if held else None,
                "binds": 1, "losses": 0, "pid": self.pid if held else None,
                "oomScoreAdj": (294 if "ranked-as-service" in self.faults else 58) if held else None,
            },
        }

    def call(self, method, path, body=None, **_options):
        self.api.calls.append((method, path, body))
        if (method, path) == ("GET", "/api/v1/health"):
            report = healthy_report()
            report["process"].update(runId=self.run, uptimeSeconds=self.uptime)
            report["wifi"] = {"connected": True, "scanGuard": self.guard()}
            report["otaSupervisor"] = self.supervisor()
            return Reply(200, report, {})
        if (method, path) == ("GET", "/api/v1/status"):
            status = healthy_status()
            brief = {key: self.guard()[key] for key in ("enabled", "supported", "state", "detail")}
            if "quiet-status" in self.faults:
                brief.update(enabled=False, state="off")
            status["wifi"]["scanGuard"] = brief
            return Reply(200, status, {})
        if (method, path) == ("GET", validate.SCAN_GUARD):
            return Reply(200, self.guard(), {})
        if (method, path) == ("PUT", validate.SCAN_GUARD):
            wanted = body.get("enabled")
            if not isinstance(wanted, bool):
                if "takes-any-guard" in self.faults:
                    return Reply(200, self.guard(), {})
                return Reply(400, {"error": "enabled must be true or false"}, {})
            self.wanted = wanted
            if wanted and "deaf-android" not in self.faults:
                self.scanning = False
                self.applied += 1
            if not wanted and "stays-quiet" not in self.faults:
                self.scanning = True
            return Reply(200, self.guard(), {})
        raise AssertionError(f"Unexpected {method} {path}")

    def context(self, directory, supervisor_apk=SUPERVISOR_APK):
        ctx = validate.Context(self.adb, self.api, pathlib.Path(directory))
        ctx.awake_peak = 255
        ctx.supervisor_apk = supervisor_apk
        return ctx


class LastingCase(unittest.TestCase):
    check = None

    def run_check(self, *faults, **options):
        fake_time(self)
        mirror = LastingMirror(*faults)
        with tempfile.TemporaryDirectory() as directory:
            ctx = mirror.context(directory, **options)
            type(self).check(ctx)
        return mirror, ctx

    def assert_faults(self, faults):
        for fault, message in faults.items():
            with self.subTest(fault=fault):
                with self.assertRaisesRegex(CheckFailed, message):
                    self.run_check(fault)


class ScanGuardCheckTest(LastingCase):
    check = staticmethod(validate.check_scan_guard)

    def test_a_guard_that_stops_the_scans_and_gives_them_back_passes(self):
        mirror, ctx = self.run_check()
        self.assertEqual(
            {
                "state": "applied", "scanningWhileConnected": False,
                "scans": {"whileConnected": 0, "sinceApplied": 0, "lastAt": None},
            },
            ctx.details["scanGuard"],
        )
        # It ends as it began: off, and Android scanning.
        self.assertEqual((False, True), (mirror.wanted, mirror.scanning))
        # Mirror Home was stopped once, to see that it finds its own work again.
        self.assertEqual([("shell", "am", "force-stop", validate.PACKAGE)], mirror.adb.commands)
        self.assertEqual(8, mirror.run)

    def test_each_fault_is_named(self):
        self.assert_faults({
            "no-switch": "has a switch for scanning while connected, and Mirror Home did not find it",
            "guard-on-at-start": "Before anyone asked for it, the scan guard stands at",
            "takes-any-guard": "A scan guard that is neither on nor off answered 200, expected 400",
            "deaf-android": "Turned on, the scan guard reports .*Android went on scanning",
            "quiet-status": "The status says of the scan guard",
            "forgets-on-restart": "After Mirror Home started again the scan guard reports",
            "sets-again": "Mirror Home set a switch again that still stood",
            "stays-quiet": "Turned off, Android was not put back to scanning as before",
            "counts-no-scans": "The scan guard does not count the scans that arrive",
        })

    def test_a_guard_that_fails_is_still_turned_off_again(self):
        fake_time(self)
        mirror = LastingMirror("sets-again")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CheckFailed):
                validate.check_scan_guard(mirror.context(directory))
        self.assertEqual((False, True), (mirror.wanted, mirror.scanning))


class UpdaterHeldCheckTest(LastingCase):
    check = staticmethod(validate.check_updater_held)

    def test_a_supervisor_that_is_held_through_everything_passes(self):
        mirror, ctx = self.run_check()
        self.assertEqual("1.3.0", ctx.details["supervisorVersion"])
        self.assertEqual(
            {"held": 58, "afterBeingEnded": 58, "afterBeingReplaced": 58}, ctx.details["oomScoreAdj"]
        )
        # Installed, ended, replaced, left alone while Mirror Home restarts, and removed again.
        self.assertEqual(
            [
                ("install", "-r", str(SUPERVISOR_APK)),
                ("shell", "kill", "-9", "4100"),
                ("install", "-r", str(SUPERVISOR_APK)),
                ("shell", "am", "force-stop", validate.PACKAGE),
                ("shell", "ps"),
                ("uninstall", validate.SUPERVISOR),
            ],
            mirror.adb.commands,
        )
        self.assertFalse(mirror.installed)

    def test_without_a_supervisor_signed_like_mirror_home_it_is_skipped(self):
        with self.assertRaisesRegex(CheckSkipped, "no OTA supervisor signed like this Mirror Home"):
            self.run_check(supervisor_apk=None)

    def test_each_fault_is_named(self):
        self.assert_faults({
            "already-installed": "A supervisor is already installed",
            "install-fails": "The supervisor was not installed: Failure",
            "never-holds": "waiting for Mirror Home to take hold of the OTA supervisor",
            "ranked-as-service": "Held by Mirror Home, the kernel ranks the supervisor at 294",
            "stays-dead": "waiting for an ended supervisor to be started and held again",
            "lets-go-when-replaced": "waiting for a replaced supervisor to be held again",
            "dies-with-home": "The supervisor ended when Mirror Home was stopped",
            "restarted-with-home": "did not carry on while Mirror Home was away: process 4300 became 4400",
            "lingers": "waiting for Mirror Home to notice that the supervisor was removed",
        })

    def test_a_supervisor_is_removed_again_whatever_happens(self):
        for fault in ("never-holds", "ranked-as-service", "stays-dead", "dies-with-home"):
            fake_time(self)
            mirror = LastingMirror(fault)
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(CheckFailed):
                    validate.check_updater_held(mirror.context(directory))
            self.assertFalse(mirror.installed, fault)
            self.assertEqual(("uninstall", validate.SUPERVISOR), mirror.adb.commands[-1], fault)


class LiveMirrorLongevityTest(unittest.TestCase):
    """What the read-only check of a real Mirror says of the two."""

    GUARD = {
        "enabled": True, "supported": True, "state": "applied", "detail": "", "scanningWhileConnected": False,
        "appliedAt": 1_791_100_000_000, "applied": 1, "checks": 12, "checkedAt": 1_791_103_600_000,
        "scans": {"whileConnected": 31, "sinceApplied": 0, "lastAt": 1_791_099_990_000},
    }
    HOLD = {"state": "held", "detail": "", "since": 1, "binds": 1, "losses": 0, "pid": 2203, "oomScoreAdj": 58}

    def check(self, change=None):
        report = healthy_report()
        report["wifi"] = {"connected": True, "scanGuard": dict(self.GUARD)}
        report["otaSupervisor"].update(versionName="1.3.0", hold=dict(self.HOLD))
        if change:
            change(report)
        ctx = validate.MirrorContext(FakeApi({("GET", "/api/v1/health"): Reply(200, report, {})}))
        validate.mirror_health(ctx)
        return ctx

    def test_a_mirror_with_both_in_place_passes_and_shows_the_scans(self):
        ctx = self.check()
        self.assertEqual(0, ctx.details["scanGuard"]["scans"]["sinceApplied"])
        # One or two can end just as the Mirror joins its network again.
        self.check(lambda report: report["wifi"]["scanGuard"]["scans"].update(sinceApplied=2))
        self.assertEqual(58, ctx.details["otaSupervisor"]["hold"]["oomScoreAdj"])

    def test_a_guard_that_is_off_or_a_supervisor_too_old_to_hold_is_the_owners_choice(self):
        self.check(lambda report: report["wifi"]["scanGuard"].update(enabled=False, state="off"))
        self.check(lambda report: report["otaSupervisor"]["hold"].update(
            state="unsupported", pid=None, oomScoreAdj=None
        ))
        # A Mirror that is reached by wire while its Wi-Fi looks for a network: the guard stands aside.
        def without_wifi(report):
            report["wifi"]["connected"] = False
            report["wifi"]["scanGuard"].update(state="waiting", scanningWhileConnected=True, appliedAt=None)

        self.check(without_wifi)
        # While Android starts an ended supervisor again, Mirror Home waits.
        self.check(lambda report: report["otaSupervisor"]["hold"].update(state="waiting", pid=None, oomScoreAdj=None))

    def test_a_mirror_home_from_before_either_is_read(self):
        def earlier(report):
            del report["wifi"]["scanGuard"]
            del report["otaSupervisor"]["hold"]

        self.assertNotIn("scanGuard", self.check(earlier).details)

    def test_each_fault_is_named(self):
        faults = {
            "the scan guard is on, but Android still scans for Wi-Fi while connected: SecurityException: no":
                lambda report: report["wifi"]["scanGuard"].update(state="error", detail="SecurityException: no"),
            "the scan guard is on, but Android still scans for Wi-Fi while connected$":
                lambda report: report["wifi"]["scanGuard"].update(state="off"),
            # Standing aside is for a Wi-Fi without a network, and this one has its network.
            "^the scan guard is on, but Android still scans for Wi-Fi while connected$":
                lambda report: report["wifi"]["scanGuard"].update(state="waiting"),
            "the scan guard is applied, yet 7 scans have arrived while connected since":
                lambda report: report["wifi"]["scanGuard"]["scans"].update(sinceApplied=7),
            "signed with another key than Mirror Home":
                lambda report: report["otaSupervisor"]["hold"].update(state="refused", pid=None, oomScoreAdj=None),
            "holds the OTA supervisor, yet the kernel ranks it at 294, not at 58 or under":
                lambda report: report["otaSupervisor"]["hold"].update(oomScoreAdj=294),
        }
        for message, break_it in faults.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(CheckFailed, message):
                    self.check(break_it)


if __name__ == "__main__":
    unittest.main()
