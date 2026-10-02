#!/usr/bin/env python3
"""Check Mirror Home end to end without watching the glass.

    python tools/validate.py emulator      full suite on an Android 6 emulator
    python tools/validate.py mirror        read-only health check of a live Mirror

The emulator suite installs a debug build on a fresh Android 6 emulator (the
MIRROR's API level and WebView generation), then drives it the way an owner
would: it pairs, changes settings through the control API, reads the dashboard
page through WebView DevTools and looks at the screen. It refuses to run on a
physical device.

The mirror check only reads: it reports what a paired Mirror says about
itself and what in that needs attention. It changes nothing on the Mirror.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import http.client
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

import android_emulator
import screen_capture
import webview_devtools


REPO = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "dev.mirror.repurpose"
ACTIVITY = f"{PACKAGE}/.MainActivity"
DEVICE_PORT = 8787
DEBUG_APK = (
    REPO / "android" / "mirror-home" / "build" / "outputs" / "apk" / "debug"
    / "mirror-home-debug.apk"
)
ZONE_TABLE = REPO / "android" / "mirror-home" / "src" / "main" / "assets" / "zone-offsets.json"
DEFAULT_MIRROR_CONFIG = REPO / ".secrets" / "mirror-background-video.json"
DEFAULT_OTA_CONFIG = REPO / ".secrets" / "mirror-ota.json"
MIRROR_FINGERPRINT_MARKER = "mirror/mirror/"
DASHBOARD_PAGE = "/dashboard/custom.html"
OFFLINE_PAGE = "/dashboard/offline.html"
# Nothing listens on the discard port, so this page can never load.
UNREACHABLE_PAGE = "http://127.0.0.1:9/unreachable"
CONTROL_ASSETS = (
    "/",
    "/app.js",
    "/clock.js",
    "/styles.css",
    "/manifest.webmanifest",
    "/icon.svg",
    "/dashboard/mirror.js",
    "/dashboard/mirror.css",
    "/dashboard/custom.html",
    "/dashboard/custom.js",
    "/dashboard/custom.css",
    "/dashboard/offline.html",
    "/dashboard/offline.js",
    "/dashboard/offline.css",
)
# Fades last three seconds to sleep and two to wake; allow for a slow host.
FADE_SETTLE_SECONDS = 12
# A sleeping display is black everywhere: no pixel may stay lit.
BLACK_PEAK = 2
# The dashboard's strokes are near white; a fade is only visible from this up.
MINIMUM_AWAKE_PEAK = 128
# The dashboard is lit strokes on true black: at most this much of it is lit.
MAX_DASHBOARD_LIT = 0.1
# Mirror Home counts a process stopped within this long of starting as an
# early stop rather than as its previous run (RunHistory.EARLY_STOP_MS).
EARLY_STOP_SECONDS = 10
# Android 6 stops a HOME app once while replacing it; more is a start-up loop.
MAX_EARLY_STOPS = 2
# Android stops the OTA supervisor when memory is short and restarts it within
# seconds; only a silence longer than this is a fault.
SUPERVISOR_RESTART_SECONDS = 30
# Mirror Home takes the display back from another screen in about fifteen
# seconds; only a dashboard that stays covered for longer is a fault.
DASHBOARD_RETURN_SECONDS = 30
# What Android says has input focus: a component, or a system window's title.
FOCUSED_WINDOW = re.compile(r"mCurrentFocus=Window\{\S+ \S+ ([^}]+)\}")
# The window Android 6 puts up when an app crashes or stops answering.
SYSTEM_DIALOG = re.compile(r"Application (?:Error|Not Responding): .+")
# A Mirror keeps its factory launcher installed as a second HOME app, with
# Mirror Home the preferred one. The emulator's own launcher plays that part.
STOCK_LAUNCHER = "com.android.launcher3"
HOME_REQUEST = ("am", "start", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME")
HOME_CHOOSER = "com.android.internal.app.ResolverActivity"
HOME_LABEL = "Mirror Home"
# Mirror Home looks every five seconds and gives another screen ten
# (ForegroundKeeper.COVERED_GRACE_MS) before it takes the display back.
COVERED_GRACE_SECONDS = 10
RETURN_SECONDS = 45
WAKE_SECONDS = 30
# Said of every check that could not run because the emulator had gone.
EMULATOR_LOST = (
    "The emulator froze or exited during this run, so this check says nothing about "
    "Mirror Home"
)
# Android 6 reboots in well under a minute, even on a shared CI runner.
REBOOT_SECONDS = 180
KEY_MENU = "82"
KEY_SLEEP = "223"
KEY_WAKEUP = "224"
KERNEL_WAKE_LOCK = "mirror-validation"
# What Android keeps about crashes, even across a reboot.
CRASH_RECORDS = ("data_app_crash", "data_app_anr", "system_app_crash", "system_app_anr", "SYSTEM_TOMBSTONE")
# Every other check uses the pairing these two make.
PREREQUISITE_CHECKS = ("install", "first-pairing")
# The system's own window while an app starts is bright in a light theme.
BRIGHT_FRAME_LEVEL = 40.0
CLOCK_BOX = (0.0, 0.0, 0.7, 0.3)


class CheckFailed(AssertionError):
    pass


class CheckSkipped(Exception):
    pass


@dataclass
class Reply:
    status: int
    body: object
    headers: dict


class Api:
    """The Mirror's control API, with statuses returned rather than raised."""

    def __init__(self, host: str, port: int, token: str | None = None):
        self.host = host
        self.port = port
        self.token = token

    def call(
        self,
        method: str,
        path: str,
        body: object = None,
        *,
        token: str | None | bool = True,
        data: bytes | None = None,
        content_type: str | None = None,
        timeout: float = 20.0,
    ) -> Reply:
        headers = {"Accept": "application/json"}
        payload = data
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        bearer = self.token if token is True else token
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        finally:
            connection.close()
        reply_headers = {name.lower(): value for name, value in response.getheaders()}
        parsed: object = raw
        if "json" in reply_headers.get("content-type", ""):
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        return Reply(response.status, parsed, reply_headers)

    def expect(self, method: str, path: str, body: object = None, *, status: int = 200, **options):
        reply = self.call(method, path, body, **options)
        if reply.status != status:
            raise CheckFailed(
                f"{method} {path} answered {reply.status}, expected {status}: "
                f"{describe(reply.body)}"
            )
        return reply.body


class Adb:
    def __init__(self, executable: pathlib.Path, serial: str):
        self.executable = executable
        self.serial = serial

    def run(self, *arguments: str, timeout: float = 120.0, check: bool = True) -> str:
        try:
            completed = subprocess.run(
                [str(self.executable), "-s", self.serial, *arguments],
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as hung:
            # An emulator that has frozen keeps its connection and never answers.
            if check:
                raise CheckFailed(
                    f"adb {' '.join(arguments)} did not finish within {int(timeout)} s; "
                    f"{self.serial} has stopped answering"
                ) from None
            return (hung.stdout or b"").decode("utf-8", errors="replace")
        output = completed.stdout.decode("utf-8", errors="replace")
        if check and completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip() or output.strip()
            raise CheckFailed(f"adb {' '.join(arguments)} failed: {detail}")
        return output

    def shell(self, *arguments: str, **options) -> str:
        return self.run("shell", *arguments, **options)

    def property(self, name: str) -> str:
        return self.shell("getprop", name).strip()

    def capture(self) -> screen_capture.Screenshot:
        try:
            completed = subprocess.run(
                [str(self.executable), "-s", self.serial, "exec-out", "screencap"],
                capture_output=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            raise CheckFailed(
                f"adb could not capture the screen within 60 s; {self.serial} has stopped answering"
            ) from None
        if completed.returncode != 0:
            raise CheckFailed("adb could not capture the screen")
        return screen_capture.parse_raw(completed.stdout)

    def answers(self, timeout: float = 20.0) -> bool:
        """Whether the device still runs commands; a frozen emulator does not."""
        return "answering" in self.shell("echo", "answering", timeout=timeout, check=False)

    def forward(self, remote: str) -> int:
        return int(self.run("forward", "tcp:0", remote).strip())

    def remove_forward(self, port: int) -> None:
        self.run("forward", "--remove", f"tcp:{port}", check=False)


def focused_window(adb: Adb) -> str:
    """The component or system window that has input focus, or nothing."""
    found = FOCUSED_WINDOW.search(adb.shell("dumpsys", "window", "windows", check=False))
    return found.group(1) if found else ""


def window_text(adb: Adb) -> str:
    """The view hierarchy of what is on the glass, with the text of native views."""
    target = "/data/local/tmp/mirror-window.xml"
    adb.shell("uiautomator", "dump", target, check=False)
    return adb.shell("cat", target, check=False)


def node_center(hierarchy: str, attribute: str, value: str) -> tuple[int, int] | None:
    """The middle of the first view whose attribute has this value."""
    found = re.search(
        rf'{attribute}="{re.escape(value)}"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        hierarchy,
    )
    if not found:
        return None
    left, top, right, bottom = (int(number) for number in found.groups())
    return (left + right) // 2, (top + bottom) // 2


def tap(adb: Adb, point: tuple[int, int]) -> None:
    adb.shell("input", "tap", str(point[0]), str(point[1]))


def describe(value: object, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def require(condition: object, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def wait_for(
    description: str,
    probe: Callable[[], object],
    *,
    timeout: float,
    interval: float = 0.5,
) -> object:
    """Poll until ``probe`` returns something truthy; say what it last saw."""
    deadline = time.monotonic() + timeout
    last: object = None
    while True:
        try:
            last = probe()
        except (OSError, http.client.HTTPException, webview_devtools.DevToolsError) as error:
            last = f"{type(error).__name__}: {error}"
        else:
            if last:
                return last
        if time.monotonic() >= deadline:
            raise CheckFailed(f"Timed out after {int(timeout)} s waiting for {description}; last saw {describe(last)}")
        time.sleep(interval)


def clock_text(epoch_ms: int, offset_minutes: int, clock_24_hour: bool) -> str:
    """What the dashboard clock shows at an instant, as its digits and meridiem."""
    local = datetime.datetime.fromtimestamp(
        (epoch_ms + offset_minutes * 60_000) / 1000, datetime.timezone.utc
    )
    if clock_24_hour:
        return f"{local.hour:02d}:{local.minute:02d}"
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'PM' if local.hour >= 12 else 'AM'}"


def zone_timeline(table: dict, zone: str, now_ms: int) -> tuple[int, list[dict]]:
    """The bundled table's offset now and its later changes, in API form."""
    base, changes = table["rules"][table["zones"][zone]]
    offset = base
    upcoming = []
    for minute, value in changes:
        if minute * 60_000 <= now_ms:
            offset = value
        else:
            upcoming.append({"at": minute * 60_000, "utcOffsetMinutes": value})
    return offset, upcoming


def awake_window(now_ms: int, offset_minutes: int) -> tuple[str, str]:
    """Wake and sleep times that keep a schedule awake now and for six hours."""
    local = datetime.datetime.fromtimestamp(
        (now_ms + offset_minutes * 60_000) / 1000, datetime.timezone.utc
    )
    wake = local - datetime.timedelta(hours=2)
    sleep = local + datetime.timedelta(hours=6)
    return f"{wake.hour:02d}:{wake.minute:02d}", f"{sleep.hour:02d}:{sleep.minute:02d}"


def noon_and_midnight_offsets(now_ms: int) -> tuple[int, int]:
    """Whole-hour UTC offsets that make it about noon and about midnight now."""
    utc_hour = datetime.datetime.fromtimestamp(now_ms / 1000, datetime.timezone.utc).hour
    noon = (12 - utc_hour + 12) % 24 - 12
    midnight = noon - 12 if noon > 0 else noon + 12
    return noon * 60, midnight * 60


@dataclass
class Result:
    name: str
    title: str
    status: str = "pass"
    seconds: float = 0.0
    message: str = ""
    details: dict = field(default_factory=dict)


class Context:
    """State shared by the emulator checks."""

    def __init__(self, adb: Adb, api: Api, output: pathlib.Path):
        self.adb = adb
        self.api = api
        self.output = output
        self.details: dict = {}
        self.forwards: list[int] = []
        self.console_errors_before_restarts = 0
        self.zone_table = json.loads(ZONE_TABLE.read_text(encoding="utf-8"))
        self.awake_peak = 0
        self.debuggable: bool | None = None
        self.apk: pathlib.Path | None = None
        self.kept: dict = {}
        self.dismissed: list[str] = []
        # The emulator this run started, if it did; its process can end under it.
        self.emulator: android_emulator.Emulator | None = None
        self.emulator_lost = False

    def note(self, key: str, value: object) -> None:
        self.details[key] = value

    def require_emulator_alive(self) -> None:
        """Fail at once, and for every later check, when the emulator is gone.

        Some emulator releases freeze or exit part-way through a run. Without
        this every remaining check would wait out its own time limits, and
        its failure would read as a fault in Mirror Home.
        """
        if not self.emulator_lost and self.emulator is not None and self.emulator.exited():
            self.emulator_lost = True
        if self.emulator_lost:
            raise CheckFailed(EMULATOR_LOST)

    def health(self) -> dict:
        return self.api.expect("GET", "/api/v1/health")

    def status(self) -> dict:
        return self.api.expect("GET", "/api/v1/status")

    def device_now(self) -> int:
        return int(self.health()["now"])

    def screenshot(self, name: str) -> screen_capture.Screenshot:
        shot = self.adb.capture()
        (self.output / f"{name}.png").write_bytes(screen_capture.to_png(shot))
        return shot

    def native_text(self) -> str:
        """The view hierarchy of what is on the glass, with the text of native views.

        This is how the setup screen is read on any build: it needs neither
        WebView debugging nor an API that an earlier build may not have.
        """
        return window_text(self.adb)

    def before_check(self) -> None:
        """Close crash dialogs of the emulator's own apps, and remember them.

        The stock image's telephony apps stop now and then on a device with
        as little memory as a Mirror. Android 6 shows no dialog for that on a
        device without input devices, which is what this suite's emulator
        and a Mirror are; on an emulator with a touchscreen the dialog would
        cover the dashboard. A dialog about Mirror Home is left for the
        checks to find.
        """
        self.require_emulator_alive()
        for _ in range(4):
            focus = focused_window(self.adb)
            if not focus and not self.adb.answers():
                self.emulator_lost = True
                raise CheckFailed(EMULATOR_LOST)
            if not SYSTEM_DIALOG.fullmatch(focus) or PACKAGE in focus:
                return
            button = node_center(self.native_text(), "text", "OK")
            if not button:
                return
            tap(self.adb, button)
            self.dismissed.append(focus)
            print(f"      closed the emulator's own dialog: {focus}", flush=True)
            time.sleep(1)

    def inspectable(self) -> bool:
        """Whether the page can be read over DevTools: debuggable builds only."""
        if self.debuggable is None:
            self.debuggable = bool(self.health().get("debuggable"))
        return self.debuggable

    def require_inspectable(self) -> None:
        if not self.inspectable():
            raise CheckSkipped("a release build does not let its page be inspected")

    @contextlib.contextmanager
    def page(self, suffix: str = DASHBOARD_PAGE, timeout: float = 40.0):
        """The WebView page whose URL ends with ``suffix``, over DevTools."""
        self.require_inspectable()
        deadline = time.monotonic() + timeout
        last = "no debuggable WebView"
        while True:
            sockets = webview_devtools.devtools_sockets(
                self.adb.shell("cat", "/proc/net/unix")
            )
            for name in sockets:
                port = self.adb.forward(f"localabstract:{name}")
                try:
                    candidates = [
                        page for page in webview_devtools.list_pages(port)
                        if page.get("url", "").endswith(suffix)
                    ]
                    if candidates:
                        with webview_devtools.Page(port, candidates[0]) as page:
                            yield page
                        return
                    last = f"pages {[page.get('url') for page in webview_devtools.list_pages(port)]}"
                except webview_devtools.DevToolsError as error:
                    last = str(error)
                finally:
                    self.adb.remove_forward(port)
            if time.monotonic() >= deadline:
                raise CheckFailed(f"No WebView page ending in {suffix}: {last}")
            time.sleep(1)

    def wait_dashboard(self, suffix: str = DASHBOARD_PAGE, timeout: float = 60.0) -> dict:
        """Wait for a page to finish loading; the built-in one must have its renderer."""

        def loaded():
            dashboard = self.health()["dashboard"]
            ready = dashboard["phase"] == "page-finished" and dashboard["url"].endswith(suffix)
            if ready and suffix == DASHBOARD_PAGE:
                ready = dashboard["pageComplete"] and dashboard["rendererPresent"]
            return dashboard if ready else None

        return wait_for(f"the dashboard to load {suffix}", loaded, timeout=timeout)

    def restart_home(self) -> None:
        """Stop Mirror Home, keeping count of what its counters held.

        Nothing starts it again here: Android does, because it is the HOME app.
        """
        self.console_errors_before_restarts += self.health()["dashboard"]["consoleErrors"]
        self.adb.shell("am", "force-stop", PACKAGE)

    def set_clock(self, zone: str, offset: int, changes: list | None, *, clock_24_hour: bool = False) -> dict:
        body = {"timeZone": zone, "utcOffsetMinutes": offset, "clock24Hour": clock_24_hour}
        if changes is not None:
            body["utcOffsetChanges"] = changes
        return self.api.expect("PUT", "/api/v1/preferences", body)

    def set_automation(self, **overrides) -> dict:
        body = {
            "enabled": False,
            "wakeTime": "06:00",
            "sleepTime": "22:00",
            "wakeBrightness": 180,
            "ambientEnabled": False,
            "ambientMinimum": 20,
            "ambientMaximum": 220,
            "motionEnabled": False,
            "motionTimeoutSeconds": 300,
            "motionSensitivity": 6,
        }
        body.update(overrides)
        return self.api.expect("PUT", "/api/v1/automation", body)

    def wait_peak(
        self,
        description: str,
        accept: Callable[[int], bool],
        name: str,
        *,
        mostly_black: bool = False,
    ) -> int:
        """Capture until the brightest pixel satisfies ``accept``; keep the frame.

        With ``mostly_black`` the frame must also be the dashboard's: lit
        strokes on black. Another app's screen is bright too, but all over.
        """
        deadline = time.monotonic() + FADE_SETTLE_SECONDS
        while True:
            shot = self.adb.capture()
            peak = screen_capture.peak_level(shot)
            lit = screen_capture.lit_fraction(shot, 24) if mostly_black else 0.0
            if accept(peak) and lit <= MAX_DASHBOARD_LIT:
                (self.output / f"{name}.png").write_bytes(screen_capture.to_png(shot))
                return peak
            if time.monotonic() >= deadline:
                (self.output / f"{name}-timeout.png").write_bytes(screen_capture.to_png(shot))
                detail = f"; {lit:.0%} of it is lit" if mostly_black else ""
                raise CheckFailed(f"The screen never became {description}; brightest pixel {peak}{detail}")

    def lit_peak(self) -> int:
        """The awake dashboard's brightest pixel, measured once per run.

        The dashboard check measures it; a check run on its own with
        ``--only`` waits here for the dashboard to load and fade in.
        """
        if not self.awake_peak:
            self.wait_dashboard()
            self.awake_peak = self.wait_peak(
                "lit", lambda peak: peak >= MINIMUM_AWAKE_PEAK, "awake-reference"
            )
        return self.awake_peak

    def wait_lit(self, name: str) -> int:
        """Wait until the dashboard is visible again, as bright as it was.

        A page can finish loading while the glass is still black, so a
        restart is only over once the dashboard has faded back in.
        """
        reference = self.lit_peak()
        return self.wait_peak(
            "the dashboard again", lambda peak: peak >= reference * 0.8, name, mostly_black=True
        )


# ---------------------------------------------------------------------------
# Emulator checks, in the order an owner's first hour would exercise them.
# ---------------------------------------------------------------------------


def check_install(ctx: Context) -> None:
    bootstrap = wait_for(
        "Mirror Home's API",
        lambda: ctx.api.call("GET", "/api/v1/bootstrap", token=None).body,
        timeout=60,
    )
    require(bootstrap.get("apiVersion") == 1, f"Unexpected API version: {bootstrap}")
    installed = re.search(
        r"versionName=(\S+)", ctx.adb.shell("dumpsys", "package", PACKAGE)
    )
    require(installed, "The package manager does not list Mirror Home")
    require(
        bootstrap.get("appVersion") == installed.group(1),
        f"API reports {bootstrap.get('appVersion')}, package is {installed.group(1)}",
    )
    ctx.note("appVersion", bootstrap["appVersion"])


def check_setup_screen(ctx: Context) -> None:
    bootstrap = ctx.api.expect("GET", "/api/v1/bootstrap", token=None)
    require(bootstrap["paired"] is False, "A fresh install reports itself paired")
    require(bootstrap["pairingOpen"] is True, "The setup screen does not open pairing")
    wait_for(
        "the native setup screen",
        lambda: ctx.health()["dashboard"]["phase"] == "native",
        timeout=20,
    )
    runtime = ctx.api.expect("GET", "/api/v1/dashboard/runtime", token=None)
    require(
        re.fullmatch(r"\d{6}", runtime.get("pairingCode", "")),
        "The setup screen's pairing code is not six digits",
    )
    # The QR card is the one large white shape on an otherwise black screen.
    card = (0.3, 0.35, 0.7, 0.65)
    wait_for(
        "the setup screen to draw its QR card",
        lambda: (lambda shot: 0.2 < screen_capture.lit_fraction(shot, 200, card) < 0.9
                 and screen_capture.lit_fraction(shot, 24) < 0.3)(ctx.adb.capture()),
        timeout=20,
    )
    shot = ctx.screenshot("setup-screen")
    ctx.note("setupScreenQrLit", round(screen_capture.lit_fraction(shot, 200, card), 3))
    ctx.note("setupScreenLit", round(screen_capture.lit_fraction(shot, 24), 3))


def check_first_pairing(ctx: Context) -> None:
    code = ctx.api.expect("GET", "/api/v1/dashboard/runtime", token=None)["pairingCode"]
    wrong = "000000" if code != "000000" else "111111"
    refused = ctx.api.call("POST", "/api/v1/pair", {"code": wrong, "name": "Wrong"}, token=None)
    require(
        refused.status == 401 and refused.body.get("reason") == "wrong-code",
        f"A wrong code was not refused as wrong: {refused.status} {refused.body}",
    )
    require(
        ctx.api.call("GET", "/api/v1/clients", token=None).status == 401,
        "The client list is readable without a credential",
    )
    now = ctx.device_now()
    offset, _ = zone_timeline(ctx.zone_table, "America/Los_Angeles", now)
    paired = ctx.api.expect(
        "POST",
        "/api/v1/pair",
        {
            "code": code,
            "name": "Validation suite",
            "timeZone": "America/Los_Angeles",
            "utcOffsetMinutes": offset,
        },
        token=None,
    )
    require(paired.get("token"), "Pairing returned no credential")
    ctx.api.token = paired["token"]
    clients = ctx.api.expect("GET", "/api/v1/clients")["clients"]
    require(
        [client["name"] for client in clients] == ["Validation suite"],
        f"Unexpected paired clients: {clients}",
    )
    reused = ctx.api.call("POST", "/api/v1/pair", {"code": code, "name": "Again"}, token=None)
    require(reused.status != 200, "A pairing code worked twice")


def check_dashboard(ctx: Context) -> None:
    dashboard = ctx.wait_dashboard()
    require(dashboard["rendererPresent"], f"The page loaded without its renderer: {dashboard}")
    if ctx.inspectable():
        with ctx.page() as page:
            seen = json.loads(wait_for(
                "the dashboard to draw its clock",
                lambda: (lambda value: value if json.loads(value)["clock"] else None)(page.evaluate(
                    "JSON.stringify({"
                    "agent: navigator.userAgent,"
                    "widgets: document.querySelectorAll('.mr-widget').length,"
                    "clock: (document.querySelector('.mr-clock .mr-digits') || {}).textContent || '',"
                    "date: (document.querySelector('.mr-date .mr-inner') || {}).textContent || '',"
                    "stage: document.getElementById('dashboard').className,"
                    "width: window.innerWidth, height: window.innerHeight})"
                )),
                timeout=30,
            ))
        require(re.fullmatch(r"\d{1,2}:\d{2}", seen["clock"]), f"Clock shows {seen['clock']!r}")
        require(re.search(r"\w+, \w+ \d{1,2}", seen["date"]), f"Date shows {seen['date']!r}")
        require(seen["widgets"] >= 2, f"Only {seen['widgets']} widgets are on the glass")
        engine = re.search(r"Chrome/([\d.]+)", seen["agent"])
        ctx.note("webViewEngine", engine.group(1) if engine else seen["agent"])
        ctx.note("viewport", f"{seen['width']}x{seen['height']}")
    else:
        ctx.note("pageInspected", False)
    wait_for(
        "the dashboard to fade in",
        lambda: screen_capture.peak_level(ctx.adb.capture(), CLOCK_BOX) >= 200,
        timeout=15,
    )
    shot = ctx.screenshot("dashboard")
    clock = screen_capture.lit_fraction(shot, 160, CLOCK_BOX)
    dark = 1.0 - screen_capture.lit_fraction(shot, 24)
    require(clock > 0.005, f"The clock area is not lit (lit {clock:.4f})")
    require(
        dark >= 1.0 - MAX_DASHBOARD_LIT,
        f"Only {dark:.2%} of the dashboard is black; the mirror needs true black",
    )
    ctx.awake_peak = screen_capture.peak_level(shot)
    ctx.note("dashboardBlack", round(dark, 4))
    errors = ctx.health()["dashboard"]["consoleErrors"]
    require(errors == 0, f"The dashboard logged {errors} script errors: {ctx.health()['dashboard']['recentConsoleErrors']}")


def check_clock_bundled(ctx: Context) -> None:
    now = ctx.device_now()
    offset, upcoming = zone_timeline(ctx.zone_table, "America/Los_Angeles", now)
    saved = ctx.set_clock("America/Los_Angeles", offset, None)
    require(saved["clockSource"] == "bundled", f"Clock source is {saved['clockSource']}")
    require(saved["utcOffsetMinutes"] == offset, f"Offset is {saved['utcOffsetMinutes']}")
    require(
        saved["utcOffsetChanges"] == upcoming[: len(saved["utcOffsetChanges"])]
        and len(saved["utcOffsetChanges"]) == min(8, len(upcoming)),
        f"Upcoming changes differ from the bundled table: {saved['utcOffsetChanges'][:2]}",
    )
    # An offset this zone never uses means the label is wrong: keep the offset.
    fixed = ctx.set_clock("America/Los_Angeles", 345, None)
    require(
        fixed["clockSource"] == "fixed" and fixed["utcOffsetChanges"] == [],
        f"A mismatched offset was not kept fixed: {fixed}",
    )
    explicit = ctx.set_clock("Asia/Kathmandu", 345, [])
    require(explicit["clockSource"] == "client", f"Client changes were not stored: {explicit}")
    unordered = ctx.api.call("PUT", "/api/v1/preferences", {
        "timeZone": "Etc/UTC",
        "utcOffsetMinutes": 0,
        "utcOffsetChanges": [
            {"at": now + 200_000, "utcOffsetMinutes": 60},
            {"at": now + 100_000, "utcOffsetMinutes": 0},
        ],
    })
    require(
        unordered.status == 400 and "time order" in unordered.body.get("error", ""),
        f"Out-of-order changes were accepted: {unordered.status} {unordered.body}",
    )
    require(
        ctx.api.expect("GET", "/api/v1/preferences")["timeZone"] == "Asia/Kathmandu",
        "A rejected clock update changed the saved zone",
    )
    ctx.set_clock("America/Los_Angeles", offset, None)
    ctx.note("nextBundledChange", upcoming[0] if upcoming else None)


def check_clock_switch(ctx: Context) -> None:
    ctx.require_inspectable()

    def glass(page) -> dict:
        return json.loads(page.evaluate(
            "JSON.stringify({now: Date.now(),"
            "clock: (document.querySelector('.mr-clock .mr-digits') || {}).textContent || ''})"
        ))

    with ctx.page() as page:
        switch_at = glass(page)["now"] + 14_000
        saved = ctx.set_clock(
            "Etc/UTC", 0, [{"at": switch_at, "utcOffsetMinutes": 60}], clock_24_hour=True
        )
        require(saved["clockSource"] == "client", f"Clock source is {saved['clockSource']}")
        status = ctx.status()
        require(
            status["nextUtcOffsetChange"] == {"at": switch_at, "utcOffsetMinutes": 60},
            f"Status does not announce the change: {status['nextUtcOffsetChange']}",
        )

        def shows(offset: int):
            seen = glass(page)
            return seen if seen["clock"] == clock_text(seen["now"], offset, True) else None

        before = wait_for("the clock to show the first offset", lambda: shows(0), timeout=10)
        require(before["now"] < switch_at, "The first offset was not seen before the change")
        after = wait_for(
            "the clock to switch offsets",
            lambda: (lambda seen: seen if seen and seen["now"] >= switch_at else None)(shows(60)),
            timeout=30,
            interval=0.2,
        )
        delay = after["now"] - switch_at
        # The page refreshes its runtime every five seconds; switching sooner
        # than that shows it applied the announced change by itself.
        require(delay < 4_000, f"The clock took {delay} ms to follow the change")
        ctx.note("clockSwitchDelayMs", delay)
    status = wait_for(
        "status to report the new offset",
        lambda: (lambda value: value if value["utcOffsetMinutes"] == 60 else None)(ctx.status()),
        timeout=10,
    )
    require(status["nextUtcOffsetChange"] is None, "A used change is still announced")


def check_schedule_switch(ctx: Context) -> None:
    lit = ctx.lit_peak()
    now = ctx.device_now()
    noon, midnight = noon_and_midnight_offsets(now)
    switch_at = now + 10_000
    try:
        ctx.set_clock(
            "Etc/UTC", noon, [{"at": switch_at, "utcOffsetMinutes": midnight}], clock_24_hour=True
        )
        awake = ctx.set_automation(enabled=True, wakeTime="06:00", sleepTime="22:00")
        require(awake["sleeping"] is False, f"Asleep at local noon: {awake}")
        asleep = wait_for(
            "the schedule to put the display to sleep after the clock change",
            lambda: (lambda value: value if value["sleeping"] else None)(
                ctx.api.expect("GET", "/api/v1/automation")
            ),
            timeout=40,
        )
        require(asleep["sleepReason"] == "schedule", f"Sleep reason is {asleep['sleepReason']}")
        require(ctx.device_now() >= switch_at, "The display slept before the clock change")
        ctx.wait_peak("black", lambda peak: peak <= BLACK_PEAK, "schedule-asleep")
    finally:
        # Whatever happened, the next check gets an awake display on local time.
        ctx.set_automation(enabled=False)
        offset, _ = zone_timeline(ctx.zone_table, "America/Los_Angeles", ctx.device_now())
        ctx.set_clock("America/Los_Angeles", offset, None)
    wait_for(
        "the display to wake when the schedule is turned off",
        lambda: not ctx.api.expect("GET", "/api/v1/automation")["sleeping"],
        timeout=20,
    )
    ctx.wait_peak("lit again", lambda peak: peak >= lit * 0.8, "schedule-awake")


def check_sleep_wake(ctx: Context) -> None:
    ctx.lit_peak()
    awake = screen_capture.peak_level(ctx.adb.capture())
    require(
        awake >= MINIMUM_AWAKE_PEAK,
        f"The dashboard is too dim to observe a fade (brightest pixel {awake})",
    )
    faded = False
    for attempt in range(3):
        slept = ctx.api.expect("POST", "/api/v1/automation/sleep", {})
        require(slept["sleeping"] is True, "Sleep was not reported at once")
        peaks = []
        deadline = time.monotonic() + FADE_SETTLE_SECONDS
        while time.monotonic() < deadline:
            peaks.append(screen_capture.peak_level(ctx.adb.capture()))
            if peaks[-1] <= BLACK_PEAK:
                break
        require(peaks[-1] <= BLACK_PEAK, f"Sleep left a pixel lit at {peaks[-1]}")
        # A frame partway down proves a fade rather than a cut to black.
        faded = any(awake * 0.1 < peak < awake * 0.9 for peak in peaks)
        ctx.note("sleepFadePeaks", peaks)
        if faded or attempt == 2:
            break
        ctx.api.expect("POST", "/api/v1/automation/wake", {})
        ctx.wait_peak("lit again", lambda peak: peak >= awake * 0.8, "sleep-retry-awake")
    require(faded, "No partly faded frame was seen; the display cut to black")
    ctx.screenshot("asleep")
    woke = ctx.api.expect("POST", "/api/v1/automation/wake", {})
    require(woke["sleeping"] is False, "Wake was not reported at once")
    restored = ctx.wait_peak("lit again", lambda peak: peak >= awake * 0.8, "awake")
    ctx.note("awakePeak", awake)
    ctx.note("restoredPeak", restored)
    # Saving the schedule ends the four-hour manual override these left behind.
    ctx.set_automation(enabled=False)


def check_pairing_closed(ctx: Context) -> None:
    wait_for(
        "pairing to close once no code is on display",
        lambda: ctx.api.expect("GET", "/api/v1/bootstrap", token=None)["pairingOpen"] is False,
        timeout=60,
        interval=2,
    )
    wrong_before = ctx.health()["pairing"]["wrongCodes"]
    for code in ("000000", "123456", ""):
        refused = ctx.api.call("POST", "/api/v1/pair", {"code": code, "name": "Guess"}, token=None)
        require(
            refused.status == 403 and refused.body.get("reason") == "closed",
            f"A guess was examined while pairing was closed: {refused.status} {refused.body}",
        )
    require(
        ctx.health()["pairing"]["wrongCodes"] == wrong_before,
        "Guesses made while pairing was closed were counted as checked",
    )
    runtime = ctx.api.expect("GET", "/api/v1/dashboard/runtime", token=None)
    require("pairingCode" not in runtime, "The dashboard is handed a code it does not show")


def check_pairing_window(ctx: Context) -> None:
    require(
        ctx.api.call("POST", "/api/v1/pair/window", {}, token=None).status == 401,
        "A pairing window can be opened without a credential",
    )
    window = ctx.api.expect("POST", "/api/v1/pair/window", {})
    require(re.fullmatch(r"\d{6}", window["code"]), f"Window code is {window['code']!r}")
    require(540 <= window["expiresInSeconds"] <= 600, f"Window lasts {window['expiresInSeconds']} s")
    require(
        ctx.api.expect("GET", "/api/v1/bootstrap", token=None)["pairingOpen"] is True,
        "Show code did not open pairing",
    )
    second = ctx.api.expect(
        "POST", "/api/v1/pair", {"code": window["code"], "name": "Second device"}, token=None
    )
    require(
        ctx.api.call("GET", "/api/v1/status", token=second["token"]).status == 200,
        "The second device's credential does not work",
    )
    require(
        ctx.api.expect("GET", "/api/v1/bootstrap", token=None)["pairingOpen"] is False,
        "Pairing stayed open after the code was used",
    )
    again = ctx.api.call("POST", "/api/v1/pair", {"code": window["code"], "name": "Third"}, token=None)
    require(again.status == 403, f"A used window code answered {again.status}")
    names = sorted(client["name"] for client in ctx.api.expect("GET", "/api/v1/clients")["clients"])
    require(names == ["Second device", "Validation suite"], f"Paired clients: {names}")
    ctx.api.expect("POST", "/api/v1/clients/revoke", {"id": second["clientId"]})
    require(
        ctx.api.call("GET", "/api/v1/clients", token=second["token"]).status == 401,
        "A revoked credential still works",
    )


def check_pairing_lockout(ctx: Context) -> None:
    window = ctx.api.expect("POST", "/api/v1/pair/window", {})
    wrong = "000000" if window["code"] != "000000" else "111111"
    for attempt in range(5):
        refused = ctx.api.call("POST", "/api/v1/pair", {"code": wrong, "name": "Guess"}, token=None)
        require(
            refused.status == 401 and refused.body.get("reason") == "wrong-code",
            f"Wrong code {attempt + 1} answered {refused.status} {refused.body}",
        )
    locked = ctx.api.call(
        "POST", "/api/v1/pair", {"code": window["code"], "name": "Too late"}, token=None
    )
    require(
        locked.status == 429 and locked.body.get("reason") == "locked",
        f"The correct code was accepted during a lockout: {locked.status} {locked.body}",
    )
    retry = locked.body.get("retryAfterSeconds")
    require(isinstance(retry, int) and 1 <= retry <= 30, f"Lockout lasts {retry} s")
    require(locked.headers.get("retry-after") == str(retry), "Retry-After header is missing")
    require(
        ctx.api.expect("GET", "/api/v1/bootstrap", token=None)["pairingOpen"] is False,
        "Pairing reads as open during a lockout",
    )
    pairing = ctx.health()["pairing"]
    require(pairing["lockedForSeconds"] >= 1, f"Health does not show the lock: {pairing}")
    # An owner asking for a new code is trusted to clear the lock.
    fresh = ctx.api.expect("POST", "/api/v1/pair/window", {})
    third = ctx.api.expect(
        "POST", "/api/v1/pair", {"code": fresh["code"], "name": "After lockout"}, token=None
    )
    ctx.api.expect("POST", "/api/v1/clients/revoke", {"id": third["clientId"]})
    ctx.note("wrongCodesSeen", ctx.health()["pairing"]["wrongCodes"])


def check_pairing_widget(ctx: Context) -> None:
    ctx.require_inspectable()
    layout = ctx.api.expect("GET", "/api/v1/dashboard/layout")
    original = json.dumps(layout)
    for widget in layout["widgets"]:
        if widget["type"] == "pairing":
            widget["visible"] = True
            widget["opacity"] = 100
    ctx.api.expect("PUT", "/api/v1/dashboard/layout", layout)
    try:
        with ctx.page() as page:
            # The page redraws from its last runtime first, so wait for the
            # glass and the API to agree rather than taking the first digits.
            def agreed_code():
                shown = re.sub(r"\D", "", page.evaluate(
                    "(document.querySelector('.mr-pairing .mr-code') || {}).textContent || ''"
                ))
                held = ctx.api.expect("GET", "/api/v1/dashboard/runtime", token=None).get("pairingCode")
                return shown if len(shown) == 6 and shown == held else None

            shown = wait_for(
                "the Pairing code widget to show the current code", agreed_code, timeout=30
            )
        require(
            ctx.api.expect("GET", "/api/v1/bootstrap", token=None)["pairingOpen"] is True,
            "A code on the glass did not open pairing",
        )
        fourth = ctx.api.expect(
            "POST", "/api/v1/pair", {"code": shown, "name": "From the glass"}, token=None
        )
        ctx.api.expect("POST", "/api/v1/clients/revoke", {"id": fourth["clientId"]})
        ctx.screenshot("pairing-widget")
    finally:
        ctx.api.expect("PUT", "/api/v1/dashboard/layout", json.loads(original))
    wait_for(
        "pairing to close after the widget is hidden",
        lambda: ctx.api.expect("GET", "/api/v1/bootstrap", token=None)["pairingOpen"] is False,
        timeout=60,
        interval=2,
    )


def check_notes(ctx: Context) -> None:
    ctx.require_inspectable()
    # The controls turn the Note widget on when a note is first posted; do the same.
    layout = ctx.api.expect("GET", "/api/v1/dashboard/layout")
    original = json.dumps(layout)
    for widget in layout["widgets"]:
        if widget["id"] == "note":
            widget.update({"visible": True, "source": "latest", "note": ""})
    ctx.api.expect("PUT", "/api/v1/dashboard/layout", layout)
    text = f"Validation note {int(time.time())}"
    created = ctx.api.expect("POST", "/api/v1/notes", {"text": text}, status=201)
    try:
        with ctx.page() as page:
            wait_for(
                "the note to appear on the glass",
                lambda: text in page.evaluate("document.getElementById('dashboard').textContent"),
                timeout=30,
            )
            ctx.screenshot("note")
            ctx.api.expect("DELETE", f"/api/v1/notes/{created['note']['id']}")
            created = None
            wait_for(
                "the deleted note to leave the glass",
                lambda: text not in page.evaluate("document.getElementById('dashboard').textContent"),
                timeout=30,
            )
    finally:
        if created is not None:
            ctx.api.call("DELETE", f"/api/v1/notes/{created['note']['id']}")
        ctx.api.expect("PUT", "/api/v1/dashboard/layout", json.loads(original))


def check_offline_fallback(ctx: Context) -> None:
    ctx.api.expect("PUT", "/api/v1/dashboard", {"url": UNREACHABLE_PAGE})
    try:
        dashboard = ctx.wait_dashboard(OFFLINE_PAGE)
        require(
            dashboard["lastFailurePhase"] in ("load-error", "http-error"),
            f"The fallback loaded without a recorded failure: {dashboard}",
        )
        if ctx.inspectable():
            with ctx.page(OFFLINE_PAGE) as page:
                shown = wait_for(
                    "the offline clock",
                    lambda: page.evaluate("document.getElementById('time').textContent"),
                    timeout=20,
                )
            require(re.fullmatch(r"\d{1,2}:\d{2}", shown), f"The offline clock shows {shown!r}")
        ctx.wait_peak(
            "lit by the offline clock", lambda peak: peak >= MINIMUM_AWAKE_PEAK, "offline-fallback"
        )
    finally:
        ctx.api.expect("PUT", "/api/v1/dashboard", {"url": ""})
    ctx.wait_dashboard()


def check_control_page(ctx: Context) -> None:
    index = ctx.api.call("GET", "/", token=None)
    require(index.status == 200, f"The control page answered {index.status}")
    policy = index.headers.get("content-security-policy", "")
    require("default-src 'self'" in policy and "frame-ancestors 'none'" in policy, f"Weak policy: {policy}")
    require(index.headers.get("x-frame-options") == "DENY", "The control page may be framed")
    html = index.body.decode("utf-8")
    referenced = set(re.findall(r'(?:src|href)="(/[^"#]*)"', html))
    for path in sorted(referenced | set(CONTROL_ASSETS)):
        reply = ctx.api.call("GET", path, token=None)
        require(reply.status == 200, f"{path} answered {reply.status}")
        require(len(reply.body) > 0, f"{path} is empty")
    require(
        ctx.api.call("GET", "/zone-offsets.json", token=None).status != 200,
        "The bundled zone table is served to browsers",
    )
    ctx.note("controlAssets", len(referenced | set(CONTROL_ASSETS)))


def check_health(ctx: Context) -> None:
    require(
        ctx.api.call("GET", "/api/v1/clients", token="not-a-credential").status == 401,
        "An invalid credential is accepted",
    )
    health = ctx.health()
    display = health["device"]["display"]
    require(health["crashes"]["count"] == 0, f"Crashes recorded: {health['crashes']}")
    require(health["api"]["unhandledErrors"] == 0, f"Unhandled API errors: {health['api']}")
    require(health["activity"]["showing"], f"The dashboard is not in front: {health['activity']}")
    require(
        health["activity"]["selectedHome"],
        "Mirror Home is not the HOME app Android would start, as it is on a Mirror",
    )
    require(
        not any(health["device"]["input"].values()),
        f"This emulator has input devices, which a Mirror does not: {health['device']['input']}",
    )
    require(display["on"] and health["device"]["power"]["interactive"], "The display is reported off")
    require(health["device"]["sdk"] == 23, f"Not Android 6: SDK {health['device']['sdk']}")
    require(
        health["device"]["webView"]["versionName"].startswith("44."),
        f"WebView is {health['device']['webView']}, not the Mirror's generation",
    )
    require(
        (display["realWidthPixels"], display["realHeightPixels"]) == (1080, 1920),
        f"Display is {display}",
    )
    require(
        (display["widthPixels"], display["heightPixels"]) == (1080, 1920),
        f"The app does not get the whole panel: {display}",
    )
    require(0 < health["memory"]["pssKb"] < 600_000, f"Memory use is {health['memory']['pssKb']} KB")
    heap_mb = health["memory"]["javaHeapMaxKb"] // 1024
    require(
        heap_mb == android_emulator.HEAP_MB,
        f"This emulator gives an app a {heap_mb} MB heap, not a Mirror's {android_emulator.HEAP_MB} MB",
    )
    open_files = health["memory"]["openFiles"]
    require(open_files and open_files > 0, f"Open files were not counted: {open_files!r}")
    if ctx.inspectable():
        # Only a debuggable app may list its descriptors; compare the two counts.
        listed = ctx.adb.shell(
            "run-as", PACKAGE, "ls", f"/proc/{health['process']['pid']}/fd", check=False
        ).split()
        if listed and all(name.isdigit() for name in listed):
            require(
                abs(len(listed) - open_files) <= 16,
                f"Health counts {open_files} open files; the kernel lists {len(listed)}",
            )
            ctx.note("openFilesListed", len(listed))
    require(health["storage"]["dataFreeBytes"] > 0, "Free storage was not measured")
    require(health["clock"]["bundledTzdata"], "The bundled zone table did not load")
    require(health["otaSupervisor"] == {"installed": False}, f"Supervisor: {health['otaSupervisor']}")
    ctx.note("pssKb", health["memory"]["pssKb"])
    ctx.note("javaHeapMaxKb", health["memory"]["javaHeapMaxKb"])
    ctx.note("openFiles", health["memory"]["openFiles"])
    ctx.note("densityDpi", display["densityDpi"])
    ctx.note("bundledTzdata", health["clock"]["bundledTzdata"])
    ctx.note("power", health["device"]["power"])


def has_stock_launcher(ctx: Context) -> bool:
    return bool(ctx.adb.shell("pm", "path", STOCK_LAUNCHER, check=False).strip())


def cover_dashboard(ctx: Context) -> str:
    """Put another screen in front of the dashboard and say whose it is.

    The other HOME app is started the way Android starts it: as HOME. An
    emulator without one gets Android's settings instead.
    """
    if has_stock_launcher(ctx):
        ctx.adb.shell(*HOME_REQUEST, STOCK_LAUNCHER)
        return STOCK_LAUNCHER
    ctx.adb.shell("am", "start", "-a", "android.settings.SETTINGS")
    return "com.android.settings"


def as_after_an_update(ctx: Context) -> None:
    """Arrange the tasks the way an update can leave them on a Mirror.

    While Android 6 replaces Mirror Home it needs a HOME app, and if it asks
    at the wrong instant it starts the other one. The new Mirror Home then
    starts its dashboard itself, which makes it an ordinary task above that
    launcher rather than the HOME task it is after a boot.
    """
    ctx.adb.shell(*HOME_REQUEST, STOCK_LAUNCHER)
    wait_for(
        "the other HOME app to come up",
        lambda: focused_window(ctx.adb).startswith(f"{STOCK_LAUNCHER}/"),
        timeout=20,
    )
    # Android does not start Mirror Home again: it has a HOME app in front.
    ctx.restart_home()
    ctx.adb.shell("am", "start", "-n", ACTIVITY)
    wait_for(
        "the dashboard that Mirror Home starts itself",
        lambda: (lambda reply: reply.status == 200 and reply.body["activity"]["showing"])(
            ctx.api.call("GET", "/api/v1/health")
        ),
        timeout=60,
    )


def dashboard_returns(ctx: Context, name: str) -> float:
    """Cover the dashboard and require Mirror Home to bring it back; the seconds it took."""
    before = ctx.health()
    require(before["activity"]["showing"], f"The dashboard is not in front to begin with: {before['activity']}")
    recovery = before["activity"].get("recovery") or {}
    cover = cover_dashboard(ctx)
    covered_at = time.monotonic()
    try:
        wait_for(
            f"{cover} to cover the dashboard",
            lambda: not ctx.health()["activity"]["resumed"],
            timeout=20,
        )
        ctx.screenshot(f"{name}-covered")
        # Nobody can close that screen on a Mirror: it has no touchscreen.
        after = wait_for(
            "the dashboard to return to the front by itself",
            lambda: (lambda health: health if health["activity"]["showing"] else None)(ctx.health()),
            timeout=RETURN_SECONDS,
            interval=1,
        )
        seconds = time.monotonic() - covered_at
    finally:
        # It does not run on a Mirror, and its task would stay under the dashboard.
        ctx.adb.shell("am", "force-stop", cover, check=False)
    returned = after["activity"]["recovery"]
    ctx.note("coveredBy", cover)
    ctx.note("lastFront", returned["lastFront"])
    require(
        returned["relaunches"] == recovery.get("relaunches", 0) + 1 and returned["lastReason"] == "covered",
        f"The dashboard is back, but not because Mirror Home brought it back: {returned}",
    )
    require(
        seconds >= COVERED_GRACE_SECONDS - 3,
        f"The dashboard took the display back after {seconds:.0f} s; a screen that "
        f"only passes by must get {COVERED_GRACE_SECONDS} s",
    )
    require(
        after["activity"]["creates"] == before["activity"]["creates"],
        f"A second dashboard was created instead of the first returning: {after['activity']}",
    )
    focus = focused_window(ctx.adb)
    require(focus.startswith(f"{PACKAGE}/"), f"Android gives the focus to {focus or 'nothing'}")
    ctx.wait_dashboard()
    ctx.wait_lit(name)
    return round(seconds, 1)


def check_returns_to_front(ctx: Context) -> None:
    recovery = ctx.health()["activity"].get("recovery") or {}
    if recovery.get("attended"):
        raise CheckSkipped(
            "this emulator has an input device or a connected computer, so Mirror Home "
            "leaves another screen alone"
        )
    ctx.note("returnedAfterSeconds", dashboard_returns(ctx, "returns-to-front"))
    if has_stock_launcher(ctx):
        as_after_an_update(ctx)
        ctx.note("returnedAfterUpdateSeconds", dashboard_returns(ctx, "returns-after-update"))


def check_wakes_display(ctx: Context) -> None:
    before = ctx.health()
    require(before["activity"]["showing"], f"The dashboard is not in front to begin with: {before['activity']}")
    require(before["device"]["power"]["interactive"], "Android is already asleep")
    if before["activity"]["recovery"]["attended"]:
        raise CheckSkipped(
            "this emulator has an input device or a connected computer, so Mirror Home "
            "leaves a display that was turned off alone"
        )
    # A sleeping emulator stops its processor, and with it ADB. A Mirror's
    # keeps running, for the camera that senses presence; hold it the same way.
    held = ctx.adb.shell(f"echo {KERNEL_WAKE_LOCK} > /sys/power/wake_lock; cat /sys/power/wake_lock", check=False)
    if KERNEL_WAKE_LOCK not in held:
        raise CheckSkipped("this emulator's shell cannot keep the processor running while Android sleeps")
    wake_ups = before["activity"]["recovery"]["wakeUps"]
    try:
        # Android's own sleep, which turns the panel off. Mirror Home's sleep
        # only dims its window, and nothing on a Mirror would wake Android again.
        ctx.adb.shell("input", "keyevent", KEY_SLEEP)
        slept_at = time.monotonic()
        after = wait_for(
            "Mirror Home to wake the display and show the dashboard",
            lambda: (lambda health: health
                     if health["activity"]["showing"]
                     and health["device"]["power"]["interactive"]
                     and health["activity"]["recovery"]["wakeUps"] > wake_ups else None)(ctx.health()),
            timeout=WAKE_SECONDS,
            interval=1,
        )
    finally:
        # Left asleep, the emulator would stop answering once the lock is gone.
        ctx.adb.shell("input", "keyevent", KEY_WAKEUP, check=False)
        ctx.adb.shell(f"echo {KERNEL_WAKE_LOCK} > /sys/power/wake_unlock", check=False)
    ctx.note("wokeAfterSeconds", round(time.monotonic() - slept_at, 1))
    ctx.note("wakeUps", after["activity"]["recovery"]["wakeUps"] - wake_ups)
    require(after["device"]["display"]["on"], f"The panel is still off: {after['device']['display']}")
    wakefulness = re.search(r"mWakefulness=(\w+)", ctx.adb.shell("dumpsys", "power", check=False))
    require(
        wakefulness and wakefulness.group(1) == "Awake",
        f"Android reports itself {wakefulness.group(1) if wakefulness else 'in an unknown state'}",
    )
    require(
        after["activity"]["creates"] == before["activity"]["creates"],
        f"The dashboard was created again: {after['activity']}",
    )
    ctx.wait_dashboard()
    ctx.wait_lit("wakes-display")


def check_cold_start(ctx: Context) -> None:
    ctx.restart_home()
    brightest = 0.0
    frames = 0
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        shot = ctx.adb.capture()
        level = screen_capture.mean_level(shot)
        frames += 1
        if level > brightest:
            brightest = level
            if level > BRIGHT_FRAME_LEVEL:
                (ctx.output / "cold-start-bright.png").write_bytes(screen_capture.to_png(shot))
    ctx.note("startupFrames", frames)
    ctx.note("brightestStartupFrame", round(brightest, 2))
    require(
        brightest <= BRIGHT_FRAME_LEVEL,
        f"Starting Home lit the whole screen (frame level {brightest:.0f} of 255); "
        "on mirror glass that is a bright flash",
    )
    wait_for(
        "Mirror Home to answer after starting",
        lambda: ctx.api.call("GET", "/api/v1/health").status == 200,
        timeout=60,
    )
    ctx.wait_dashboard()
    ctx.wait_lit("cold-start")


def wait_past_start(ctx: Context) -> dict:
    """Health once Home has run for longer than an early stop."""
    return wait_for(
        "Home to have run for longer than a start-up",
        lambda: (lambda health: health
                 if health["process"]["uptimeSeconds"] > EARLY_STOP_SECONDS else None)(ctx.health()),
        timeout=EARLY_STOP_SECONDS + 30,
        interval=1,
    )


def wait_for_run(ctx: Context, run_id: int, interval: float = 0.5) -> dict:
    return wait_for(
        f"run {run_id} of Mirror Home to answer",
        lambda: (lambda reply: reply.body
                 if reply.status == 200 and reply.body["process"]["runId"] == run_id else None)(
            ctx.api.call("GET", "/api/v1/health")
        ),
        timeout=60,
        interval=interval,
    )


def check_restart(ctx: Context) -> None:
    before = wait_past_start(ctx)
    ctx.restart_home()
    after = wait_for_run(ctx, before["process"]["runId"] + 1)
    previous = after["process"]["previousRun"]
    require(previous["end"] == "killed", f"The stopped process is recorded as {previous['end']}")
    require(previous["runId"] == before["process"]["runId"], f"Previous run is {previous}")
    require(after["process"]["earlyStops"] == 0, f"Early stops: {after['process']['earlyStops']}")
    ctx.wait_dashboard()
    ctx.wait_lit("restart")
    require(
        ctx.api.call("GET", "/api/v1/clients").status == 200,
        "The credential did not survive a restart",
    )


def check_quick_restart(ctx: Context) -> None:
    before = wait_past_start(ctx)
    first = before["process"]["runId"]
    started = time.monotonic()
    ctx.console_errors_before_restarts += before["dashboard"]["consoleErrors"]
    ctx.adb.shell("am", "force-stop", PACKAGE)
    # Android starts its HOME app again at once. Once that process answers it
    # has recorded its start; stop it while it is still starting.
    wait_for_run(ctx, first + 1, interval=0.2)
    ctx.adb.shell("am", "force-stop", PACKAGE)
    lifetime = time.monotonic() - started
    after = wait_for(
        "Mirror Home to come back",
        lambda: (lambda reply: reply.body
                 if reply.status == 200 and reply.body["process"]["runId"] > first + 1 else None)(
            ctx.api.call("GET", "/api/v1/health")
        ),
        timeout=60,
    )
    ctx.note("stoppedAfterSeconds", round(lifetime, 1))
    ctx.wait_dashboard()
    ctx.wait_lit("quick-restart")
    if lifetime >= EARLY_STOP_SECONDS - 1:
        raise CheckSkipped(
            f"this host took {lifetime:.0f} s to restart Home twice, too slow to stop "
            "a process within its first seconds"
        )
    process = after["process"]
    require(process["runId"] == first + 2, f"Run {first} was followed by {process['runId']}")
    require(
        process["previousRun"]["runId"] == first and process["previousRun"]["end"] == "killed",
        f"The process stopped while starting is reported as the previous run: {process['previousRun']}",
    )
    require(process["earlyStops"] == 1, f"Early stops: {process['earlyStops']}")


def check_reboot(ctx: Context) -> None:
    before = ctx.health()
    ctx.console_errors_before_restarts += before["dashboard"]["consoleErrors"]
    zone = ctx.api.expect("GET", "/api/v1/preferences")["timeZone"]
    ctx.adb.run("reboot")
    time.sleep(5)

    def booted() -> bool:
        if ctx.emulator is not None and ctx.emulator.exited():
            ctx.emulator_lost = True
            raise CheckFailed(
                "The emulator exited when Android rebooted, which is a fault of this "
                "release of the emulator and not of Mirror Home"
            )
        return android_emulator.boot_completed(ctx.adb.executable, ctx.adb.serial)

    try:
        wait_for("Android to boot again", booted, timeout=REBOOT_SECONDS, interval=3)
    except CheckFailed:
        # Later checks would only wait for an emulator that is not coming back.
        ctx.emulator_lost = True
        raise
    ctx.api.port = ctx.adb.forward(f"tcp:{DEVICE_PORT}")
    ctx.forwards.append(ctx.api.port)
    # Nothing here starts Mirror Home or unlocks the screen: nobody would on a Mirror.
    after = wait_for(
        "Mirror Home to start on its own after boot",
        lambda: (lambda reply: reply.body if reply.status == 200 else None)(
            ctx.api.call("GET", "/api/v1/health")
        ),
        timeout=180,
        interval=2,
    )
    previous = after["process"]["previousRun"]
    require(previous["end"] == "reboot", f"The reboot is recorded as {previous['end']}")
    boots = (before["device"]["bootId"], after["device"]["bootId"])
    require(
        None in boots or boots[0] != boots[1],
        "The boot identifier did not change across a reboot",
    )
    ctx.note("bootIdKnown", None not in boots)
    require(after["device"]["uptimeSeconds"] < before["device"]["uptimeSeconds"], "The device did not restart")
    require(ctx.api.expect("GET", "/api/v1/bootstrap", token=None)["paired"], "Pairing was lost")
    require(ctx.api.expect("GET", "/api/v1/preferences")["timeZone"] == zone, "The clock was lost")
    ctx.wait_dashboard()
    ctx.wait_lit("reboot")
    activity = ctx.health()["activity"]
    require(
        activity["showing"] and activity["selectedHome"],
        f"The dashboard did not come to the front by itself after a reboot: {activity}",
    )


def check_script_errors(ctx: Context) -> None:
    dashboard = ctx.health()["dashboard"]
    total = ctx.console_errors_before_restarts + dashboard["consoleErrors"]
    require(
        total == 0,
        f"The dashboard logged {total} script errors during the run: {dashboard['recentConsoleErrors']}",
    )
    require(ctx.health()["api"]["unhandledErrors"] == 0, "The API hit an unhandled error")
    ctx.screenshot("final")


EMULATOR_CHECKS: list[tuple[str, str, Callable[[Context], None], bool]] = [
    ("install", "Mirror Home installs and its API answers", check_install, False),
    ("setup-screen", "Unpaired, the glass shows the setup screen with a pairing code", check_setup_screen, False),
    ("first-pairing", "The code on the glass pairs a client; a wrong code does not", check_first_pairing, False),
    ("dashboard", "The dashboard renders in the Android 6 WebView on true black", check_dashboard, False),
    ("clock-bundled", "A zone follows the bundled table unless a client says otherwise", check_clock_bundled, False),
    ("clock-switch", "The glass clock switches at the instant of an offset change", check_clock_switch, False),
    ("schedule-switch", "The sleep schedule follows the clock across a change", check_schedule_switch, False),
    ("sleep-wake", "Sleep fades to black and wake restores the dashboard", check_sleep_wake, False),
    ("pairing-closed", "With no code on display, pairing attempts are refused unread", check_pairing_closed, False),
    ("pairing-window", "Show code pairs one more device, once", check_pairing_window, False),
    ("pairing-lockout", "Five wrong codes lock pairing; an owner's new code clears it", check_pairing_lockout, False),
    ("pairing-widget", "The Pairing code widget opens pairing while it is on the glass", check_pairing_widget, False),
    ("notes", "A note posted from the controls appears on the glass", check_notes, False),
    ("offline-fallback", "An unreachable web page falls back to the offline clock", check_offline_fallback, False),
    ("control-page", "The control page and everything it loads are served", check_control_page, False),
    ("health", "The health report describes this device and shows no faults", check_health, False),
    ("returns-to-front", "A screen that covers the dashboard does not stay in front", check_returns_to_front, False),
    ("wakes-display", "A display that Android put to sleep is woken again", check_wakes_display, False),
    ("cold-start", "Starting Home never lights the whole screen", check_cold_start, False),
    ("restart", "A stopped process is recorded and Home comes back paired", check_restart, False),
    ("quick-restart", "A process stopped while starting is counted, not reported as the previous run", check_quick_restart, False),
    ("reboot", "After a reboot the dashboard appears by itself with its settings", check_reboot, True),
    ("script-errors", "The dashboard logged no script errors during the run", check_script_errors, False),
]


# ---------------------------------------------------------------------------
# Upgrade rehearsal: an earlier build with an owner's settings, then this one.
# ---------------------------------------------------------------------------

UPGRADE_CLIENT = "Upgrade rehearsal"
UPGRADE_NOTE = "Kept across the update"
UPGRADE_DATE_OPACITY = 73
UPGRADE_WAKE_BRIGHTNESS = 140


def installed_version(ctx: Context) -> tuple[str, int]:
    dump = ctx.adb.shell("dumpsys", "package", PACKAGE)
    name = re.search(r"versionName=(\S+)", dump)
    code = re.search(r"versionCode=(\d+)", dump)
    require(name and code, "The package manager does not list Mirror Home")
    return name.group(1), int(code.group(1))


def upgrade_earlier_build(ctx: Context) -> None:
    bootstrap = wait_for(
        "the earlier build's API",
        lambda: ctx.api.call("GET", "/api/v1/bootstrap", token=None).body,
        timeout=60,
    )
    require(bootstrap.get("apiVersion") == 1, f"Unexpected API version: {bootstrap}")
    require(bootstrap.get("paired") is False, "The earlier build did not start unpaired")
    ctx.kept["version"] = installed_version(ctx)
    ctx.note("from", "{} (code {})".format(*ctx.kept["version"]))


def upgrade_owner_settings(ctx: Context) -> None:
    def pairing_code():
        shown = re.search(r'text="(\d{3})\s?(\d{3})"', ctx.native_text())
        if shown:
            return shown.group(1) + shown.group(2)
        # Builds before 2.2.0 showed no code on this screen without a network;
        # over USB their controls read it from the loopback runtime instead.
        runtime = ctx.api.call("GET", "/api/v1/dashboard/runtime", token=None)
        return runtime.body.get("pairingCode") if runtime.status == 200 else None

    code = wait_for("the earlier build's pairing code", pairing_code, timeout=40, interval=2)
    # The earlier build may have no health report; the emulator keeps host time.
    now = int(time.time() * 1000)
    offset, _ = zone_timeline(ctx.zone_table, "America/Los_Angeles", now)
    paired = ctx.api.expect(
        "POST",
        "/api/v1/pair",
        {
            "code": code,
            "name": UPGRADE_CLIENT,
            "timeZone": "America/Los_Angeles",
            "utcOffsetMinutes": offset,
        },
        token=None,
    )
    ctx.api.token = paired["token"]
    # What earlier controls saved: a zone name and the offset then in force.
    ctx.api.expect("PUT", "/api/v1/preferences", {
        "timeZone": "America/Los_Angeles", "utcOffsetMinutes": offset, "clock24Hour": True,
    })
    wake, sleep = awake_window(now, offset)
    ctx.set_automation(
        enabled=True, wakeTime=wake, sleepTime=sleep, wakeBrightness=UPGRADE_WAKE_BRIGHTNESS
    )
    ctx.api.expect("POST", "/api/v1/notes", {"text": UPGRADE_NOTE}, status=201)
    layout = ctx.api.expect("GET", "/api/v1/dashboard/layout")
    for widget in layout["widgets"]:
        if widget["id"] == "date":
            widget["opacity"] = UPGRADE_DATE_OPACITY
        if widget["id"] == "name":
            widget["visible"] = True
    ctx.api.expect("PUT", "/api/v1/dashboard/layout", layout)
    ctx.kept.update(wake=wake, sleep=sleep)


def upgrade_install(ctx: Context) -> None:
    old_name, old_code = ctx.kept["version"]
    try:
        installed = ctx.adb.run("install", "-r", "-g", str(ctx.apk), timeout=300)
    except CheckFailed as failure:
        if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in str(failure) or "signatures do not match" in str(failure):
            raise CheckFailed(
                "The build under test is signed with a different key than the earlier "
                "build, so Android will not install it over it"
            ) from None
        raise
    require("Success" in installed, f"The update did not install: {installed.strip()}")
    # Nothing here starts the updated Home: on a Mirror nobody would.
    new_name, new_code = installed_version(ctx)
    require(
        new_code > old_code,
        f"Version code {new_code} does not exceed {old_code}; the OTA supervisor would refuse it",
    )
    health = wait_for(
        "the updated Home to answer",
        lambda: (lambda reply: reply.body if reply.status == 200 else None)(
            ctx.api.call("GET", "/api/v1/health")
        ),
        timeout=90,
    )
    require(health["appVersion"] == new_name, f"Home reports {health['appVersion']}, not {new_name}")
    previous = health["process"]["previousRun"]
    # A build from before the health report leaves no previous run behind.
    require(
        previous is None or previous["end"] == "update",
        f"The run before the update is recorded as {previous}",
    )
    require(health["crashes"]["count"] == 0, f"The updated Home crashed: {health['crashes']}")
    ctx.note("to", f"{new_name} (code {new_code})")


def upgrade_kept_pairing(ctx: Context) -> None:
    require(
        ctx.api.expect("GET", "/api/v1/bootstrap", token=None)["paired"],
        "The update lost the pairing",
    )
    names = [client["name"] for client in ctx.api.expect("GET", "/api/v1/clients")["clients"]]
    require(names == [UPGRADE_CLIENT], f"Paired clients after the update: {names}")


def upgrade_kept_settings(ctx: Context) -> None:
    offset, upcoming = zone_timeline(ctx.zone_table, "America/Los_Angeles", ctx.device_now())
    expected_change = upcoming[0] if upcoming else None
    preferences = ctx.api.expect("GET", "/api/v1/preferences")
    require(
        preferences["timeZone"] == "America/Los_Angeles" and preferences["clock24Hour"] is True,
        f"The clock settings changed: {preferences}",
    )
    # A zone saved by an earlier build must follow the bundled table at once,
    # without anyone opening the controls.
    require(
        preferences["clockSource"] == "bundled",
        f"The saved zone is followed as {preferences['clockSource']}, not from the bundled table",
    )
    require(preferences["utcOffsetMinutes"] == offset, f"The offset is {preferences['utcOffsetMinutes']}")
    announced = ctx.status()["nextUtcOffsetChange"]
    require(
        announced == expected_change,
        f"The next clock change is {announced}, the bundled table says {expected_change}",
    )
    automation = ctx.api.expect("GET", "/api/v1/automation")
    require(
        (automation["enabled"], automation["wakeTime"], automation["sleepTime"], automation["wakeBrightness"])
        == (True, ctx.kept["wake"], ctx.kept["sleep"], UPGRADE_WAKE_BRIGHTNESS),
        f"The schedule changed: {automation}",
    )
    require(automation["sleeping"] is False, f"The display sleeps inside its wake hours: {automation}")
    notes = [note["text"] for note in ctx.api.expect("GET", "/api/v1/notes")["notes"]]
    require(notes == [UPGRADE_NOTE], f"Notes after the update: {notes}")
    widgets = {
        widget["id"]: widget
        for widget in ctx.api.expect("GET", "/api/v1/dashboard/layout")["widgets"]
    }
    require(
        widgets["date"]["opacity"] == UPGRADE_DATE_OPACITY and widgets["name"]["visible"] is True,
        "The layout changes were lost",
    )
    ctx.note("clockSource", preferences["clockSource"])
    ctx.note("nextClockChange", expected_change)


def upgrade_dashboard(ctx: Context) -> None:
    ctx.wait_dashboard()
    ctx.lit_peak()
    shot = ctx.screenshot("after-update")
    dark = 1.0 - screen_capture.lit_fraction(shot, 24)
    require(
        dark >= 1.0 - MAX_DASHBOARD_LIT,
        f"Only {dark:.2%} of the dashboard is black after the update",
    )
    health = ctx.health()
    require(health["activity"]["showing"], f"The dashboard is not in front: {health['activity']}")
    require(
        health["activity"].get("selectedHome", True),
        "The update cost Mirror Home its place as the HOME app",
    )
    ctx.note("front", health["activity"].get("front"))
    errors = health["dashboard"]["consoleErrors"]
    require(errors == 0, f"The dashboard logged {errors} script errors: {health['dashboard']['recentConsoleErrors']}")
    require(health["api"]["unhandledErrors"] == 0, f"Unhandled API errors: {health['api']}")
    require(health["crashes"]["count"] == 0, f"Crashes recorded: {health['crashes']}")


UPGRADE_CHECKS: list[tuple[str, str, Callable[[Context], None], bool]] = [
    ("earlier-build", "The earlier build installs on empty storage and answers", upgrade_earlier_build, False),
    ("owner-settings", "It is paired and given a clock, a schedule, a note and a layout", upgrade_owner_settings, False),
    ("update", "The build under test installs over it and Home comes back", upgrade_install, False),
    ("kept-pairing", "The paired browser's credential still works", upgrade_kept_pairing, False),
    ("kept-settings", "Clock, schedule, note and layout survive, and the clock follows the bundled table", upgrade_kept_settings, False),
    ("dashboard-after", "The dashboard is back on true black with no errors", upgrade_dashboard, False),
    ("pairing-after", "With no code on display, the updated Home refuses pairing attempts", check_pairing_closed, False),
    ("returns-to-front", "A screen that covers the updated dashboard does not stay in front", check_returns_to_front, False),
]


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def run_checks(
    checks: list[tuple[str, str, Callable, bool]],
    context,
    *,
    quick: bool = False,
    only: set[str] | None = None,
) -> list[Result]:
    results = []
    for name, title, function, slow in checks:
        result = Result(name, title)
        if (quick and slow) or (only and name not in only):
            result.status = "skip"
            result.message = "not selected"
            results.append(result)
            continue
        context.details = {}
        started = time.monotonic()
        try:
            prepare = getattr(context, "before_check", None)
            if prepare is not None:
                prepare()
            function(context)
        except CheckSkipped as skipped:
            result.status = "skip"
            result.message = str(skipped)
        except (CheckFailed, AssertionError) as failure:
            result.status = "fail"
            result.message = str(failure)
        except Exception as error:  # A check must never take the run down with it.
            result.status = "fail"
            result.message = f"{type(error).__name__}: {error}"
            result.details["traceback"] = traceback.format_exc(limit=6)
        result.seconds = round(time.monotonic() - started, 1)
        result.details.update(context.details)
        results.append(result)
        print_result(result)
    return results


def print_result(result: Result) -> None:
    label = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[result.status]
    print(f"{label}  {result.name:<17} {result.title} ({result.seconds} s)", flush=True)
    if result.message and result.status != "pass":
        print(f"      {result.message}", flush=True)


def write_report(output: pathlib.Path, target: str, results: list[Result], extra: dict) -> None:
    report = {
        "target": target,
        "finishedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "passed": sum(result.status == "pass" for result in results),
        "failed": sum(result.status == "fail" for result in results),
        "skipped": sum(result.status == "skip" for result in results),
        **extra,
        "checks": [
            {
                "name": result.name,
                "title": result.title,
                "status": result.status,
                "seconds": result.seconds,
                "message": result.message,
                "details": result.details,
            }
            for result in results
        ],
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def summarize(results: list[Result], output: pathlib.Path) -> int:
    failed = [result for result in results if result.status == "fail"]
    passed = sum(result.status == "pass" for result in results)
    skipped = sum(result.status == "skip" for result in results)
    print(f"\n{passed} passed, {len(failed)} failed, {skipped} skipped. Evidence: {output}")
    return 1 if failed else 0


def require_emulator(adb: Adb) -> None:
    """Never install a debug build on, or reconfigure, a real device."""
    fingerprint = adb.property("ro.build.fingerprint")
    if (
        not android_emulator.is_emulator_serial(adb.serial)
        or adb.property("ro.kernel.qemu") != "1"
        or MIRROR_FINGERPRINT_MARKER in fingerprint
    ):
        raise CheckFailed(
            f"{adb.serial} ({fingerprint or 'unknown device'}) is not an Android emulator; "
            "the emulator suite never runs on a physical device"
        )


def prepare_device(adb: Adb) -> None:
    # The first immersive window otherwise gets a tutorial bubble that dims it.
    adb.shell("settings", "put", "secure", "immersive_mode_confirmations", "confirmed")
    # A Mirror tells Android it has no mains power, so "stay awake while
    # charging" never applies to it: its display stays on only while a window
    # asks for that, and from the factory turns off ten minutes after.
    adb.shell("settings", "put", "global", "stay_on_while_plugged_in", "0")
    adb.shell("settings", "put", "system", "screen_off_timeout", "600000")
    # A freshly booted emulator shows a lock screen, which a Mirror has not.
    adb.shell("input", "keyevent", KEY_WAKEUP)
    adb.shell("input", "keyevent", KEY_MENU)


def select_home(adb: Adb) -> None:
    """Make Mirror Home the preferred HOME app, as its owner does: in Android's chooser.

    This also brings the dashboard to the front as HOME, which is how Android
    starts it on a Mirror after every boot.
    """
    for _ in range(4):
        adb.shell(*HOME_REQUEST)
        time.sleep(2)
        focus = focused_window(adb)
        if focus.startswith(f"{PACKAGE}/"):
            # A Mirror's factory launcher is installed but not running.
            adb.shell("am", "force-stop", STOCK_LAUNCHER, check=False)
            return
        if HOME_CHOOSER in focus:
            row = node_center(window_text(adb), "text", HOME_LABEL)
            if row:
                tap(adb, row)
                time.sleep(1)
            always = node_center(window_text(adb), "resource-id", "android:id/button_always")
            if always:
                tap(adb, always)
                time.sleep(2)
    raise CheckFailed(
        "Mirror Home could not be made the emulator's HOME app; "
        f"{focused_window(adb) or 'nothing'} is in front"
    )


def save_logs(adb: Adb, output: pathlib.Path) -> bool:
    """Keep Android's log and crash records; False if the emulator no longer answers.

    A frozen emulator keeps its ADB connection and answers nothing, so each
    request would otherwise wait out its time limit, and a run that ended
    that way would leave no report behind.
    """
    if not adb.answers():
        print(
            f"\n{adb.serial} has stopped answering, so its logs could not be read. Checks that "
            "failed once it had stopped say nothing about Mirror Home; see "
            '"When the emulator stops answering" in docs/validation.md.',
            flush=True,
        )
        return False
    (output / "logcat.txt").write_text(
        adb.run("logcat", "-d", "-v", "time", check=False), encoding="utf-8"
    )
    (output / "crashes.txt").write_text(
        "\n".join(
            adb.shell("dumpsys", "dropbox", "--print", record, check=False)
            for record in CRASH_RECORDS
        ),
        encoding="utf-8",
    )
    return True


def build_debug_apk() -> None:
    wrapper = REPO / ("gradlew.bat" if os.name == "nt" else "gradlew")
    print("Building the Mirror Home debug APK", flush=True)
    subprocess.run(
        [str(wrapper), "-p", str(REPO), ":android:mirror-home:assembleDebug", "--no-daemon", "-q"],
        check=True,
    )


def default_output(target: str) -> pathlib.Path:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPO / "build" / "validation" / f"{target}-{stamp}"


def selected_checks(only: str | None, checks: list[tuple[str, str, Callable, bool]]) -> set[str] | None:
    """The checks ``--only`` names, plus the ones the rest cannot run without."""
    if not only:
        return None
    names = {name.strip() for name in only.split(",") if name.strip()}
    known = [name for name, _, _, _ in checks]
    unknown = sorted(names.difference(known))
    if unknown:
        raise CheckFailed(f"Unknown check {', '.join(unknown)}; choose from {', '.join(known)}")
    return names.union(PREREQUISITE_CHECKS)


def run_emulator(options: argparse.Namespace) -> int:
    only = selected_checks(options.only, EMULATOR_CHECKS)
    earlier = options.upgrade_from
    if earlier is not None:
        if options.only or options.quick:
            raise CheckFailed("--upgrade-from runs its own checks; leave out --only and --quick")
        if not earlier.is_file():
            raise CheckFailed(f"APK not found: {earlier}")
    target = "upgrade" if earlier is not None else "emulator"
    output = options.output or default_output(target)
    output.mkdir(parents=True, exist_ok=True)
    apk = options.apk or DEBUG_APK
    if not options.apk and not options.skip_build:
        build_debug_apk()
    if not apk.is_file():
        raise CheckFailed(f"APK not found: {apk}")

    emulator = None
    emulator_version = None
    serial = options.serial
    if serial is None:
        emulator = android_emulator.Emulator(
            density=options.density,
            window=options.window,
            log_path=output / "emulator.log",
        )
        emulator.start()
        serial = emulator.serial
        release = android_emulator.installed_release(emulator.sdk)
        if release:
            emulator_version = f"{release[0]} (build {release[1]})" if release[1] else release[0]
        print(
            f"Booting Android 6 as {serial} on Android Emulator {emulator_version or 'of an unknown release'}",
            flush=True,
        )
        advice = android_emulator.release_advice(release[0] if release else None)
        if advice:
            print(f"      {advice}", flush=True)
    adb = Adb(android_emulator.adb_path(android_emulator.sdk_root()), serial)
    context = None
    try:
        if emulator is not None:
            emulator.wait_for_boot()
        require_emulator(adb)
        fingerprint = adb.property("ro.build.fingerprint")
        prepare_device(adb)
        adb.run("uninstall", PACKAGE, check=False)
        installed = adb.run("install", "-r", "-g", str(earlier or apk), timeout=300)
        require("Success" in installed, f"Installation failed: {installed.strip()}")
        select_home(adb)
        port = adb.forward(f"tcp:{DEVICE_PORT}")
        context = Context(adb, Api("127.0.0.1", port), output)
        context.forwards.append(port)
        context.apk = apk
        context.emulator = emulator
        if earlier is not None:
            results = run_checks(UPGRADE_CHECKS, context)
        else:
            results = run_checks(EMULATOR_CHECKS, context, quick=options.quick, only=only)
        answering = save_logs(adb, output)
        write_report(output, target, results, {
            "serial": serial,
            "fingerprint": fingerprint,
            "emulator": emulator_version,
            "emulatorStoppedAnswering": not answering,
            "apk": str(apk),
            "systemDialogsClosed": context.dismissed,
            **({"upgradeFrom": str(earlier)} if earlier is not None else {}),
        })
        return summarize(results, output)
    finally:
        if context is not None:
            for port in context.forwards:
                adb.remove_forward(port)
        if emulator is not None and not options.keep_running:
            emulator.stop()
        elif emulator is not None:
            print(f"Left {serial} running; stop it with: adb -s {serial} emu kill")


# ---------------------------------------------------------------------------
# Live Mirror: read-only
# ---------------------------------------------------------------------------


class MirrorContext:
    def __init__(self, api: Api, ota_config: pathlib.Path | None = None):
        self.api = api
        self.ota_config = ota_config
        self.details: dict = {}
        self.findings: list[str] = []
        self.health: dict | None = None
        self.status: dict | None = None

    def note(self, key: str, value: object) -> None:
        self.details[key] = value


def mirror_reachable(ctx: MirrorContext) -> None:
    bootstrap = ctx.api.expect("GET", "/api/v1/bootstrap", token=None)
    require(bootstrap.get("apiVersion") == 1, f"Unexpected API version: {bootstrap}")
    ctx.note("appVersion", bootstrap.get("appVersion"))
    ctx.note("displayName", bootstrap.get("displayName"))
    ctx.note("pairingOpen", bootstrap.get("pairingOpen"))


def mirror_status(ctx: MirrorContext) -> None:
    status = ctx.api.expect("GET", "/api/v1/status")
    ctx.status = status
    video = status.get("ambientVideo", {})
    automation = status.get("automation", {})
    ctx.note("uptimeDays", round(status.get("deviceUptimeSeconds", 0) / 86400, 2))
    ctx.note("wifi", status.get("wifi", {}).get("ssid") or status.get("wifi"))
    ctx.note("sleeping", automation.get("sleeping"))
    ctx.note("sleepReason", automation.get("sleepReason"))
    ctx.note("clock", {
        "timeZone": status.get("timeZone"),
        "utcOffsetMinutes": status.get("utcOffsetMinutes"),
        "nextUtcOffsetChange": status.get("nextUtcOffsetChange", "not reported"),
    })
    ctx.note("video", {
        key: video.get(key)
        for key in ("state", "decoderName", "frameRate", "renderedFrames", "droppedFrames",
                    "droppedFramePercent", "maxConsecutiveDroppedFrames", "loopCount", "error",
                    "retries")
    })
    require(status.get("wifi", {}).get("connected"), "Wi-Fi is not connected")
    require(not video.get("error"), f"Background video reports an error: {video.get('error')}")
    dropped = video.get("droppedFramePercent") or 0
    require(dropped < 1.0, f"Background video dropped {dropped:.2f}% of its frames")
    motion = automation.get("motion", {})
    if automation.get("motionEnabled"):
        require(motion.get("monitoring"), f"Presence sensing is on but not monitoring: {motion.get('state')}")
    weather = status.get("weather", {})
    require(not weather.get("stale"), "The weather shown is stale")


def mirror_health(ctx: MirrorContext) -> None:
    reply = ctx.api.call("GET", "/api/v1/health")
    if reply.status == 404:
        raise CheckSkipped("this Mirror Home predates the health report")
    require(reply.status == 200, f"Health answered {reply.status}: {describe(reply.body)}")
    health = reply.body
    if not health["activity"]["showing"] and "recovery" in health["activity"]:
        # Mirror Home brings a covered dashboard back within about fifteen seconds.
        try:
            health = wait_for(
                "the dashboard to return to the front",
                lambda: (lambda seen: seen if seen["activity"]["showing"] else None)(
                    ctx.api.expect("GET", "/api/v1/health")
                ),
                timeout=DASHBOARD_RETURN_SECONDS,
                interval=3,
            )
            ctx.note("dashboardReturned", True)
        except CheckFailed:
            health = ctx.api.expect("GET", "/api/v1/health")
    ctx.health = health
    memory = health["memory"]
    activity = health["activity"]
    supervisor = health["otaSupervisor"]
    if supervisor.get("installed") and not supervisor.get("listening"):
        try:
            supervisor = wait_for(
                "the OTA supervisor to accept connections again",
                lambda: (lambda seen: seen if seen.get("listening") else None)(
                    ctx.api.expect("GET", "/api/v1/health")["otaSupervisor"]
                ),
                timeout=SUPERVISOR_RESTART_SECONDS,
                interval=3,
            )
            ctx.note("otaSupervisorRestarting", True)
        except CheckFailed:
            pass
    previous = health["process"]["previousRun"]
    early_stops = health["process"].get("earlyStops", 0)
    ctx.note("process", {
        "runId": health["process"]["runId"],
        "uptimeHours": round(health["process"]["uptimeSeconds"] / 3600, 1),
        "previousEnd": previous["end"] if previous else None,
        "earlyStops": early_stops,
    })
    ctx.note("memory", {key: memory.get(key) for key in ("pssKb", "javaHeapUsedKb", "nativeHeapKb", "openFiles", "threads", "systemAvailableKb", "trimEvents")})
    ctx.note("display", health["device"]["display"])
    ctx.note("power", health["device"].get("power"))
    ctx.note("front", activity.get("front"))
    ctx.note("recovery", activity.get("recovery"))
    ctx.note("webView", health["device"]["webView"])
    ctx.note("clockSource", health["clock"]["source"])
    ctx.note("nextClockChange", health["clock"]["nextChange"])
    ctx.note("pairing", health["pairing"])
    ctx.note("otaSupervisor", supervisor)
    problems = []
    if early_stops > MAX_EARLY_STOPS:
        problems.append(f"Home was stopped {early_stops} times while starting before this run")
    if health["crashes"]["count"]:
        last = health["crashes"]["last"] or {}
        problems.append(f"{health['crashes']['count']} crash(es); last {last.get('exception')}: {last.get('message')}")
    if not activity["showing"]:
        covering = f"; in front: {activity['front']}" if activity.get("front") else ""
        problems.append(
            f"the dashboard is not in front (paused {activity['pausedForSeconds']} s, "
            f"unfocused {activity['unfocusedForSeconds']} s){covering}"
        )
    if not activity.get("selectedHome", True):
        problems.append("Mirror Home is not Android's HOME app, so nothing starts it or brings it back")
    if not (health["device"].get("power") or {}).get("interactive", True):
        problems.append("Android has put the display to sleep")
    if health["dashboard"]["consoleErrors"]:
        problems.append(f"{health['dashboard']['consoleErrors']} dashboard script error(s)")
    if health["api"]["unhandledErrors"]:
        problems.append(f"{health['api']['unhandledErrors']} unhandled API error(s)")
    if memory.get("systemLow"):
        problems.append("Android reports low memory")
    if health["storage"].get("dataFreeBytes", 1 << 40) < 256 * 1024 * 1024:
        problems.append("less than 256 MiB of storage is free")
    if supervisor.get("installed") and not supervisor.get("listening"):
        problems.append(
            "the OTA supervisor is installed but has not accepted connections for "
            f"{SUPERVISOR_RESTART_SECONDS} s"
        )
    if health["clock"]["source"] == "fixed":
        problems.append("the clock keeps a fixed offset and will not follow daylight saving")
    if health["pairing"]["lockedForSeconds"]:
        problems.append("pairing is locked after wrong codes")
    require(not problems, "; ".join(problems))


def mirror_updater(ctx: MirrorContext) -> None:
    if ctx.ota_config is None or not ctx.ota_config.is_file():
        raise CheckSkipped("this computer holds no OTA credential for the Mirror")
    import otactl

    try:
        client, _ = otactl.load_config(ctx.ota_config, None)
        status = client.status()
    except otactl.OtaClientError as error:
        raise CheckFailed(f"The OTA supervisor did not answer a signed request: {error}") from error
    ctx.note("updaterVersion", status.get("updaterVersion"))
    ctx.note("state", status.get("state"))
    ctx.note("home", f"{status.get('homeVersionName')} (code {status.get('homeVersionCode')})")
    ctx.note("knownGood", f"{status.get('knownGoodVersionName')} (code {status.get('knownGoodVersionCode')})")
    problems = []
    if not status.get("deviceOwner"):
        problems.append("the supervisor is not the device owner, so it cannot install updates")
    if status.get("deviceFingerprint") != status.get("supportedFingerprint"):
        problems.append("the supervisor does not support this firmware")
    if status.get("state") == "recovery_required":
        problems.append(f"the last update needs recovery: {status.get('message')}")
    if ctx.status is not None and status.get("homeVersionName") != ctx.status.get("appVersion"):
        problems.append(
            f"the supervisor sees Home {status.get('homeVersionName')}, "
            f"Home reports {ctx.status.get('appVersion')}"
        )
    health = status.get("health")
    if health:
        ctx.note("supervisorRun", {
            "runId": health["process"]["runId"],
            "uptimeHours": round(health["process"]["uptimeSeconds"] / 3600, 1),
            "previousEnd": (health["process"]["previousRun"] or {}).get("end"),
        })
        if health["crashes"]["count"]:
            last = health["crashes"]["last"] or {}
            problems.append(
                f"the supervisor crashed {health['crashes']['count']} time(s); "
                f"last {last.get('exception')}: {last.get('message')}"
            )
    require(not problems, "; ".join(problems))


MIRROR_CHECKS = [
    ("reachable", "The Mirror answers on the network", mirror_reachable, False),
    ("status", "Wi-Fi, background video, presence sensing and weather are working", mirror_status, False),
    ("health", "No crashes, covered display, script errors or resource pressure", mirror_health, False),
    ("updater", "The OTA supervisor answers signed requests and can install updates", mirror_updater, False),
]


# ---------------------------------------------------------------------------
# Live Mirror: a short exercise that changes things and puts them back
# ---------------------------------------------------------------------------

EXERCISE_CLIENT = "Validation (temporary)"
EXERCISE_NOTE = "Validation note"
AUTOMATION_SETTINGS = (
    "enabled", "wakeTime", "sleepTime", "wakeBrightness", "ambientEnabled", "ambientMinimum",
    "ambientMaximum", "motionEnabled", "motionTimeoutSeconds", "motionSensitivity",
)
# Enough frames to show the video is really playing again after a wake.
VIDEO_RESUME_FRAMES = 60


def require_health_report(ctx: MirrorContext) -> None:
    """The exercise relies on what Mirror Home 2.2.0 added."""
    if ctx.health is None:
        raise CheckSkipped("this Mirror Home is too old to be exercised; it has no health report")


def exercise_pairing(ctx: MirrorContext) -> None:
    require_health_report(ctx)
    closed = ctx.api.expect("GET", "/api/v1/bootstrap", token=None).get("pairingOpen") is False
    if closed:
        refused = ctx.api.call("POST", "/api/v1/pair", {"code": "000000", "name": "Guess"}, token=None)
        require(
            refused.status == 403 and refused.body.get("reason") == "closed",
            f"A guess was examined while no code was on display: {refused.status} {describe(refused.body)}",
        )
    else:
        # A wrong code would count towards a lockout while one is on display.
        ctx.note("closedPairing", "not probed: a pairing code is on display")
    before = sorted(client["id"] for client in ctx.api.expect("GET", "/api/v1/clients")["clients"])
    window = ctx.api.expect("POST", "/api/v1/pair/window", {})
    paired = ctx.api.expect(
        "POST", "/api/v1/pair", {"code": window["code"], "name": EXERCISE_CLIENT}, token=None
    )
    try:
        require(
            ctx.api.call("GET", "/api/v1/status", token=paired["token"]).status == 200,
            "The new device's credential does not work",
        )
        if closed:
            again = ctx.api.call(
                "POST", "/api/v1/pair", {"code": window["code"], "name": EXERCISE_CLIENT}, token=None
            )
            require(again.status == 403, f"A used pairing code answered {again.status}")
            require(
                ctx.api.expect("GET", "/api/v1/bootstrap", token=None)["pairingOpen"] is False,
                "Pairing stayed open after the code was used",
            )
    finally:
        ctx.api.expect("POST", "/api/v1/clients/revoke", {"id": paired["clientId"]})
    require(
        ctx.api.call("GET", "/api/v1/status", token=paired["token"]).status == 401,
        "A revoked credential still works",
    )
    after = sorted(client["id"] for client in ctx.api.expect("GET", "/api/v1/clients")["clients"])
    require(after == before, "The list of paired devices is not what it was")
    ctx.note("pairedDevices", len(after))


def exercise_notes(ctx: MirrorContext) -> None:
    require_health_report(ctx)
    notes = ctx.api.expect("GET", "/api/v1/notes")
    if len(notes["notes"]) >= notes.get("maxNotes", 50):
        raise CheckSkipped("the Mirror already holds as many notes as it can")
    version = ctx.api.expect("GET", "/api/v1/status")["notesVersion"]
    text = f"{EXERCISE_NOTE} {int(time.time())}"
    note = ctx.api.expect("POST", "/api/v1/notes", {"text": text}, status=201)["note"]
    try:
        listed = ctx.api.expect("GET", "/api/v1/notes")["notes"]
        require(
            any(entry["id"] == note["id"] and entry["text"] == text for entry in listed),
            "The posted note is not listed",
        )
        require(
            ctx.api.expect("GET", "/api/v1/status")["notesVersion"] != version,
            "Posting a note did not change notesVersion, so the glass would not show it",
        )
    finally:
        ctx.api.expect("DELETE", f"/api/v1/notes/{note['id']}")
    remaining = ctx.api.expect("GET", "/api/v1/notes")["notes"]
    require(all(entry["id"] != note["id"] for entry in remaining), "The deleted note is still listed")
    require(len(remaining) == len(notes["notes"]), "The number of notes is not what it was")


def exercise_display(ctx: MirrorContext) -> None:
    require_health_report(ctx)

    def status() -> dict:
        return ctx.api.expect("GET", "/api/v1/status")

    start = status()
    if start["media"].get("state") not in (None, "idle"):
        raise CheckSkipped("something is playing on the Mirror, and sleep would stop it")
    automation = ctx.api.expect("GET", "/api/v1/automation")
    saved = {key: automation[key] for key in AUTOMATION_SETTINGS}
    video = bool(start["ambientVideo"].get("enabled"))
    brightness_before = start.get("brightness") if not automation["sleeping"] else None
    try:
        slept = ctx.api.expect("POST", "/api/v1/automation/sleep", {})
        require(slept["sleeping"] is True, "Sleep was not reported at once")
        if video:
            # The video plays on until the fade reaches black, then stops.
            wait_for(
                "the background video to stop once the display is dark",
                lambda: not status()["ambientVideo"].get("playing"),
                timeout=FADE_SETTLE_SECONDS,
            )
        else:
            time.sleep(4)
        require(status()["brightness"] == 0, "A sleeping display reports a brightness")

        woke = ctx.api.expect("POST", "/api/v1/automation/wake", {})
        require(woke["sleeping"] is False, "Wake was not reported at once")
        awake = wait_for(
            "the Mirror's own services to report a brightness after waking",
            lambda: (lambda seen: seen
                     if isinstance(seen.get("brightness"), int) and seen["brightness"] > 1 else None)(status()),
            timeout=FADE_SETTLE_SECONDS,
        )
        level = awake["brightness"]
        ctx.note("wakeBrightness", level)
        # Let the two-second fade back in finish before touching the backlight.
        time.sleep(3)
        other = level - 40 if level > 60 else level + 40
        changed = ctx.api.call("POST", "/api/v1/control/brightness", {"value": other})
        try:
            require(
                changed.status == 200 and changed.body.get("changed"),
                f"The brightness could not be set: {changed.status} {describe(changed.body)}",
            )
            wait_for(
                f"brightness {other} to be read back",
                lambda: status()["brightness"] == other,
                timeout=6,
            )
        finally:
            ctx.api.call("POST", "/api/v1/control/brightness", {"value": level})
        wait_for(f"brightness {level} to be restored", lambda: status()["brightness"] == level, timeout=6)

        if video:
            first = status()["ambientVideo"].get("renderedFrames") or 0

            def resumed():
                seen = status()["ambientVideo"]
                frames = seen.get("renderedFrames") or 0
                # The count starts again when playback does.
                playing = frames >= first + VIDEO_RESUME_FRAMES or VIDEO_RESUME_FRAMES <= frames < first
                return seen if seen.get("playing") and playing else None

            playing = wait_for("the background video to play again", resumed, timeout=30)
            require(not playing.get("error"), f"The background video reports {playing.get('error')}")
            dropped = playing.get("droppedFramePercent") or 0
            require(dropped < 1.0, f"The background video dropped {dropped:.2f}% of its frames")
            ctx.note("videoDecoder", playing.get("decoderName"))
    finally:
        # Saving the schedule ends the manual override that sleep and wake leave.
        ctx.api.expect("PUT", "/api/v1/automation", saved)
        if automation.get("manualOverride"):
            ctx.api.call(
                "POST", f"/api/v1/automation/{'sleep' if automation['sleeping'] else 'wake'}", {}
            )
        elif brightness_before and brightness_before > 0:
            ctx.api.call("POST", "/api/v1/control/brightness", {"value": brightness_before})
    restored = ctx.api.expect("GET", "/api/v1/automation")
    require(
        {key: restored[key] for key in AUTOMATION_SETTINGS} == saved,
        "The sleep schedule is not what it was",
    )


def exercise_offline_fallback(ctx: MirrorContext) -> None:
    require_health_report(ctx)

    def dashboard() -> dict:
        return ctx.api.expect("GET", "/api/v1/health")["dashboard"]

    original = ctx.api.expect("GET", "/api/v1/dashboard").get("url") or ""
    ctx.api.expect("PUT", "/api/v1/dashboard", {"url": UNREACHABLE_PAGE})
    try:
        fallen = wait_for(
            "the offline clock to replace a page that cannot load",
            lambda: (lambda seen: seen
                     if seen["phase"] == "page-finished" and seen["url"].endswith(OFFLINE_PAGE) else None)(
                dashboard()
            ),
            timeout=60,
        )
        require(
            fallen["lastFailurePhase"] in ("load-error", "http-error"),
            f"The fallback loaded without a recorded failure: {fallen}",
        )
    finally:
        ctx.api.expect("PUT", "/api/v1/dashboard", {"url": original})
    wait_for(
        "the dashboard to come back",
        lambda: (lambda seen: seen
                 if seen["phase"] == "page-finished" and not seen["url"].endswith(OFFLINE_PAGE)
                 and (original or (seen["pageComplete"] and seen["rendererPresent"])) else None)(dashboard()),
        timeout=90,
    )
    require(
        (ctx.api.expect("GET", "/api/v1/dashboard").get("url") or "") == original,
        "The dashboard address is not what it was",
    )


def exercise_weather(ctx: MirrorContext) -> None:
    require_health_report(ctx)
    weather = ctx.api.expect("GET", "/api/v1/weather")
    if not (weather.get("config") or {}).get("enabled"):
        raise CheckSkipped("weather is turned off")
    before = weather.get("updatedAt") or 0
    asked = ctx.api.call("POST", "/api/v1/weather/refresh", {})
    require(asked.status in (200, 202), f"The refresh was refused: {asked.status} {describe(asked.body)}")

    try:
        refreshed = wait_for(
            "the weather to refresh from the network",
            lambda: (lambda seen: seen if (seen.get("updatedAt") or 0) > before else None)(
                ctx.api.expect("GET", "/api/v1/weather")
            ),
            timeout=45,
            interval=2,
        )
    except CheckFailed:
        seen = ctx.api.expect("GET", "/api/v1/weather")
        raise CheckFailed(
            f"The weather did not refresh: {seen.get('error') or seen.get('state')}"
        ) from None
    require(not refreshed.get("stale"), "The refreshed weather is marked stale")


def exercise_health_after(ctx: MirrorContext) -> None:
    require_health_report(ctx)
    before = ctx.health
    mirror_health(ctx)
    after = ctx.health
    if before is not None and after is not None:
        require(
            after["process"]["runId"] == before["process"]["runId"],
            "Mirror Home restarted during the exercise",
        )
        ctx.note("openFilesChange", (after["memory"].get("openFiles") or 0) - (before["memory"].get("openFiles") or 0))


EXERCISE_CHECKS = [
    ("pairing", "Pairing is closed until a paired device asks for a code, which works once", exercise_pairing, False),
    ("notes", "A note can be posted, read back and deleted", exercise_notes, False),
    ("display", "The display sleeps and wakes, brightness is set and read back, the video resumes", exercise_display, False),
    ("offline-fallback", "A page that cannot load gives way to the offline clock, and the dashboard returns", exercise_offline_fallback, False),
    ("weather", "The weather refreshes from the network", exercise_weather, False),
    ("health-after", "Afterwards Home is still the same process, with no crash or error", exercise_health_after, False),
]


def load_mirror_config(path: pathlib.Path) -> dict:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CheckFailed(f"Unable to read the Mirror credential file {path}: {error}") from error
    if not config.get("host") or not config.get("token"):
        raise CheckFailed(f"{path} needs host and token; pair with tools/background-video.ps1")
    return config


def run_mirror(options: argparse.Namespace) -> int:
    config = load_mirror_config(options.config)
    output = options.output or default_output("mirror")
    output.mkdir(parents=True, exist_ok=True)
    context = MirrorContext(
        Api(config["host"], int(config.get("port", DEVICE_PORT)), config["token"]),
        options.ota_config,
    )
    checks = list(MIRROR_CHECKS)
    if options.exercise:
        print(
            "Exercising the Mirror: its display will sleep and wake, and the offline "
            "clock will replace the dashboard for a few seconds.",
            flush=True,
        )
        checks += EXERCISE_CHECKS
    results = run_checks(checks, context)
    for result in results:
        for key, value in result.details.items():
            if key != "traceback":
                print(f"      {result.name}.{key}: {describe(value, 400)}")
    write_report(output, "mirror", results, {"host": config["host"], "exercised": options.exercise})
    return summarize(results, output)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    targets = parser.add_subparsers(dest="target", required=True)

    emulator = targets.add_parser("emulator", help="run the full suite on an Android 6 emulator")
    emulator.add_argument("--apk", type=pathlib.Path, help="APK to install instead of building a debug one")
    emulator.add_argument("--skip-build", action="store_true", help="use the debug APK already built")
    emulator.add_argument(
        "--upgrade-from",
        type=pathlib.Path,
        help="rehearse an update: install this earlier APK, set it up, then install the build under test over it",
    )
    emulator.add_argument("--serial", help="use this running emulator instead of starting one")
    emulator.add_argument("--keep-running", action="store_true", help="leave the emulator running afterwards")
    emulator.add_argument("--window", action="store_true", help="show the emulator window")
    emulator.add_argument("--quick", action="store_true", help="skip the checks that reboot the emulator")
    emulator.add_argument("--only", help="comma-separated check names to run")
    emulator.add_argument("--density", type=int, default=android_emulator.DEFAULT_DENSITY)
    emulator.add_argument("--output", type=pathlib.Path, help="directory for the report and screenshots")
    emulator.set_defaults(run=run_emulator)

    mirror = targets.add_parser("mirror", help="read-only health check of a paired Mirror")
    mirror.add_argument("--config", type=pathlib.Path, default=DEFAULT_MIRROR_CONFIG, help="JSON file with host, port and token")
    mirror.add_argument("--ota-config", type=pathlib.Path, default=DEFAULT_OTA_CONFIG, help="OTA credential file, if this computer has one")
    mirror.add_argument(
        "--exercise",
        action="store_true",
        help="also use the Mirror briefly: pair and revoke a device, post a note, sleep and wake the display",
    )
    mirror.add_argument("--output", type=pathlib.Path, help="directory for the report")
    mirror.set_defaults(run=run_mirror)

    options = parser.parse_args(arguments)
    try:
        return options.run(options)
    except (CheckFailed, android_emulator.EmulatorError, subprocess.CalledProcessError) as error:
        print(f"validate: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
