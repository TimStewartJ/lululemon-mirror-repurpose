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
import hashlib
import http.client
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback
import wave
import zipfile
from dataclasses import dataclass, field
from typing import Callable

import android_emulator
import fake_companion
import screen_capture
import voice
import webview_devtools


REPO = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "dev.mirror.repurpose"
ACTIVITY = f"{PACKAGE}/.MainActivity"
DEVICE_PORT = 8787
DEBUG_APK = (
    REPO / "android" / "mirror-home" / "build" / "outputs" / "apk" / "debug"
    / "mirror-home-debug.apk"
)
DEBUG_SUPERVISOR_APK = (
    REPO / "android" / "ota-updater" / "build" / "outputs" / "apk" / "debug" / "ota-updater-debug.apk"
)
SUPERVISOR = "dev.mirror.repurpose.updater"
SCAN_GUARD = "/api/v1/wifi/scan-guard"
# How readily the kernel of Android 6 ends a process ("oom_score_adj"): what the display
# needs, and a background service. A supervisor that Mirror Home holds is ranked as the first.
HELD_SCORE = 58
SERVICE_SCORE = 294
# A scan can end as the Mirror joins its network again; more than these with the guard on are not that.
SCANS_DESPITE_GUARD = 2
# Android starts an ended supervisor again within a second; a replaced one is looked for after two.
HOLD_SECONDS = 40
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
# How long the other HOME app's idle process may stay under a dashboard that is
# back in front. Mirror Home asks Android to end it three times within 6 seconds.
OTHER_HOME_ENDS_SECONDS = 20
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
# The recogniser has a process of its own, named after Mirror Home's.
VOICE_PROCESS = f"{PACKAGE}:voice"
# Speech for an emulator, which has no microphone worth the name.
VOICE_CLIPS = REPO / "tools" / "validation-clips"
# What Mirror Home wants in a speech model's archive before it tries the model.
VOICE_MODEL_FILES = ("am/final.mdl", "conf/mfcc.conf", "conf/model.conf", "graph/HCLr.fst", "graph/Gr.fst")
# A Mirror loads the model in four seconds; a shared runner is given longer.
VOICE_LISTENING_SECONDS = 120
# A sentence ends once a second or so of silence has followed it.
VOICE_ACT_SECONDS = 30
# How long a command may follow the Mirror's name (VoiceInterpreter.WINDOW_MS).
VOICE_WINDOW_SECONDS = 6
# What one "brighter" adds to the wake brightness (VoiceManager.BRIGHTNESS_STEP).
VOICE_BRIGHTNESS_STEP = 40
# Where the glass says what it heard: low, in the middle.
VOICE_CAPTION_BOX = (0.2, 0.84, 0.8, 0.98)
# How long it says what it did (MainActivity.VOICE_CAPTION_MS), and fades.
VOICE_CAPTION_SECONDS = 3
# The caption's strokes light at least this much more of that box.
VOICE_CAPTION_LIT = 0.002
RECORD_AUDIO = "android.permission.RECORD_AUDIO"
# Said to the Mirror and no command of its own: for the assistant.
ASSISTANT_CLIP = "mirror-what-is-the-weather.wav"
# The clip's silence before its first and after its last word, in samples.
ASSISTANT_CLIP_SILENCE = (4_800, 6_400)
# How long the suite looks for a line on the glass; the shortest stays 3.5 s.
ASSISTANT_SHOWN_SECONDS = 12
# A picture of a black screen at this size is a third of this.
ASSISTANT_PICTURE_BYTES = 6_000
# What the glass shows at the place its answers were moved to, and where a Mirror's answers start.
PLACE_NOTICE = "Answers appear here"
FIRST_PLACE = {"height": "bottom", "side": "center"}
NO_SPEECH_MODEL = "no speech model on this computer; fetch it with: python tools/voice.py fetch-model"
NO_TEST_SPEECH = "a release build cannot be given test speech"


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
        headers: dict[str, str] | None = None,
    ) -> Reply:
        sent = {"Accept": "application/json", **(headers or {})}
        payload = data
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            sent["Content-Type"] = "application/json"
        if content_type:
            sent["Content-Type"] = content_type
        bearer = self.token if token is True else token
        if bearer:
            sent["Authorization"] = "Bearer " + bearer
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            connection.request(method, path, body=payload, headers=sent)
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
        # What those errors were: a process that ends takes its list along.
        self.console_errors_kept: list = []
        self.zone_table = json.loads(ZONE_TABLE.read_text(encoding="utf-8"))
        self.awake_peak = 0
        self.debuggable: bool | None = None
        self.apk: pathlib.Path | None = None
        # An OTA supervisor signed with the same key as that APK, if there is one.
        self.supervisor_apk: pathlib.Path | None = None
        # The speech model on this computer, if it has one; and its checksum.
        self.voice_model: pathlib.Path | None = None
        self.voice_checksum = ""
        # Whether a check of this run switched voice on, for the checks after restarts.
        self.voice_left_on = False
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
        self.keep_console_errors(self.health())
        self.adb.shell("am", "force-stop", PACKAGE)

    def keep_console_errors(self, health: dict) -> None:
        """Remember what the dashboard logged, before its process ends and forgets it."""
        dashboard = health["dashboard"]
        self.console_errors_before_restarts += dashboard["consoleErrors"]
        self.console_errors_kept.extend(dashboard.get("recentConsoleErrors") or [])

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


BOARD = "/api/v1/board"
BOARD_ITEMS = BOARD + "/items"
BOARD_GUIDE = BOARD + "/guide"
BOARD_SOURCE = "validation"
# The widget's pages turn every ten seconds; allow each a little longer.
BOARD_PAGE_SECONDS = 14
# The dashboard asks for its runtime every five seconds and then for the board.
BOARD_GLASS_POLL_SECONDS = 7
# What the glass lists, read out of the page: one entry per row, in order.
BOARD_ON_GLASS = (
    "JSON.stringify((function () {"
    "var board = document.querySelector('.mr-board');"
    "if (!board) return null;"
    "var rows = board.querySelectorAll('.mr-board-row');"
    "var seen = {rows: [], heading: '', dots: board.querySelectorAll('.mr-board-dot').length,"
    " fading: Boolean(board.querySelector('.mr-fading')),"
    " inner: board.querySelector('.mr-inner').offsetHeight, box: board.clientHeight};"
    "var heading = board.querySelector('.mr-board-heading');"
    "if (heading) seen.heading = heading.textContent;"
    "for (var index = 0; index < rows.length; index++) {"
    "var when = rows[index].querySelector('.mr-board-when');"
    "seen.rows.push({title: rows[index].querySelector('.mr-board-title').textContent,"
    " when: when ? when.textContent : '', state: rows[index].className});"
    "}"
    "return seen;"
    "}()))"
)


def board_refusal(reply: Reply, status: int, field: str | None, what: str) -> None:
    """A refusal has to tell a program what to change and where to read more."""
    require(reply.status == status, f"{what} answered {reply.status}, expected {status}: {describe(reply.body)}")
    body = reply.body if isinstance(reply.body, dict) else {}
    require(body.get("error"), f"{what} was refused without saying why: {describe(reply.body)}")
    require(body.get("guide") == BOARD_GUIDE, f"{what} was refused without pointing at the guide")
    require(body.get("field") == field, f"{what} blamed the field {body.get('field')!r}, expected {field!r}")


def check_board_api(ctx: Context) -> None:
    """The board as a program meets it: with nothing but the Mirror's address."""
    guide = ctx.api.expect("GET", BOARD_GUIDE, token=None)
    require(guide.get("start") and guide.get("item"), f"The board's guide is empty: {describe(guide)}")
    board_refusal(
        ctx.api.call("POST", BOARD_ITEMS, {"title": "No token"}, token=None), 401, None,
        "A post without a token",
    )
    # The suite reaches the emulator over loopback, where the glass may read the
    # summary without a token; everything else is closed even there.
    board_refusal(ctx.api.call("GET", BOARD_ITEMS, token=None), 401, None, "Listing the board without a token")
    ctx.api.expect("DELETE", BOARD_ITEMS + "?all=true")
    version = ctx.status()["boardVersion"]
    try:
        # The guide's own examples, sent as they are written.
        for example in guide["examples"]:
            reply = ctx.api.call(example["method"], example["path"], example.get("body"))
            require(
                reply.status in (200, 201),
                f"The guide's example {example['does']!r} answered {reply.status}: {describe(reply.body)}",
            )
        posted = ctx.api.expect("GET", BOARD_ITEMS)
        require(
            posted["total"] >= 3 and {item["source"] for item in posted["items"]} == {"Validation suite"},
            f"The guide's examples did not leave three items named after this device: {describe(posted)}",
        )
        ctx.api.expect("DELETE", BOARD_ITEMS + "?all=true")

        # An id the caller chose: the same request creates, then replaces.
        todo = {"kind": "todo", "title": "Water the plants", "source": BOARD_SOURCE}
        first = ctx.api.expect("PUT", BOARD_ITEMS + "/plants", todo, status=201)
        second = ctx.api.expect("PUT", BOARD_ITEMS + "/plants", todo)
        require(first["created"] is True and second["created"] is False, "A repeated PUT made a second item")
        done = ctx.api.expect("PATCH", BOARD_ITEMS + "/plants", {"done": True})["item"]
        require(
            done["state"] == "done" and done["title"] == todo["title"] and done["doneAt"],
            f"PATCH did not mark the item done and keep the rest: {describe(done)}",
        )
        reminder = ctx.api.expect(
            "POST", BOARD_ITEMS,
            {"kind": "reminder", "title": "Call", "due": "2099-01-01T09:00:00-08:00", "source": BOARD_SOURCE},
            status=201,
        )["item"]
        require(
            reminder["due"] == 4_070_970_000_000 and reminder["dueIso"] == "2099-01-01T17:00:00Z",
            f"An ISO 8601 time was not read as given: {describe(reminder)}",
        )

        board_refusal(ctx.api.call("POST", BOARD_ITEMS, {"text": "Buy milk"}), 400, "text", "An unknown field")
        board_refusal(
            ctx.api.call("POST", BOARD_ITEMS, {"kind": "reminder", "title": "No time"}), 400, "due",
            "A reminder without a time",
        )
        board_refusal(
            ctx.api.call("POST", BOARD_ITEMS, data=b"not json", content_type="application/json"), 400, None,
            "A body that is not JSON",
        )
        # Text as a program sends it: UTF-8 bytes, whatever the Content-Type says or omits.
        accented = "Caf\u00e9 at 72\u00b0, \u8cb7\u3044\u7269"
        raw = json.dumps({"title": accented, "source": BOARD_SOURCE}, ensure_ascii=False).encode("utf-8")
        for method, path, content_type, status in (
            ("POST", BOARD_ITEMS, "application/json", 201),
            ("POST", BOARD_ITEMS, "application/x-www-form-urlencoded", 201),
            ("PUT", BOARD_ITEMS + "/accented", "application/json", 201),
            ("PATCH", BOARD_ITEMS + "/accented", "application/json", 200),
        ):
            reply = ctx.api.call(method, path, data=raw, content_type=content_type)
            require(
                reply.status == status and reply.body["item"]["title"] == accented,
                f"{method} as {content_type} did not keep its text: {reply.status} {describe(reply.body)}",
            )
        ctx.api.expect("DELETE", f"{BOARD_ITEMS}?source={BOARD_SOURCE}")
        board_refusal(ctx.api.call("PATCH", BOARD_ITEMS + "/missing", {"done": True}), 404, "id", "A missing item")
        board_refusal(ctx.api.call("POST", BOARD_ITEMS + "/plants", todo), 405, None, "POST to an item")
        board_refusal(ctx.api.call("DELETE", BOARD_ITEMS), 400, None, "Removing items without saying which")

        # Pages of the listing, walked the way the guide says.
        for number in range(5):
            ctx.api.expect(
                "PUT", f"{BOARD_ITEMS}/page-{number}",
                {"title": f"Page item {number}", "source": BOARD_SOURCE}, status=201,
            )
        walked, offset, requests = [], 0, 0
        while offset is not None:
            page = ctx.api.expect("GET", f"{BOARD_ITEMS}?source={BOARD_SOURCE}&limit=3&offset={offset}")
            walked += [item["id"] for item in page["items"]]
            offset = page["nextOffset"]
            requests += 1
            require(requests <= 5, "The listing's pages never end")
        whole = [item["id"] for item in ctx.api.expect("GET", f"{BOARD_ITEMS}?source={BOARD_SOURCE}")["items"]]
        require(len(whole) == 5 and walked == whole, f"Walking pages of three gave {walked}, the whole is {whole}")

        # Everything leaves by itself.
        brief = ctx.api.expect(
            "POST", BOARD_ITEMS, {"title": "Brief", "ttlSeconds": 2, "source": BOARD_SOURCE}, status=201
        )
        before_expiry = brief["version"]
        wait_for(
            "an item to leave when its time is up",
            lambda: ctx.api.call("GET", f"{BOARD_ITEMS}/{brief['item']['id']}").status == 404,
            timeout=15,
        )
        require(
            ctx.status()["boardVersion"] > before_expiry,
            "An expired item did not move boardVersion, so the glass would keep showing it",
        )
        removed = ctx.api.expect("DELETE", f"{BOARD_ITEMS}?source={BOARD_SOURCE}")
        require(removed["deleted"] == 5, f"Removing one sender's items removed {removed['deleted']}, expected 5")
        require(ctx.status()["boardVersion"] > version, "Changing the board did not move boardVersion")
        ctx.note("boardExamples", len(guide["examples"]))
    finally:
        ctx.api.call("DELETE", BOARD_ITEMS + "?all=true")
    require(ctx.api.expect("GET", BOARD)["counts"]["total"] == 0, "The board was not left empty")


# What the glass shows of its moments: each one's kind, words and box, and the boxes of what the widgets draw.
MOMENTS_ON_GLASS = """JSON.stringify((function () {
  var box = function (element) {
    var rect = element.getBoundingClientRect();
    return [rect.left, rect.top, rect.right, rect.bottom].map(Math.round);
  };
  var all = function (selector, read) { return [].map.call(document.querySelectorAll(selector), read); };
  return {
    moments: all('.mr-moment:not(.mr-leaving)', function (element) {
      return {
        kind: element.className.replace(/.*mr-m-/, ''),
        text: element.textContent,
        box: box(element),
        shapes: element.querySelectorAll('svg > *').length,
        opacity: Number(getComputedStyle(element).opacity),
        fontSize: parseFloat(getComputedStyle(element).fontSize),
        cutOff: element.firstChild.scrollHeight > element.clientHeight + 1 || element.firstChild.scrollWidth > element.clientWidth + 1,
        bars: [].map.call(element.querySelectorAll('.mr-m-bar'), function (bar) { return Math.round(bar.getBoundingClientRect().height); }),
        dots: element.querySelectorAll('.mr-m-dot').length,
        rows: [].map.call(element.querySelectorAll('.mr-m-row'), function (row) { return Math.round(row.getBoundingClientRect().height); })
      };
    }),
    drawn: all('.mr-widget:not(.mr-leaving) .mr-inner', box)
  };
}()))"""


def boxes_share(first: list[int], second: list[int]) -> bool:
    """Whether two boxes, each left, top, right, bottom, lie over one another."""
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def check_moments(ctx: Context) -> None:
    ctx.require_inspectable()
    api = ctx.api
    api.expect("DELETE", "/api/v1/moments")
    try:
        with ctx.page() as page:
            def glass() -> dict:
                return json.loads(page.evaluate(MOMENTS_ON_GLASS))

            def shown(kind: str) -> dict | None:
                found = [moment for moment in glass()["moments"] if moment["kind"] == kind and moment["opacity"] > 0.9]
                return found[0] if found else None

            # The Mirror's own clock says when a countdown ends; the computer's may differ.
            now = api.expect("GET", "/api/v1/moments")["now"]
            first = api.expect(
                "POST", "/api/v1/moments",
                {"id": "tea", "kind": "countdown", "title": "Tea", "endsAt": now + 95_000}, status=201,
            )
            if first["replaced"] or not first["shown"] or first["moment"]["until"] != now + 115_000:
                raise CheckFailed(f"The countdown was not taken as sent: {describe(first)}")
            api.expect("POST", "/api/v1/moments", {"kind": "text", "text": "Validation moment", "seconds": 8}, status=201)
            api.expect(
                "POST", "/api/v1/moments",
                {
                    "id": "art", "kind": "drawing", "title": "Drawn", "color": "#ff8fa3", "motion": "pulse",
                    "shapes": [
                        {"shape": "circle", "x": 50, "y": 50, "r": 40},
                        {"shape": "line", "x1": 10, "y1": 90, "x2": 90, "y2": 90},
                        {"shape": "rect", "x": 30, "y": 30, "w": 40, "h": 40, "round": 4},
                        {"shape": "path", "d": "M50 30 L70 70 L30 70 Z", "fill": "#ffd9a0"},
                        {"shape": "text", "x": 50, "y": 55, "text": "ok", "size": 9},
                    ],
                },
                status=201,
            )
            wait_for(
                "the three moments to have arrived on the glass",
                lambda: all(shown(kind) for kind in ("countdown", "text", "drawing")),
                timeout=30,
            )
            ctx.screenshot("moments")
            seen = glass()
            if shown("drawing")["shapes"] != 5:
                raise CheckFailed(f"The drawing has {shown('drawing')['shapes']} shapes on the glass, not the 5 sent")
            # The glass found each a place: over nothing that a widget draws, and not over one another.
            for index, moment in enumerate(seen["moments"]):
                others = seen["drawn"] + [other["box"] for other in seen["moments"][:index]]
                if any(boxes_share(moment["box"], other) for other in others):
                    raise CheckFailed(f"The {moment['kind']} at {moment['box']} lies over something drawn: {describe(seen)}")
            running = re.search(r"Tea(\d):(\d\d)", shown("countdown")["text"])
            if not running:
                raise CheckFailed(f"The countdown shows {shown('countdown')['text']!r}, not the time left")
            wait_for(
                "the countdown to run on",
                lambda: shown("countdown")["text"] != running.group(0),
                timeout=10,
            )
            wait_for(
                "the words to leave when their eight seconds are up",
                lambda: all(moment["kind"] != "text" for moment in glass()["moments"]),
                timeout=30,
            )
            # The same id takes the place of what is showing, where it stands.
            before = shown("drawing")["box"]
            second = api.expect(
                "POST", "/api/v1/moments",
                {"id": "art", "kind": "drawing", "title": "Redrawn", "shapes": [{"shape": "circle", "x": 50, "y": 50, "r": 20}]},
            )
            if not second["replaced"]:
                raise CheckFailed(f"The second drawing did not take the first one's place: {describe(second)}")
            wait_for("the drawing to change", lambda: "Redrawn" in (shown("drawing") or {"text": ""})["text"], timeout=15)
            if shown("drawing")["box"] != before or shown("drawing")["shapes"] != 1:
                raise CheckFailed(f"The redrawn drawing moved or kept its shapes: {describe(shown('drawing'))}, was at {before}")
            # What could be more than a drawing is refused, with the field at fault.
            refused = api.expect(
                "POST", "/api/v1/moments",
                {"kind": "drawing", "shapes": [{"shape": "path", "d": "M0 0\"/><script>alert(1)</script>"}]}, status=400,
            )
            if refused.get("field") != "shapes":
                raise CheckFailed(f"The refusal does not name the shapes: {describe(refused)}")
            if api.call("POST", "/api/v1/moments", {"kind": "text", "text": "No"}, token=None).status != 401:
                raise CheckFailed("A moment was taken from a caller without a credential")
            api.expect("DELETE", "/api/v1/moments/tea")
            api.expect("DELETE", "/api/v1/moments/tea", status=404)
            wait_for("the countdown to leave when it is taken down", lambda: shown("countdown") is None, timeout=15)
            # A glass with no room left lets the oldest go for the newest, and the Mirror then lists what shows.
            for number in range(1, 6):
                api.expect(
                    "POST", "/api/v1/moments",
                    {"id": f"big-{number}", "kind": "text", "size": "large", "text": f"Number {number}", "seconds": 120},
                    status=201,
                )
                time.sleep(1.0)

            def words() -> str:
                return " ".join(moment["text"] for moment in glass()["moments"])

            wait_for("the newest of the large words to have arrived", lambda: "Number 5" in words(), timeout=20)
            wait_for(
                "the Mirror to list as many moments as the glass shows",
                lambda: len(api.expect("GET", "/api/v1/moments")["moments"]) == len(glass()["moments"]),
                timeout=20,
            )
            ctx.screenshot("moments-full")
            full = glass()["moments"]
            if any(boxes_share(moment["box"], other["box"]) for index, moment in enumerate(full) for other in full[:index]):
                raise CheckFailed(f"On a full glass, moments lie over one another: {describe(full, 2000)}")
            if "Number 1" in words() or "Redrawn" in words():
                raise CheckFailed(f"The oldest moments did not make room for the newest: {describe(full, 2000)}")
            if api.expect("DELETE", "/api/v1/moments")["removed"] != len(full):
                raise CheckFailed("Clearing the glass did not remove the moments it showed")
            wait_for("the glass to be clear of moments", lambda: glass()["moments"] == [], timeout=15)
            # The other two kinds: a list whose long row goes on under itself, and a chart as bars and as a line.
            api.expect(
                "POST", "/api/v1/moments",
                {
                    "id": "steps", "kind": "list", "title": "Pour-over", "seconds": 60,
                    "rows": [
                        {"label": "1", "text": "Rinse the filter"},
                        {"label": "2", "text": "Bloom with forty grams of hot water for thirty seconds"},
                        {"label": "3", "text": "Pour"},
                    ],
                },
                status=201,
            )
            values = [{"label": day, "value": value} for day, value in (("Mon", 10), ("Tue", 40), ("Wed", 20), ("Thu", 30))]
            api.expect("POST", "/api/v1/moments", {"id": "bars", "kind": "chart", "size": "small", "values": values}, status=201)
            api.expect(
                "POST", "/api/v1/moments",
                {"id": "line", "kind": "chart", "chart": "line", "size": "small", "side": "left", "height": "bottom", "values": values},
                status=201,
            )
            wait_for("the list and the two charts to have arrived", lambda: shown("list") and len(glass()["moments"]) == 3, timeout=20)
            time.sleep(1.0)
            ctx.screenshot("moments-list-charts")
            listed, bars, line = glass()["moments"]
            if listed["cutOff"] or len(listed["rows"]) != 3 or "thirty seconds" not in listed["text"]:
                raise CheckFailed(f"The list does not show its three rows whole: {describe(listed)}")
            # Read from across a room: a long row takes a second line, the writing is not shrunk to fit one.
            if listed["rows"][1] < 1.6 * listed["rows"][0] or listed["fontSize"] < 30:
                raise CheckFailed(f"The long row was shrunk onto one line, or the list is too small to read: {describe(listed)}")
            heights = bars["bars"]
            if len(heights) != 4 or not heights[0] < heights[2] < heights[3] < heights[1]:
                raise CheckFailed(f"The bars do not stand in the order of their values: {describe(bars)}")
            if line["dots"] != 4 or line["shapes"] != 1 or line["cutOff"] or bars["cutOff"]:
                raise CheckFailed(f"The line does not have its four points, or a chart is cut off: {describe(line)}")
            api.expect("DELETE", "/api/v1/moments")
            wait_for("the glass to be clear of moments", lambda: glass()["moments"] == [], timeout=15)
    finally:
        api.call("DELETE", "/api/v1/moments")


def check_board_glass(ctx: Context) -> None:
    """More items than fit: every one of them has to come round, unclipped."""
    ctx.require_inspectable()
    layout = ctx.api.expect("GET", "/api/v1/dashboard/layout")
    original = json.dumps(layout)
    for widget in layout["widgets"]:
        if widget["id"] == "board":
            widget.update({
                "visible": True, "opacity": 100, "text": "Validation", "size": "large", "show": "all",
                "x": 50, "y": 440, "w": 600, "h": 260,
            })
    ctx.api.expect("DELETE", BOARD_ITEMS + "?all=true")
    ctx.api.expect("PUT", "/api/v1/dashboard/layout", layout)
    now = ctx.api.expect("GET", BOARD)["now"]
    items = {
        "soon": {"kind": "reminder", "title": "Leave for the dentist", "due": now + 25 * 60_000},
        "late": {"kind": "todo", "title": "Take out the bins", "due": now - 5 * 60_000},
        "plants": {"kind": "todo", "title": "Water the plants", "body": "Not the cactus"},
        "dinner": {"title": "Dinner is in the oven"},
        "parcel": {"title": "A parcel is at the door", "body": "Signed for next door"},
        "milk": {"kind": "todo", "title": "Buy milk", "priority": "low"},
        "laundry": {"kind": "todo", "title": "Move the laundry", "done": True},
    }
    try:
        for name, item in items.items():
            ctx.api.expect("PUT", f"{BOARD_ITEMS}/{name}", dict(item, source=BOARD_SOURCE), status=201)
        # The glass asks every five seconds; let it hold all seven before reading its pages.
        time.sleep(BOARD_GLASS_POLL_SECONDS)
        titles = {item["title"] for item in items.values()}
        with ctx.page() as page:
            def glass():
                seen = json.loads(page.evaluate(BOARD_ON_GLASS))
                return seen if seen and seen["rows"] and not seen["fading"] else None

            first = wait_for(
                "the board to list the items a page at a time",
                lambda: (lambda seen: seen if seen and seen["dots"] >= 2 else None)(glass()),
                timeout=40,
            )
            require(first["heading"] == "Validation", f"The board's heading reads {first['heading']!r}")
            # Let the list finish fading in, so the picture shows what a person sees.
            time.sleep(1)
            ctx.screenshot("board")
            shown: dict[str, dict] = {}
            pages = []

            def all_shown():
                seen = glass()
                if seen:
                    require(
                        seen["inner"] <= seen["box"] + 1,
                        f"A page of the board is taller than its widget and would be cut off: {seen}",
                    )
                    page_titles = [row["title"] for row in seen["rows"]]
                    if page_titles not in pages:
                        pages.append(page_titles)
                    shown.update({row["title"]: row for row in seen["rows"]})
                return titles.issubset(shown)

            wait_for(
                "every item to come round as the pages turn", all_shown,
                timeout=first["dots"] * BOARD_PAGE_SECONDS + 10, interval=1,
            )
            require(
                len(pages) == first["dots"],
                f"The board shows {first['dots']} page dots but turned through {len(pages)} pages: {pages}",
            )
            listed = [item["title"] for item in ctx.api.expect("GET", BOARD)["items"]]
            require(
                [title for page_titles in sorted(pages, key=lambda rows: listed.index(rows[0]))
                 for title in page_titles] == listed,
                f"The pages {pages} are not the API's order {listed}",
            )
            require(
                re.fullmatch(r"In 2[0-5] min", shown[items["soon"]["title"]]["when"]),
                f"A reminder 25 minutes away reads {shown[items['soon']['title']]['when']!r}",
            )
            require("mr-board-soon" in shown[items["soon"]["title"]]["state"], "A reminder due soon is not marked so")
            require(
                "mr-board-overdue" in shown[items["late"]["title"]]["state"]
                and re.fullmatch(r"\d+ min ago", shown[items["late"]["title"]]["when"]),
                f"An overdue to-do reads {shown[items['late']['title']]}",
            )
            require("mr-board-done" in shown[items["laundry"]["title"]]["state"], "A done to-do is not struck through")
            require("mr-board-low" in shown[items["milk"]["title"]]["state"], "A low priority item is not drawn fainter")
            ctx.note("boardPages", len(pages))

            ctx.api.expect("DELETE", f"{BOARD_ITEMS}?source={BOARD_SOURCE}")
            wait_for(
                "an empty board to leave the glass",
                lambda: page.evaluate("document.querySelector('.mr-board') === null") is True,
                timeout=30,
            )
    finally:
        ctx.api.call("DELETE", BOARD_ITEMS + "?all=true")
        ctx.api.expect("PUT", "/api/v1/dashboard/layout", json.loads(original))


NOTE_TEXT = "Caf\u00e9 at 72\u00b0, \u65e5\u672c \u2600"


def check_note_text(ctx: Context) -> None:
    # As the phone controls send it: UTF-8 bytes, and a Content-Type that names no charset.
    raw = json.dumps({"text": NOTE_TEXT}, ensure_ascii=False).encode("utf-8")
    created = ctx.api.call("POST", "/api/v1/notes", data=raw, content_type="application/json")
    require(created.status == 201, f"Posting a note answered {created.status}: {describe(created.body)}")
    note = created.body["note"]
    try:
        require(note["text"] == NOTE_TEXT, f"The note was answered as {note['text']!r}")
        kept = [entry["text"] for entry in ctx.api.expect("GET", "/api/v1/notes")["notes"] if entry["id"] == note["id"]]
        require(kept == [NOTE_TEXT], f"The note is kept as {kept!r}")
    finally:
        ctx.api.call("DELETE", f"/api/v1/notes/{note['id']}")


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
    kernel = health["memory"]["kernel"]
    largest = health["memory"]["largest"]
    require(
        isinstance(kernel, dict) and kernel["cachedKb"] > 0 and kernel["freeKb"] > 0
        and all(key in kernel for key in ("swapTotalKb", "swapFreeKb")),
        f"What the kernel says of memory was not read: {describe(kernel)}",
    )
    held = [entry["rssKb"] + entry["swapKb"] for entry in largest]
    require(
        # The emulator's Android 6 lets an app read this of every process, so the list is full.
        # (A Mirror's shows an app only other apps.)
        len(largest) == 5 and all(entry["name"] for entry in largest) and held == sorted(held, reverse=True) and held[-1] > 0,
        f"The processes that hold most memory are listed as {describe(largest)}",
    )
    nothing = {"advised": False, "reason": None}
    require(
        health["restart"] == nothing and ctx.status()["restart"] == nothing,
        f"An emulator that has just started asks to be restarted: {describe(health['restart'])}",
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
    ctx.note("kernelMemory", kernel)
    ctx.note("largestProcess", largest[0])
    ctx.note("javaHeapMaxKb", health["memory"]["javaHeapMaxKb"])
    ctx.note("openFiles", health["memory"]["openFiles"])
    ctx.note("densityDpi", display["densityDpi"])
    ctx.note("bundledTzdata", health["clock"]["bundledTzdata"])
    ctx.note("power", health["device"]["power"])


# ---------------------------------------------------------------------------
# What keeps a Mirror running for longer than nine days: Android does not
# scan for Wi-Fi while connected, and the OTA supervisor is not among the
# first that the kernel ends.
# ---------------------------------------------------------------------------


def check_scan_guard(ctx: Context) -> None:
    guard = ctx.api.expect("GET", SCAN_GUARD)
    require(
        guard["supported"],
        "Android 6 has a switch for scanning while connected, and Mirror Home did not find it",
    )
    require(
        not guard["enabled"] and guard["state"] == "off" and guard["scanningWhileConnected"] is True,
        f"Before anyone asked for it, the scan guard stands at {describe(guard)}",
    )
    expect_refused(
        ctx.api.call("PUT", SCAN_GUARD, {"enabled": "yes"}), 400, "A scan guard that is neither on nor off"
    )
    try:
        on = ctx.api.expect("PUT", SCAN_GUARD, {"enabled": True})
        require(
            on["enabled"] and on["state"] == "applied" and on["scanningWhileConnected"] is False
            and on["appliedAt"] and on["applied"] == 1,
            f"Turned on, the scan guard reports {describe(on)}",
        )
        brief = ctx.status()["wifi"]["scanGuard"]
        require(
            brief == {"enabled": True, "supported": True, "state": "applied", "detail": ""},
            f"The status says of the scan guard: {describe(brief)}",
        )
        # Mirror Home starts again, as after an update; Android does not, and has not forgotten.
        before = wait_past_start(ctx)
        ctx.restart_home()
        wait_for_run(ctx, before["process"]["runId"] + 1)
        kept = wait_for(
            "the scan guard to look again after Mirror Home started",
            lambda: (lambda seen: seen if seen["checks"] else None)(ctx.health()["wifi"]["scanGuard"]),
            timeout=30,
        )
        require(
            kept["enabled"] and kept["state"] == "applied" and kept["scanningWhileConnected"] is False,
            f"After Mirror Home started again the scan guard reports {describe(kept)}",
        )
        require(
            kept["applied"] == 0,
            f"Mirror Home set a switch again that still stood: {describe(kept)}",
        )
        ctx.wait_dashboard()
        ctx.wait_lit("scan-guard")
    finally:
        off = ctx.api.call("PUT", SCAN_GUARD, {"enabled": False})
    require(off.status == 200, f"The scan guard could not be turned off: {describe(off.body)}")
    require(
        not off.body["enabled"] and off.body["state"] == "off" and off.body["scanningWhileConnected"] is True,
        f"Turned off, Android was not put back to scanning as before: {describe(off.body)}",
    )
    require(
        isinstance(on.get("scans"), dict) and all(key in on["scans"] for key in ("whileConnected", "sinceApplied", "lastAt")),
        f"The scan guard does not count the scans that arrive: {describe(on)}",
    )
    ctx.note("scanGuard", {key: on[key] for key in ("state", "scanningWhileConnected", "scans")})


def supervisor_held(ctx: Context, other_than: int | None = None) -> dict | None:
    """What health says of a supervisor that is held and answering, in a process other than that one."""
    seen = ctx.health()["otaSupervisor"]
    hold = seen.get("hold") or {}
    held = hold.get("state") == "held" and hold.get("pid") not in (None, other_than) and seen.get("listening")
    return seen if held else None


def require_ranked_as_needed(seen: dict, when: str) -> int:
    score = seen["hold"]["oomScoreAdj"]
    require(
        score is not None and score <= HELD_SCORE,
        f"{when}, the kernel ranks the supervisor at {score}; what the display needs is at "
        f"{HELD_SCORE} or under, a background service at {SERVICE_SCORE}",
    )
    return score


def check_updater_held(ctx: Context) -> None:
    if ctx.supervisor_apk is None:
        raise CheckSkipped(
            "no OTA supervisor signed like this Mirror Home was named; see --supervisor-apk"
        )
    require(
        ctx.health()["otaSupervisor"] == {"installed": False},
        f"A supervisor is already installed: {describe(ctx.health()['otaSupervisor'])}",
    )
    scores = {}
    try:
        installed = ctx.adb.run("install", "-r", str(ctx.supervisor_apk), timeout=300)
        require("Success" in installed, f"The supervisor was not installed: {installed.strip()}")
        first = wait_for(
            "Mirror Home to take hold of the OTA supervisor",
            lambda: supervisor_held(ctx),
            timeout=HOLD_SECONDS,
        )
        scores["held"] = require_ranked_as_needed(first, "Held by Mirror Home")
        # What the kernel does to it when memory runs short.
        ctx.adb.shell("kill", "-9", str(first["hold"]["pid"]))
        second = wait_for(
            "an ended supervisor to be started and held again",
            lambda: supervisor_held(ctx, first["hold"]["pid"]),
            timeout=HOLD_SECONDS,
        )
        scores["afterBeingEnded"] = require_ranked_as_needed(second, "Started again")
        # Replaced, as its owner updates it; Android drops the hold with the old one.
        replaced = ctx.adb.run("install", "-r", str(ctx.supervisor_apk), timeout=300)
        require("Success" in replaced, f"The supervisor was not replaced: {replaced.strip()}")
        third = wait_for(
            "a replaced supervisor to be held again",
            lambda: supervisor_held(ctx, second["hold"]["pid"]),
            timeout=HOLD_SECONDS,
        )
        scores["afterBeingReplaced"] = require_ranked_as_needed(third, "Replaced")
        # The supervisor must not need Mirror Home: it is what puts a Mirror Home that fails right.
        before = wait_past_start(ctx)
        ctx.restart_home()
        require(
            process_running(ctx, SUPERVISOR),
            "The supervisor ended when Mirror Home was stopped",
        )
        wait_for_run(ctx, before["process"]["runId"] + 1)
        fourth = wait_for(
            "Mirror Home to take hold of the supervisor after starting again",
            lambda: supervisor_held(ctx),
            timeout=HOLD_SECONDS,
        )
        require(
            fourth["hold"]["pid"] == third["hold"]["pid"],
            f"The supervisor did not carry on while Mirror Home was away: process {third['hold']['pid']} "
            f"became {fourth['hold']['pid']}",
        )
        ctx.wait_dashboard()
        ctx.wait_lit("updater-held")
    finally:
        ctx.adb.run("uninstall", SUPERVISOR, check=False)
    wait_for(
        "Mirror Home to notice that the supervisor was removed",
        lambda: ctx.health()["otaSupervisor"] == {"installed": False},
        timeout=HOLD_SECONDS,
    )
    ctx.note("supervisorVersion", first.get("versionName"))
    ctx.note("oomScoreAdj", scores)


# ---------------------------------------------------------------------------
# Voice commands. The suite speaks through two doors that only a debug build
# has: one takes a recording in place of the microphone, the other a sentence
# as if the recogniser had just heard it.
# ---------------------------------------------------------------------------


def voice_state(ctx: Context) -> dict:
    return ctx.api.expect("GET", "/api/v1/voice")


def process_running(ctx: Context, name: str) -> bool:
    return any(line.split()[-1:] == [name] for line in ctx.adb.shell("ps").splitlines())


def voice_process_running(ctx: Context) -> bool:
    return process_running(ctx, VOICE_PROCESS)


def model_archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def wait_voice(ctx: Context, wanted: str, timeout: float) -> dict:
    """Wait for voice to reach a state; say where it stood if it never does."""
    seen: dict = {}

    def reached() -> bool:
        seen.update(voice_state(ctx))
        return seen["state"] == wanted

    try:
        wait_for(f"voice to be {wanted}", reached, timeout=timeout)
    except CheckFailed:
        raise CheckFailed(
            f"Voice was not {wanted} within {int(timeout)} s; it is "
            f"{seen.get('state', 'unanswered')}: {seen.get('detail', '')}"
        ) from None
    return dict(seen)


def listening_voice(ctx: Context, *, spoken_to: bool = True) -> dict:
    """Voice listening with the speech model: as an earlier check left it, or set up now.

    Skips the check where that cannot be: on a computer without the model,
    and, if the check speaks to the Mirror, on a release build.
    """
    state = voice_state(ctx)
    if spoken_to and not state["testHooks"]:
        raise CheckSkipped(NO_TEST_SPEECH)
    if ctx.voice_model is None:
        raise CheckSkipped(NO_SPEECH_MODEL)
    if not ctx.voice_checksum:
        ctx.voice_checksum = voice.sha256_file(ctx.voice_model)
    if not state["model"] or state["model"]["sha256"] != ctx.voice_checksum:
        installed = ctx.api.expect(
            "PUT", "/api/v1/voice/model",
            data=ctx.voice_model.read_bytes(),
            content_type="application/zip",
            headers={"X-Content-SHA256": ctx.voice_checksum},
            timeout=600,
            status=201,
        )
        require(
            installed["model"] and installed["model"]["sha256"] == ctx.voice_checksum,
            f"The Mirror reports another model than the one sent: {installed['model']}",
        )
    if not state["enabled"]:
        ctx.api.expect("PUT", "/api/v1/voice", {"enabled": True})
    ctx.voice_left_on = True
    return wait_voice(ctx, "listening", VOICE_LISTENING_SECONDS)


def hear(ctx: Context, clip: str) -> None:
    """Play a recording to the recogniser in place of the microphone."""
    ctx.api.expect(
        "POST", "/api/v1/voice/test/clip",
        data=(VOICE_CLIPS / clip).read_bytes(), content_type="audio/wav", status=202,
    )


def say(ctx: Context, text: str, confidence: float = 1.0) -> None:
    """Hand over a sentence as if the recogniser had just heard it."""
    ctx.api.expect(
        "POST", "/api/v1/voice/test/sentence", {"text": text, "confidence": confidence}, status=202
    )


def asleep(ctx: Context) -> bool:
    return ctx.api.expect("GET", "/api/v1/automation")["sleeping"]


def check_voice_off(ctx: Context) -> None:
    state = voice_state(ctx)
    require(
        state["enabled"] is False and state["state"] == "off",
        f"Voice is {state['state']} before anyone switched it on",
    )
    require(state["model"] is None, f"A speech model is installed already: {state['model']}")
    require(not voice_process_running(ctx), "The recogniser's process runs although voice is off")
    said = [sentence for command in state["commands"] for sentence in command["say"]]
    require(
        state["wakeWord"] == "mirror" and said and all(sentence.startswith("mirror ") for sentence in said),
        f"The controls are not told what can be said: {describe(state['commands'])}",
    )
    require(ctx.status()["voice"]["state"] == "off", "The status does not say that voice is off")
    require(ctx.health()["voice"]["state"] == "off", "The health report does not say that voice is off")
    require(
        ctx.api.call("GET", "/api/v1/voice", token="not-a-credential").status == 401,
        "Voice can be read without a pairing",
    )
    require(
        ctx.api.call("PUT", "/api/v1/voice", {"enabled": "yes"}).status == 400,
        "A switch position that is neither on nor off is accepted",
    )
    wanted = ctx.api.expect("PUT", "/api/v1/voice", {"enabled": True})
    try:
        require(
            wanted["state"] == "no-model",
            f"Switched on without a speech model, voice is {wanted['state']}",
        )
        time.sleep(2)
        require(not voice_process_running(ctx), "The recogniser's process runs without a speech model")
        if wanted["testHooks"]:
            unheard = ctx.api.call("POST", "/api/v1/voice/test/sentence", {"text": "mirror go to sleep"})
            require(
                unheard.status == 409 and not asleep(ctx),
                "A sentence was taken while nothing listens",
            )
    finally:
        ctx.api.expect("PUT", "/api/v1/voice", {"enabled": False})
    ctx.note("canBeSaid", said)
    ctx.note("microphoneAllowed", state["permissionGranted"])


def check_voice_model(ctx: Context) -> None:
    def upload(data: bytes, content_type: str = "application/zip", **options) -> Reply:
        return ctx.api.call(
            "PUT", "/api/v1/voice/model", data=data, content_type=content_type, timeout=120, **options
        )

    # It has every file Mirror Home looks for, and nothing in them.
    hollow = model_archive({
        f"hollow-model/{name}": b"not what a speech model holds\n" * 40 for name in VOICE_MODEL_FILES
    })
    refused = (
        ("an archive sent as something else", upload(hollow, "application/json"), 415),
        ("what is no archive", upload(b"no archive at all " * 64), 400),
        ("an archive without a model", upload(model_archive({"notes/README": b"nothing"})), 400),
        (
            "an archive that names a file outside itself",
            upload(model_archive({"../../shared_prefs/planted.xml": b"<map/>"})),
            400,
        ),
        (
            "an archive that is not what its checksum says",
            upload(hollow, headers={"X-Content-SHA256": "0" * 64}),
            400,
        ),
    )
    for what, reply, status in refused:
        require(
            reply.status == status,
            f"{what} was answered {reply.status}, not {status}: {describe(reply.body)}",
        )
    require(voice_state(ctx)["model"] is None, "A refused upload left a speech model behind")
    if ctx.inspectable():
        planted = ctx.adb.shell("run-as", PACKAGE, "ls", "shared_prefs", check=False)
        require("planted" not in planted, "An archive wrote a file outside the model's folder")

    run = ctx.health()["process"]["runId"]
    installed = upload(hollow)
    require(installed.status == 201, f"A well-formed archive was refused: {describe(installed.body)}")
    require(
        installed.body["model"]["name"] == "hollow-model",
        f"The model is reported as {installed.body['model']}",
    )
    ctx.api.expect("PUT", "/api/v1/voice", {"enabled": True})
    try:
        failed = wait_voice(ctx, "error", 60)
        health = ctx.health()
        require(
            health["process"]["runId"] == run and health["crashes"]["count"] == 0,
            "Mirror Home did not carry on over a speech model that cannot be loaded",
        )
        require(
            health["voice"]["state"] == "error",
            f"The health report says voice is {health['voice']['state']}",
        )
    finally:
        ctx.api.expect("PUT", "/api/v1/voice", {"enabled": False})
        removed = ctx.api.expect("DELETE", "/api/v1/voice/model")
    require(removed["model"] is None and removed["state"] == "off", f"After removal: {describe(removed)}")
    wait_for(
        "the recogniser's process to end", lambda: not voice_process_running(ctx), timeout=20
    )
    ctx.note("unloadableModel", failed["detail"])


def check_voice_listens(ctx: Context) -> None:
    listening = listening_voice(ctx, spoken_to=False)
    require(voice_process_running(ctx), "No process of its own holds the recogniser")
    # It measures itself every five seconds.
    measured: dict = {}

    def has_measured() -> bool:
        measured.update(voice_state(ctx))
        return measured["recogniser"]["cpuShare"] is not None

    wait_for("the recogniser's first measurements", has_measured, timeout=30)
    require(measured["state"] == "listening", f"Voice stopped listening: {measured['detail']}")
    memory = measured["process"]["pssKb"]
    require(memory and memory < 250_000, f"The recogniser's process holds {memory} KB")
    require(
        measured["recogniser"]["behindMs"] < 2_000,
        f"The recogniser is {measured['recogniser']['behindMs']} ms behind the microphone",
    )
    health = ctx.health()["voice"]
    require(health["state"] == "listening", f"The health report says voice is {health['state']}")
    require(ctx.status()["voice"]["state"] == "listening", "The status does not say that voice listens")
    ctx.note("model", listening["model"]["name"])
    ctx.note("modelLoadMs", measured["recogniser"]["modelLoadMs"])
    ctx.note("recogniserPssKb", memory)
    ctx.note("cpuShare", measured["recogniser"]["cpuShare"])
    ctx.note("microphoneSilent", measured["microphone"]["silent"])


def check_voice_commands(ctx: Context) -> None:
    listening_voice(ctx)
    ctx.lit_peak()
    # A spoken "wake up" ends an earlier check's leftovers as well as a sleep.
    say(ctx, "mirror wake up")
    wait_for("the Mirror to be awake", lambda: not asleep(ctx), timeout=10)
    before = voice_state(ctx)["counts"]
    try:
        hear(ctx, "mirror-go-to-sleep.wav")
        wait_for("the Mirror to sleep when told to", lambda: asleep(ctx), timeout=VOICE_ACT_SECONDS)
        ctx.wait_peak("black", lambda peak: peak <= BLACK_PEAK, "voice-asleep")
        hear(ctx, "mirror-wake-up.wav")
        wait_for("the Mirror to wake when told to", lambda: not asleep(ctx), timeout=VOICE_ACT_SECONDS)
        ctx.wait_lit("voice-awake")
        acted = voice_state(ctx)
        require(
            acted["counts"]["commands"] == before["commands"] + 2,
            f"Two commands were spoken and {acted['counts']['commands'] - before['commands']} counted",
        )
        hear(ctx, "talk-of-sleep.wav")
        wait_for(
            "the recogniser to have heard the talk",
            lambda: voice_state(ctx)["counts"]["sentences"] > acted["counts"]["sentences"],
            timeout=VOICE_ACT_SECONDS,
        )
        # Its last words may arrive as a sentence of their own.
        time.sleep(3)
        after = voice_state(ctx)
        require(
            not asleep(ctx) and after["counts"]["commands"] == acted["counts"]["commands"],
            f"Talk that holds a command's words was taken for a command: {describe(after['recent'][-2:])}",
        )
    finally:
        # A sleep that was asked for lasts four hours; saving the schedule ends it.
        ctx.set_automation(enabled=False)
    ctx.note("heard", [entry["heard"] for entry in after["recent"][-2:]])
    ctx.note("sentences", after["counts"]["sentences"] - before["sentences"])


def check_voice_wake_word(ctx: Context) -> None:
    listening_voice(ctx)
    ctx.lit_peak()

    def brightness() -> int:
        return ctx.api.expect("GET", "/api/v1/automation")["wakeBrightness"]

    def shown() -> str:
        return voice_state(ctx)["lastCommand"]["shown"]

    try:
        usual = ctx.set_automation(enabled=False)["wakeBrightness"]
        # A command ends the wait that an earlier name may have begun. The
        # sentence is taken after the request has been answered, and a Mirror
        # that is awake already shows nothing of it: wait for it to be counted.
        taken = voice_state(ctx)["counts"]["commands"]
        say(ctx, "mirror wake up")

        def counted_since() -> dict | None:
            counts = voice_state(ctx)["counts"]
            return counts if counts["commands"] > taken else None

        start = wait_for("the Mirror to take a command", counted_since, timeout=10)
        wait_for("the Mirror to be awake", lambda: not asleep(ctx), timeout=10)

        say(ctx, "go to sleep")
        # Long enough for the glass to have stopped saying that it woke.
        time.sleep(VOICE_CAPTION_SECONDS + 1)
        require(not asleep(ctx), "A command without the Mirror's name was carried out")

        frames = [ctx.adb.capture()]
        unlit = screen_capture.lit_fraction(frames[0], 128, VOICE_CAPTION_BOX)

        def captioned() -> bool:
            frames.append(ctx.adb.capture())
            return screen_capture.lit_fraction(frames[-1], 128, VOICE_CAPTION_BOX) > unlit + VOICE_CAPTION_LIT

        say(ctx, "mirror")
        try:
            wait_for("a caption", captioned, timeout=VOICE_WINDOW_SECONDS, interval=0.1)
        except CheckFailed:
            raise CheckFailed("The glass did not show that the Mirror listens after its name") from None
        finally:
            (ctx.output / "voice-listening.png").write_bytes(screen_capture.to_png(frames[-1]))

        # The name, a pause, then the command: two sentences to the recogniser.
        say(ctx, "mirror")
        say(ctx, "go to sleep")
        wait_for("a command that follows the name to be carried out", lambda: asleep(ctx), timeout=10)

        say(ctx, "mirror brighter")
        wait_for("a dark Mirror to wake when spoken to", lambda: not asleep(ctx), timeout=10)
        require(
            shown() == "Awake" and brightness() == usual,
            f"Spoken to while dark, the Mirror showed {shown()!r} and wakes at {brightness()}",
        )
        say(ctx, "mirror brighter")
        wait_for(
            "the wake brightness to rise",
            lambda: brightness() == usual + VOICE_BRIGHTNESS_STEP,
            timeout=10,
        )
        require(shown() == "Brighter", f"The glass showed {shown()!r} for brighter")
        say(ctx, "mirror dimmer")
        wait_for("the wake brightness to fall again", lambda: brightness() == usual, timeout=10)
        say(ctx, "mirror dimmer", 0.6)
        time.sleep(1)
        require(brightness() == usual, "A command the recogniser was unsure of was carried out")

        say(ctx, "mirror [unk]")
        wait_for(
            "the glass to say that it did not follow",
            lambda: "catch that" in ctx.native_text(),
            timeout=VOICE_WINDOW_SECONDS,
            interval=0.1,
        )

        say(ctx, "mirror")
        time.sleep(VOICE_WINDOW_SECONDS + 1.5)
        say(ctx, "go to sleep")
        time.sleep(1)
        require(not asleep(ctx), "A command was carried out long after the Mirror's name")

        counts = voice_state(ctx)["counts"]
        counted = {key: counts[key] - start[key] for key in ("commands", "wakeWords", "notUnderstood", "unsure")}
        require(
            counted == {"commands": 4, "wakeWords": 3, "notUnderstood": 1, "unsure": 1},
            f"What was said is counted as {counted}",
        )
    finally:
        ctx.set_automation(enabled=False)
    ctx.note("counted", counted)


def check_voice_recovers(ctx: Context) -> None:
    listening = listening_voice(ctx, spoken_to=False)
    run = ctx.health()["process"]["runId"]
    stopped = listening["process"]["pid"]
    ctx.adb.shell("kill", str(stopped))

    def back():
        state = voice_state(ctx)
        again = state["state"] == "listening" and state["process"]["pid"] not in (None, stopped)
        return state if again else None

    returned = wait_for("the recogniser to come back", back, timeout=VOICE_LISTENING_SECONDS)
    require(
        returned["process"]["restarts"] == listening["process"]["restarts"] + 1,
        f"The stop was not counted: {returned['process']}",
    )
    health = ctx.health()
    require(
        health["process"]["runId"] == run and health["crashes"]["count"] == 0,
        "Mirror Home did not carry on when its recogniser stopped",
    )
    require(health["activity"]["showing"], f"The dashboard is not in front: {health['activity']}")
    if returned["testHooks"]:
        try:
            hear(ctx, "mirror-go-to-sleep.wav")
            wait_for(
                "the Mirror to follow a command after the recogniser came back",
                lambda: asleep(ctx),
                timeout=VOICE_ACT_SECONDS,
            )
        finally:
            ctx.set_automation(enabled=False)
    ctx.note("stoppedProcess", stopped)
    ctx.note("newProcess", returned["process"]["pid"])


def check_voice_steps_aside(ctx: Context) -> None:
    listening = listening_voice(ctx, spoken_to=False)
    run = ctx.health()["process"]["runId"]
    # An update begins with Android being told of an installation. Android 6
    # compiles what it installs, with memory that the recogniser would hold.
    created = ctx.adb.shell("pm", "install-create")
    session = re.search(r"\[(\d+)\]", created)
    require(session is not None, f"Android began no installation: {created.strip()}")
    try:
        wait_voice(ctx, "paused", 20)
        wait_for(
            "the recogniser's process to end while an app is installed",
            lambda: not voice_process_running(ctx),
            timeout=20,
        )
    finally:
        ctx.adb.shell("pm", "install-abandon", session.group(1), check=False)
    returned = wait_voice(ctx, "listening", VOICE_LISTENING_SECONDS)
    require(
        returned["process"]["restarts"] == listening["process"]["restarts"],
        f"Stepping aside was counted as a recogniser that stopped: {returned['process']}",
    )
    require(
        ctx.health()["process"]["runId"] == run,
        "Mirror Home did not carry on while its recogniser stepped aside",
    )
    ctx.note("installation", int(session.group(1)))


def check_voice_permission(ctx: Context) -> None:
    listening_voice(ctx, spoken_to=False)
    ctx.keep_console_errors(ctx.health())
    # Android stops an app that loses a permission. A Mirror that was updated
    # from a release without voice is where this one starts: not allowed yet.
    ctx.adb.shell("pm", "revoke", PACKAGE, RECORD_AUDIO)
    try:
        waiting = wait_voice(ctx, "no-permission", 60)
        require(not waiting["permissionGranted"], "Voice reports a permission that was taken away")
        require(not voice_process_running(ctx), "The recogniser runs without the microphone permission")
    finally:
        ctx.adb.shell("pm", "grant", PACKAGE, RECORD_AUDIO)
    # Nobody restarts anything: Mirror Home looks every ten seconds.
    wait_voice(ctx, "listening", VOICE_LISTENING_SECONDS)
    ctx.wait_dashboard()
    ctx.wait_lit("voice-permission")


def expect_refused(reply: Reply, status: int, what: str) -> None:
    require(reply.status == status, f"{what} answered {reply.status}, expected {status}: {describe(reply.body)}")
    require(
        isinstance(reply.body, dict) and reply.body.get("error"),
        f"{what} was refused without saying why: {describe(reply.body)}",
    )


def assistant_state(ctx: Context) -> dict:
    return ctx.api.expect("GET", "/api/v1/assistant")


@contextlib.contextmanager
def companion_for(ctx: Context):
    """A stand-in companion that Mirror Home asks, until the check ends."""
    with fake_companion.FakeCompanion() as companion:
        try:
            wanted = ctx.api.expect(
                "PUT", "/api/v1/assistant",
                {"enabled": True, "address": companion.emulator_address + "/", "key": companion.key},
            )
            require(
                wanted["enabled"] and wanted["address"] == companion.emulator_address and wanted["keySet"],
                f"The companion was not kept as it was set: {describe(wanted)}",
            )
            require(companion.key not in json.dumps(wanted), "The Mirror shows the companion's key again")
            seen: dict = {}

            def connected() -> bool:
                seen.update(assistant_state(ctx))
                return seen["state"] == "connected"

            try:
                wait_for("the Mirror to find its companion", connected, timeout=30)
            except CheckFailed:
                raise CheckFailed(
                    f"The Mirror did not find a companion on this computer; the assistant is "
                    f"{seen.get('state')}: {seen.get('detail')}"
                ) from None
            yield companion
        finally:
            ctx.api.expect("PUT", "/api/v1/assistant", {"enabled": False, "address": "", "key": ""})


def jpeg_size(picture: bytes) -> tuple[int, int]:
    """The width and height that a JPEG file's frame header gives."""
    position = 2
    while position + 9 <= len(picture) and picture[position] == 0xFF:
        marker = picture[position + 1]
        if marker == 0xFF:
            position += 1
            continue
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return (
                int.from_bytes(picture[position + 7:position + 9], "big"),
                int.from_bytes(picture[position + 5:position + 7], "big"),
            )
        position += 2 + int.from_bytes(picture[position + 2:position + 4], "big")
    raise CheckFailed("The picture is not a JPEG with a frame header")


def on_glass(ctx: Context, words: str, what: str, timeout: float = ASSISTANT_SHOWN_SECONDS) -> str:
    """Waits for words to stand on the glass; gives the view hierarchy that had them, for where they stood."""
    try:
        return wait_for(
            what, lambda: (lambda glass: glass if words in glass else None)(ctx.native_text()),
            timeout=timeout, interval=0.1,
        )
    except CheckFailed:
        raise CheckFailed(f"The glass did not show {what}: {words!r}") from None


def check_assistant_off(ctx: Context) -> None:
    state = assistant_state(ctx)
    require(
        state["enabled"] is False and state["state"] == "off" and state["address"] == "" and not state["keySet"],
        f"The assistant is set up before anyone did so: {describe(state)}",
    )
    require(
        state.get("mascot") == "none" and len(state.get("mascots") or []) >= 3
        and all(each["id"] and each["name"] for each in state["mascots"]),
        f"A Mirror answers as a character before anyone chose one, or offers none: {describe(state.get('mascot'))} "
        f"of {describe(state.get('mascots'))}",
    )
    places = state.get("places") or {}
    require(
        state.get("place") == FIRST_PLACE
        and (places.get("heights") or [])[:1] == ["top"] and FIRST_PLACE["height"] in places["heights"]
        and {"left", "center", "right"} <= set(places.get("sides") or []),
        f"A Mirror's answers do not start low and in the middle, or cannot be moved: "
        f"{describe(state.get('place'))} of {describe(places)}",
    )
    # The status also says where the answers stand: the glass keeps what it places by itself clear of them.
    require(
        ctx.status()["assistant"] == {"enabled": False, "state": "off", "place": FIRST_PLACE},
        "The status does not say that the assistant is off",
    )
    health = ctx.health()["assistant"]
    require(health["state"] == "off" and "recent" not in health, f"The health report says of the assistant: {describe(health)}")
    for path in ("/api/v1/assistant", "/api/v1/screenshot"):
        require(
            ctx.api.call("GET", path, token="not-a-credential").status == 401,
            f"{path} can be read without a pairing",
        )
    for body, what in (
        ({"enabled": "yes"}, "A switch position that is neither on nor off"),
        ({"address": 8790}, "An address that is no text"),
        ({"address": "ftp://10.0.2.2"}, "An address that is no web address"),
        ({"address": "http://10.0.2.2:8790/v1/health"}, "An address with a path"),
        ({"address": "http://someone:secret@10.0.2.2:8790"}, "An address with a password in it"),
        ({"key": "two words"}, "A key with a space in it"),
        ({"key": "k" * 257}, "A key of 257 characters"),
        ({"mascot": "dragon"}, "A character that there is none of"),
        ({"mascot": 3}, "A character that is no text"),
        ({"place": "top"}, "A place that is one word"),
        ({"place": {"height": "ceiling"}}, "A height that there is none of"),
        ({"place": {"side": 3}}, "A side that is no text"),
        ({"mascot": "blink", "place": {"side": "middle"}}, "A character together with a side that there is none of"),
    ):
        expect_refused(ctx.api.call("PUT", "/api/v1/assistant", body), 400, what)
    require(assistant_state(ctx) == state, "A refused setting changed something")
    expect_refused(
        ctx.api.call("POST", "/api/v1/assistant/ask", {"text": "Is anyone there?"}),
        503, "A request while the assistant is off",
    )
    for body, what in (
        ({}, "A line without text"),
        ({"text": "   "}, "An empty line"),
        ({"text": "x" * 201}, "A line of 201 characters"),
        ({"text": "two\nlines"}, "Two lines"),
        ({"text": "Louder", "kind": "shout"}, "A line of an unknown kind"),
        ({"text": "Briefly", "seconds": 1}, "A line for one second"),
        ({"text": "Today", "details": "Dry"}, "Rows that are no list"),
        ({"text": "Today", "details": [{"label": "Weather"}]}, "A row without words"),
        ({"text": "Today", "details": [{"label": "A label of fifteen", "text": "Dry"}]}, "A row with a label of 18 characters"),
        ({"text": "Today", "details": [{"label": "Weather", "text": "x" * 91}]}, "A row of 91 characters"),
        ({"text": "Today", "details": [{"label": "To do", "text": "Row"}] * 6}, "Six rows"),
    ):
        expect_refused(ctx.api.call("POST", "/api/v1/assistant/say", body), 400, what)
    try:
        waiting = ctx.api.expect("PUT", "/api/v1/assistant", {"enabled": True})
        require(
            waiting["state"] == "unconfigured",
            f"Switched on without a companion, the assistant is {waiting['state']}",
        )
        expect_refused(
            ctx.api.call("POST", "/api/v1/assistant/ask", {"text": "Is anyone there?"}),
            503, "A request without a companion",
        )
    finally:
        ctx.api.expect("PUT", "/api/v1/assistant", {"enabled": False})


def check_assistant_asks(ctx: Context) -> None:
    ctx.lit_peak()
    ctx.set_automation(enabled=False)
    with companion_for(ctx) as companion:
        state = assistant_state(ctx)
        before = state["counts"]
        require(state["model"] == "scripted", f"The Mirror does not say what answers: {describe(state)}")
        require(ctx.status()["assistant"]["state"] == "connected", "The status does not say that a companion answers")
        greeted = companion.sent("/v1/health")
        require(
            greeted and all(request["headers"].get("authorization") == "Bearer " + companion.key for request in greeted),
            "The Mirror asked its companion without the key",
        )

        reply = "Calm and clear tonight \u2013 18\u00b0 outside, and nothing left on your list."
        companion.script({"heard": "What kind of evening is it?", "reply": reply, "acted": ["get_state"]})
        answer = ctx.api.expect("POST", "/api/v1/assistant/ask", {"text": "What kind of evening is it?"}, timeout=70)
        require(answer["reply"] == reply, f"The companion's answer came back as {describe(answer)}")
        asked = companion.sent("/v1/ask")
        require(
            len(asked) == 1
            and json.loads(asked[0]["body"].decode("utf-8")) == {"text": "What kind of evening is it?", "source": "controls"},
            f"The companion was asked {describe([request['body'] for request in asked])}",
        )
        on_glass(ctx, "Calm and clear tonight", "the companion's answer")
        ctx.screenshot("assistant-reply")

        shown = ctx.api.expect(
            "POST", "/api/v1/assistant/say", {"text": "The washing is done", "kind": "notice", "seconds": 8}
        )
        require(shown == {"shown": True}, f"A line for an awake glass was answered with {describe(shown)}")
        on_glass(ctx, "The washing is done", "a line that the companion sent")
        # An answer in several parts: a headline, and a row for each part under it.
        card = ctx.api.expect("POST", "/api/v1/assistant/say", {
            "text": "While you were away",
            "kind": "notice",
            "seconds": 12,
            "details": [
                {"label": "Missed", "text": "Start the dishwasher, due at 9:00 PM"},
                {"label": "To do", "text": "Water the plants"},
                {"text": "A row without a label"},
            ],
        })
        require(card == {"shown": True}, f"A line with rows under it was answered with {describe(card)}")
        on_glass(ctx, "Start the dishwasher, due at 9:00 PM", "the first row under a line")
        glass = ctx.native_text()
        require(
            all(words in glass for words in ("While you were away", "MISSED", "TO DO", "Water the plants", "A row without a label")),
            "The glass does not show a line with its rows and their labels",
        )
        time.sleep(1)
        ctx.screenshot("assistant-card")
        ctx.api.expect("POST", "/api/v1/assistant/say", {"text": "The washing is done", "kind": "notice", "seconds": 8})
        on_glass(ctx, "The washing is done", "the line that followed the rows")
        require("Water the plants" not in ctx.native_text(), "The rows of one line stayed under the next")

        # A character, once chosen, says hello and then stands above whatever the Mirror says.
        figure = state["mascots"][1]
        there = f'content-desc="{figure["name"]}"'
        try:
            chosen = ctx.api.expect("PUT", "/api/v1/assistant", {"mascot": figure["id"]})
            require(
                chosen["mascot"] == figure["id"] and chosen["state"] == "connected",
                f"Choosing a character left the assistant {chosen['state']} with {describe(chosen['mascot'])}",
            )
            on_glass(ctx, f'text="{figure["name"]}"', "the hello of the character that was chosen")
            require(there in ctx.native_text(), "A character says hello without being on the glass")
            time.sleep(1)
            ctx.screenshot("assistant-mascot")
            ctx.api.expect("POST", "/api/v1/assistant/say", {"text": "The washing is done", "kind": "notice", "seconds": 4})
            on_glass(ctx, "The washing is done", "a line under the character")
            require(there in ctx.native_text(), "The character left when the next line came")
        finally:
            ctx.api.expect("PUT", "/api/v1/assistant", {"mascot": "none"})
        ctx.api.expect("POST", "/api/v1/assistant/say", {"text": "The washing is done", "kind": "notice", "seconds": 4})
        on_glass(ctx, "The washing is done", "a line without the character")
        require(there not in ctx.native_text(), "A character that was sent away is still on the glass")

        # The answers can be moved. A line shows the new place, and what the Mirror says next stands there.
        try:
            moved = ctx.api.expect("PUT", "/api/v1/assistant", {"place": {"height": "top", "side": "left"}})
            require(
                moved["place"] == {"height": "top", "side": "left"} and moved["state"] == "connected",
                f"Moving the answers left the assistant {moved['state']} with them at {describe(moved['place'])}",
            )
            shown = on_glass(ctx, PLACE_NOTICE, "the line that shows where the answers went")
            above = node_center(shown, "text", PLACE_NOTICE)
            time.sleep(1)
            ctx.screenshot("assistant-place")
        finally:
            back = ctx.api.expect("PUT", "/api/v1/assistant", {"place": {"side": "center", "height": "bottom"}})
        require(back["place"] == FIRST_PLACE, f"Moved back, the answers are at {describe(back['place'])}")
        ctx.api.expect("POST", "/api/v1/assistant/say", {"text": "The washing is done", "kind": "notice", "seconds": 4})
        shown = on_glass(ctx, "The washing is done", "a line where the answers went back to")
        below = node_center(shown, "text", "The washing is done")
        require(
            above is not None and below is not None and above[0] < below[0] and above[1] < below[1],
            f"Moved to the top left, a line stood at {above}; moved back, the next stood at {below}, "
            "which is not below it and to its right",
        )
        ctx.note("answersMoved", {"topLeft": above, "bottomCenter": below})

        picture = ctx.api.call("GET", "/api/v1/screenshot")
        require(
            picture.status == 200
            and picture.headers.get("content-type") == "image/jpeg"
            and isinstance(picture.body, bytes)
            and picture.body[:2] == b"\xff\xd8",
            f"The glass was not pictured: {picture.status} {describe(picture.body)}",
        )
        (ctx.output / "assistant-screenshot.jpg").write_bytes(picture.body)
        width, height = jpeg_size(picture.body)
        screen = ctx.adb.capture()
        require(
            width == 540 and abs(height - round(540 * screen.height / screen.width)) <= 1,
            f"The picture is {width} by {height} for a screen of {screen.width} by {screen.height}",
        )
        require(
            len(picture.body) >= ASSISTANT_PICTURE_BYTES,
            f"A picture of {len(picture.body)} bytes holds nothing of the dashboard",
        )
        small = ctx.api.call("GET", "/api/v1/screenshot?width=200")
        require(
            small.status == 200 and jpeg_size(small.body)[0] == 200,
            "A picture of another width was not made",
        )
        ctx.note("pictureBytes", len(picture.body))

        try:
            ctx.api.expect("POST", "/api/v1/automation/sleep", {})
            ctx.wait_peak("black", lambda peak: peak <= BLACK_PEAK, "assistant-asleep")
            dark = ctx.api.expect("POST", "/api/v1/assistant/say", {"text": "Nobody sees this"})
            require(dark == {"shown": False, "reason": "sleeping"}, f"A dark glass answered a line with {describe(dark)}")
            expect_refused(ctx.api.call("GET", "/api/v1/screenshot"), 409, "A picture of a dark glass")
            # Someone who asks is answered where they can see it, unless the answer was to stay dark.
            companion.script({"heard": "Good night", "reply": "Good night.", "acted": ["set_power"]})
            ctx.api.expect("POST", "/api/v1/assistant/ask", {"text": "Good night"}, timeout=70)
            time.sleep(2)
            require(asleep(ctx), "An answer that put the Mirror to sleep woke it")
            companion.script({"heard": "Are you there?", "reply": "Here, and listening for whatever comes next."})
            ctx.api.expect("POST", "/api/v1/assistant/ask", {"text": "Are you there?"}, timeout=70)
            wait_for("an answer to wake a dark Mirror", lambda: not asleep(ctx), timeout=10)
            ctx.wait_lit("assistant-awake")
        finally:
            ctx.set_automation(enabled=False)

        companion.script({"status": 500, "error": "The model gave no answer"})
        failed = ctx.api.call("POST", "/api/v1/assistant/ask", {"text": "And now?"}, timeout=70)
        expect_refused(failed, 503, "A request that the companion failed at")
        on_glass(ctx, "answering", "that the assistant gave no answer")

        report = assistant_state(ctx)
        recent = report["recent"]
        counted = {key: report["counts"][key] - before[key] for key in before}
        require(
            counted == {"requests": 4, "ignored": 0, "failures": 1}
            and [entry["reply"] for entry in recent[-4:]] == [reply, "Good night.", "Here, and listening for whatever comes next.", ""]
            and recent[-1]["error"]
            and recent[-4]["heard"] == "What kind of evening is it?",
            f"What was asked is kept as {describe(counted)} {describe(recent, 900)}",
        )

        accepted = companion.key
        companion.key = "another-key-altogether"
        expect_refused(
            ctx.api.call("POST", "/api/v1/assistant/ask", {"text": "Still there?"}, timeout=70),
            503, "A request with a key that the companion does not accept",
        )
        refused = assistant_state(ctx)
        require(
            refused["state"] == "unreachable" and "key" in refused["detail"],
            f"With a key that is not accepted the assistant is {refused['state']}: {refused['detail']}",
        )
        companion.key = accepted
        ctx.api.expect("PUT", "/api/v1/assistant", {"enabled": True})
        wait_for("the companion to be found again", lambda: assistant_state(ctx)["state"] == "connected", timeout=30)

        companion.close()
        gone = ctx.api.call("POST", "/api/v1/assistant/ask", {"text": "Anyone?"}, timeout=70)
        expect_refused(gone, 503, "A request to a companion that is gone")
        lost = assistant_state(ctx)
        require(
            lost["state"] == "unreachable",
            f"With its companion gone the assistant is {lost['state']}: {lost['detail']}",
        )
        require(ctx.health()["assistant"]["state"] == "unreachable", "The health report does not say that the companion is gone")
        ctx.note("gone", lost["detail"])
    require(assistant_state(ctx)["state"] == "off", "The assistant did not switch off")


def check_assistant_voice(ctx: Context) -> None:
    listening_voice(ctx)
    ctx.lit_peak()
    usual = ctx.set_automation(enabled=False)["wakeBrightness"]

    def brightness() -> int:
        return ctx.api.expect("GET", "/api/v1/automation")["wakeBrightness"]

    def requests(companion: fake_companion.FakeCompanion) -> list[dict]:
        return companion.sent("/v1/ask") + companion.sent("/v1/utterance")

    def texts(companion: fake_companion.FakeCompanion) -> list[str]:
        return [json.loads(request["body"].decode("utf-8"))["text"] for request in companion.sent("/v1/ask")]

    with companion_for(ctx) as companion:
        try:
            before = voice_state(ctx)["counts"]
            # The Mirror's own commands stay with the Mirror.
            say(ctx, "mirror brighter")
            wait_for("the wake brightness to rise", lambda: brightness() == usual + VOICE_BRIGHTNESS_STEP, timeout=10)
            say(ctx, "mirror dimmer")
            wait_for("the wake brightness to fall again", lambda: brightness() == usual, timeout=10)
            say(ctx, "[unk] [unk] [unk]")
            time.sleep(1.5)
            require(
                not requests(companion),
                f"The companion was asked about a command of the Mirror's own, or about talk: {texts(companion)}",
            )

            # What is no command goes to the companion; its question opens the next sentence to it.
            companion.script(
                {"heard": "Add milk to the list", "reply": "Which list do you mean?", "listen": True},
                {"heard": "The shopping list", "reply": "Milk is on the shopping list."},
                {"heard": "Show me the list", "reply": "Here is what you still need."},
            )
            say(ctx, "mirror [unk] [unk] [unk] [unk]")
            wait_for("the companion to be asked", lambda: len(requests(companion)) == 1, timeout=10)
            on_glass(ctx, "Which list do you mean?", "the companion's question")
            say(ctx, "[unk] [unk]")
            wait_for("an answer without the name to reach the companion", lambda: len(requests(companion)) == 2, timeout=10)
            on_glass(ctx, "Milk is on the shopping list.", "the companion's answer")
            say(ctx, "[unk] [unk]")
            time.sleep(1.5)
            require(len(requests(companion)) == 2, "Talk after an answer was passed on to the companion")

            # The name, a pause, then the request.
            say(ctx, "mirror")
            say(ctx, "[unk] [unk] [unk]")
            wait_for("a request that follows the name to reach the companion", lambda: len(requests(companion)) == 3, timeout=10)
            require(
                texts(companion) == ["mirror [unk] [unk] [unk] [unk]", "[unk] [unk]", "[unk] [unk] [unk]"]
                and all(json.loads(request["body"].decode("utf-8"))["source"] == "test" for request in companion.sent("/v1/ask")),
                f"The companion was asked {texts(companion)}",
            )

            # A request the recogniser was unsure of having been for the Mirror goes nowhere.
            say(ctx, "mirror [unk] [unk]", 0.5)
            time.sleep(1.5)
            require(len(requests(companion)) == 3, "A sentence that may not have been for the Mirror was passed on")

            # Spoken, the request arrives as the sound of it.
            wait_for(
                "the answer to leave the glass",
                lambda: "Here is what you still need." not in ctx.native_text(),
                timeout=20,
            )
            companion.script({
                "heard": "Mirror, what is the weather like today?",
                "reply": "Mild and dry until the evening.",
                "delaySeconds": 3,
            })
            hear(ctx, ASSISTANT_CLIP)
            sent = wait_for(
                "the sound of a request to reach the companion",
                lambda: companion.sent("/v1/utterance"),
                timeout=VOICE_ACT_SECONDS,
            )
            # While the companion works on it, the glass shows that it does.
            frames = [ctx.adb.capture()]
            (ctx.output / "assistant-thinking.png").write_bytes(screen_capture.to_png(frames[0]))
            on_glass(ctx, "Mild and dry until the evening.", "the answer to what was said")
            request = sent[0]
            headers = request["headers"]
            require(
                headers.get("content-type") == "audio/wav"
                and headers.get("x-mirror-addressed") == "name"
                and len(headers.get("x-mirror-utterance", "")) >= 8
                and headers.get("authorization") == "Bearer " + companion.key,
                f"The sound arrived with {describe({name: value for name, value in headers.items() if name != 'authorization'})}",
            )
            (ctx.output / "assistant-utterance.wav").write_bytes(request["body"])
            with wave.open(io.BytesIO(request["body"])) as sound:
                form = (sound.getframerate(), sound.getnchannels(), sound.getsampwidth())
                heard = sound.readframes(sound.getnframes())
            require(form == (16_000, 1, 2), f"The sound is {form} and not 16 kHz, one channel, 16 bits")
            with wave.open(str(VOICE_CLIPS / ASSISTANT_CLIP)) as clip:
                spoken = clip.readframes(clip.getnframes())
            # The recogniser heard the clip sample for sample, so the request's sound
            # is a stretch of it. All that was said must lie within what was sent.
            lead, tail = ASSISTANT_CLIP_SILENCE
            middle = len(spoken) // 4 * 2
            found = heard.find(spoken[middle:middle + 3200])
            require(found >= 0 and found % 2 == 0, "What was sent is not the sound that was heard")
            begins = (found - middle) // 2
            missing_before = max(0, -(begins + lead)) / 16_000
            missing_after = max(0, (begins + len(spoken) // 2 - tail) - len(heard) // 2) / 16_000
            require(
                missing_before <= 0.05 and missing_after <= 0.05,
                f"The sound that was sent lacks {missing_before:.2f} s of the request's beginning "
                f"and {missing_after:.2f} s of its end",
            )
            require(
                # A second before the first word, and up to a second and a half until the room is quiet.
                len(heard) // 2 <= len(spoken) // 2 - lead - tail + 40_000,
                f"{len(heard) / 32_000:.1f} s were sent for a request of {(len(spoken) // 2 - lead - tail) / 16_000:.1f} s",
            )
            counts = voice_state(ctx)["counts"]
            require(
                counts["asked"] - before["asked"] == 4 and counts["commands"] - before["commands"] == 2,
                f"What was said is counted as {describe({key: counts[key] - before[key] for key in before})}",
            )
            report = assistant_state(ctx)
            require(
                report["counts"]["requests"] >= 4 and report["recent"][-1]["reply"] == "Mild and dry until the evening.",
                f"What was asked is kept as {describe(report['recent'][-1])}",
            )
            ctx.note("sentSeconds", round(len(heard) / 32_000, 2))
            ctx.note("spokenSeconds", round((len(spoken) // 2 - lead - tail) / 16_000, 2))
            ctx.note("answerMillis", report["recent"][-1]["millis"])
        finally:
            ctx.set_automation(enabled=False)


def check_assistant_greets(ctx: Context) -> None:
    listening_voice(ctx)
    ctx.lit_peak()
    ctx.set_automation(enabled=False)

    def asked(companion: fake_companion.FakeCompanion) -> list[dict]:
        return [json.loads(request["body"].decode("utf-8")) for request in companion.sent("/v1/ask")]

    try:
        with companion_for(ctx) as companion:
            before = assistant_state(ctx)["counts"]
            companion.script({
                "heard": "",
                "reply": "Good morning",
                "acted": [],
                "seconds": 12,
                "details": [
                    {"label": "Weather", "text": "Clear now, 31\u00b0 later"},
                    {"label": "Missed", "text": "Start the dishwasher, due at 9:00 PM"},
                ],
            })
            say(ctx, "mirror good morning")
            # The greeting is the Mirror's own and is there at once; what the companion knows follows.
            on_glass(ctx, "Good morning", "the greeting", timeout=VOICE_CAPTION_SECONDS)
            on_glass(ctx, "Start the dishwasher, due at 9:00 PM", "where things stand, under the greeting")
            require(
                asked(companion) == [{"text": "good morning", "source": "shortcut", "shortcut": "good-morning"}]
                and not companion.sent("/v1/utterance"),
                f"For a greeting the companion was asked {describe(asked(companion))}",
            )
            glass = ctx.native_text()
            require(
                glass.count("Good morning") == 1 and "WEATHER" in glass and "MISSED" in glass,
                "The greeting and what the companion knows are not one answer on the glass",
            )
            time.sleep(1)
            ctx.screenshot("assistant-greeting")

            # Told good night, the Mirror says what there is to say and then goes dark.
            companion.script({
                "reply": "Good night",
                "seconds": 5,
                "details": [{"label": "Tomorrow", "text": "Dentist at 9:30 AM"}],
            })
            say(ctx, "mirror good night")
            on_glass(ctx, "Dentist at 9:30 AM", "what tomorrow holds")
            require(not asleep(ctx), "Told good night, the Mirror went dark before it had answered")
            wait_for("the Mirror to sleep once it had answered", lambda: asleep(ctx), timeout=12)
            require(asked(companion)[-1]["shortcut"] == "good-night", f"The companion was asked {describe(asked(companion)[-1])}")

            # Whoever speaks again has not gone to bed.
            say(ctx, "mirror wake up")
            wait_for("the Mirror to wake", lambda: not asleep(ctx), timeout=10)
            companion.script({"reply": "Good night", "seconds": 6, "details": [{"label": "Tomorrow", "text": "Nothing is planned"}]})
            say(ctx, "mirror good night")
            on_glass(ctx, "Nothing is planned", "the answer to good night")
            say(ctx, "mirror brighter")
            time.sleep(8)
            require(not asleep(ctx), "The Mirror went dark although someone spoke to it after good night")
            say(ctx, "mirror dimmer")

            # A companion that does not answer keeps nobody waiting.
            companion.script({"status": 500, "error": "Nothing to say"})
            say(ctx, "mirror good night")
            wait_for("the Mirror to sleep without an answer", lambda: asleep(ctx), timeout=6)

            report = assistant_state(ctx)
            counted = {key: report["counts"][key] - before[key] for key in before}
            sources = [entry["source"] for entry in report["recent"][-4:]]
            require(
                counted == {"requests": 4, "ignored": 0, "failures": 1} and sources == ["shortcut"] * 4,
                f"The greetings are kept as {describe(counted)} from {sources}",
            )
        # Without an assistant a greeting is what it always was: it wakes the Mirror, or darkens it.
        say(ctx, "mirror good morning")
        wait_for("a greeting to wake the Mirror", lambda: not asleep(ctx), timeout=10)
        say(ctx, "mirror good night")
        wait_for("good night to darken a Mirror that has no assistant", lambda: asleep(ctx), timeout=4)
    finally:
        ctx.set_automation(enabled=False)


def check_voice_returns(ctx: Context) -> None:
    state = voice_state(ctx)
    if ctx.voice_model is None:
        raise CheckSkipped(NO_SPEECH_MODEL)
    if ctx.voice_left_on:
        require(
            state["enabled"] and state["model"],
            "Voice was switched on with a speech model before the restarts, and is not after them: "
            f"{state['state']}",
        )
        wait_voice(ctx, "listening", VOICE_LISTENING_SECONDS)
    else:
        listening_voice(ctx, spoken_to=False)
    if state["testHooks"]:
        try:
            hear(ctx, "mirror-go-to-sleep.wav")
            wait_for("the Mirror to sleep when told to", lambda: asleep(ctx), timeout=VOICE_ACT_SECONDS)
        finally:
            ctx.set_automation(enabled=False)
    off = ctx.api.expect("PUT", "/api/v1/voice", {"enabled": False})
    require(off["state"] == "off", f"Switched off, voice is {off['state']}")
    wait_for(
        "the recogniser's process to end once voice is off",
        lambda: not voice_process_running(ctx),
        timeout=20,
    )
    removed = ctx.api.expect("DELETE", "/api/v1/voice/model")
    require(removed["model"] is None, f"The speech model was not removed: {removed['model']}")
    ctx.voice_left_on = False


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
        if cover == STOCK_LAUNCHER:
            # Its process idles under the dashboard now. On a Mirror, whenever
            # the kernel ended that process for want of memory, Mirror Home's
            # ended with it, and both started again, over and over.
            try:
                wait_for(
                    "the other HOME app's idle process to be ended",
                    lambda: not process_running(ctx, cover),
                    timeout=OTHER_HOME_ENDS_SECONDS,
                    interval=1,
                )
            except CheckFailed:
                raise CheckFailed(
                    f"Mirror Home left the idle process of {cover} under its dashboard"
                ) from None
            ended = ctx.health()
            require(
                ended["process"]["runId"] == before["process"]["runId"] and ended["activity"]["showing"],
                "Ending the other HOME app's process disturbed the dashboard: "
                f"run {before['process']['runId']} is now run {ended['process']['runId']}, {ended['activity']}",
            )
            ctx.note("otherHomeEnds", ended["activity"]["recovery"].get("otherHomeEnds"))
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
    ctx.keep_console_errors(before)
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
    ctx.keep_console_errors(before)
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
    logged = ctx.console_errors_kept + dashboard["recentConsoleErrors"]
    ctx.note("scriptErrors", logged)
    require(
        total == 0,
        f"The dashboard logged {total} script errors during the run: {describe(logged, 600)}",
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
    ("note-text", "A note keeps its accents, signs and other scripts", check_note_text, False),
    ("notes", "A note posted from the controls appears on the glass", check_notes, False),
    ("board-api", "A program with only the address can learn the board, post to it and clear it", check_board_api, False),
    ("board-glass", "A board with more than fits turns its pages until everything was shown", check_board_glass, False),
    ("moments", "A countdown, words and a drawing appear where the glass is free, run, and leave when their time is up", check_moments, False),
    ("offline-fallback", "An unreachable web page falls back to the offline clock", check_offline_fallback, False),
    ("control-page", "The control page and everything it loads are served", check_control_page, False),
    ("health", "The health report describes this device and shows no faults", check_health, False),
    ("scan-guard", "Asked to, Android stops scanning for Wi-Fi while connected; asked again, it scans as before", check_scan_guard, False),
    ("updater-held", "Mirror Home keeps the OTA supervisor from being ended first, and the supervisor does not need it to", check_updater_held, False),
    ("voice-off", "Voice commands are off until switched on, and nothing listens", check_voice_off, False),
    ("voice-model", "What is no speech model is refused; one that cannot be loaded harms nothing", check_voice_model, False),
    ("voice-listens", "With the speech model installed and voice on, a process of its own listens", check_voice_listens, False),
    ("voice-commands", "Told by name to sleep and to wake, the Mirror does; talk in the room does nothing", check_voice_commands, False),
    ("voice-wake-word", "A command counts after the Mirror's name, at once or after a pause, and if heard for sure", check_voice_wake_word, False),
    ("voice-recovers", "A recogniser that stops comes back, and the dashboard never notices", check_voice_recovers, False),
    ("voice-steps-aside", "While Android installs an app, voice gives back its memory; then it listens again", check_voice_steps_aside, False),
    ("voice-permission", "Without the microphone permission voice waits, and starts once it is given", check_voice_permission, False),
    ("assistant-off", "The assistant is off until switched on, and takes only a companion it could reach", check_assistant_off, False),
    ("assistant-asks", "A typed request reaches the companion; its answer, its lines and a picture of the glass come back", check_assistant_asks, False),
    ("assistant-voice", "Said to the Mirror, what is no command of its own reaches the companion as sound", check_assistant_voice, False),
    ("assistant-greets", "Greeted, the Mirror shows where things stand; told good night, it answers and then goes dark", check_assistant_greets, False),
    ("returns-to-front", "A screen that covers the dashboard does not stay in front", check_returns_to_front, False),
    ("wakes-display", "A display that Android put to sleep is woken again", check_wakes_display, False),
    ("cold-start", "Starting Home never lights the whole screen", check_cold_start, False),
    ("restart", "A stopped process is recorded and Home comes back paired", check_restart, False),
    ("quick-restart", "A process stopped while starting is counted, not reported as the previous run", check_quick_restart, False),
    ("reboot", "After a reboot the dashboard appears by itself with its settings", check_reboot, True),
    ("voice-returns", "After the restarts voice listens again by itself; switched off, nothing remains", check_voice_returns, False),
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
    ("voice-off", "The updated Home has voice commands, switched off, and nothing listens", check_voice_off, False),
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
    print("Building the debug APKs of Mirror Home and the OTA supervisor", flush=True)
    subprocess.run(
        [
            str(wrapper), "-p", str(REPO),
            ":android:mirror-home:assembleDebug", ":android:ota-updater:assembleDebug", "--no-daemon", "-q",
        ],
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


def speech_model(named: pathlib.Path | None) -> pathlib.Path | None:
    """The speech model the voice checks install, or None to skip those that need it.

    A model that was asked for must be there. Otherwise the one that
    ``tools/voice.py fetch-model`` keeps is used if this computer has it.
    """
    if named is not None:
        if not named.is_file():
            raise CheckFailed(f"Speech model not found: {named}")
        return named
    if not voice.DEFAULT_MODEL.is_file():
        print(f"Voice checks that need the speech model will be skipped: {NO_SPEECH_MODEL}", flush=True)
        return None
    if voice.sha256_file(voice.DEFAULT_MODEL) != voice.MODEL_SHA256:
        raise CheckFailed(
            f"{voice.DEFAULT_MODEL} is not the speech model this suite knows; "
            "delete it and run: python tools/voice.py fetch-model"
        )
    return voice.DEFAULT_MODEL


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
    voice_model = speech_model(options.voice_model)
    if not options.apk and not options.skip_build:
        build_debug_apk()
    if not apk.is_file():
        raise CheckFailed(f"APK not found: {apk}")
    # Only a supervisor signed like Mirror Home lets itself be held: the debug one goes with
    # the debug build, and another build has to bring its own.
    supervisor_apk = options.supervisor_apk or (None if options.apk else DEBUG_SUPERVISOR_APK)
    if options.supervisor_apk and not supervisor_apk.is_file():
        raise CheckFailed(f"APK not found: {supervisor_apk}")
    if supervisor_apk is not None and not supervisor_apk.is_file():
        supervisor_apk = None

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
        context.supervisor_apk = supervisor_apk
        context.voice_model = voice_model
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
            "supervisorApk": str(supervisor_apk) if supervisor_apk else None,
            "voiceModel": str(voice_model) if voice_model else None,
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
    if memory.get("kernel"):
        ctx.note("kernelMemory", memory["kernel"])
        ctx.note("largestProcesses", memory.get("largest"))
    ctx.note("display", health["device"]["display"])
    ctx.note("power", health["device"].get("power"))
    ctx.note("front", activity.get("front"))
    ctx.note("recovery", activity.get("recovery"))
    ctx.note("webView", health["device"]["webView"])
    ctx.note("clockSource", health["clock"]["source"])
    ctx.note("nextClockChange", health["clock"]["nextChange"])
    ctx.note("pairing", health["pairing"])
    ctx.note("otaSupervisor", supervisor)
    guard = (health.get("wifi") or {}).get("scanGuard")
    hold = supervisor.get("hold")
    if guard is not None:
        ctx.note("scanGuard", guard)
    problems = []
    # Without a network the guard stands aside, so that Android finds one as it came.
    standing_aside = guard and guard.get("state") == "waiting" and not (health.get("wifi") or {}).get("connected")
    if guard and guard.get("enabled") and guard.get("state") != "applied" and not standing_aside:
        problems.append(
            "the scan guard is on, but Android still scans for Wi-Fi while connected"
            + (f": {guard['detail']}" if guard.get("detail") else "")
        )
    if guard and guard.get("state") == "applied" and (guard.get("scans") or {}).get("sinceApplied", 0) > SCANS_DESPITE_GUARD:
        problems.append(
            f"the scan guard is applied, yet {guard['scans']['sinceApplied']} scans have arrived while "
            "connected since"
        )
    if hold and hold.get("state") == "refused":
        problems.append("the OTA supervisor is signed with another key than Mirror Home, which cannot hold it")
    if hold and hold.get("state") == "held" and (hold.get("oomScoreAdj") or 0) > HELD_SCORE \
            and activity["showing"]:
        problems.append(
            f"Mirror Home holds the OTA supervisor, yet the kernel ranks it at {hold['oomScoreAdj']}, "
            f"not at {HELD_SCORE} or under"
        )
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
    if (health.get("restart") or {}).get("advised"):
        problems.append(f"the Mirror asks to be switched off and on: {health['restart']['reason']}")
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


def exercise_board(ctx: MirrorContext) -> None:
    require_health_report(ctx)
    if "boardVersion" not in ctx.api.expect("GET", "/api/v1/status"):
        raise CheckSkipped("this Mirror Home has no board; it came after 2.2.0")
    before = ctx.api.expect("GET", BOARD)
    version = before["version"]
    path = f"{BOARD_ITEMS}/validation-{int(time.time())}"
    # Two minutes to live, so that an exercise that is cut short still leaves nothing behind.
    item = {"kind": "todo", "title": EXERCISE_NOTE, "ttlSeconds": 120, "source": EXERCISE_CLIENT}
    created = ctx.api.call("PUT", path, item)
    if created.status == 409:
        raise CheckSkipped("the board already holds as many items as it can")
    require(created.status == 201, f"Posting to the board answered {created.status}: {describe(created.body)}")
    try:
        require(
            ctx.api.expect("GET", "/api/v1/status")["boardVersion"] != version,
            "Posting to the board did not change boardVersion, so the glass would not show it",
        )
        done = ctx.api.expect("PATCH", path, {"done": True})["item"]
        require(done["state"] == "done", f"Marking the item done left it {done['state']}")
        listed = ctx.api.expect("GET", BOARD)["items"]
        require(
            any(entry["id"] == done["id"] and entry["title"] == EXERCISE_NOTE for entry in listed),
            "The posted item is not among those the glass lists",
        )
    finally:
        ctx.api.expect("DELETE", path)
    require(ctx.api.call("GET", path).status == 404, "The removed item can still be read")
    after = ctx.api.expect("GET", BOARD)
    require(
        after["counts"]["total"] == before["counts"]["total"],
        "The number of items on the board is not what it was",
    )
    ctx.note("boardOnGlass", bool(before["glass"]["showsBoard"]))
    ctx.note("boardItems", after["counts"]["total"])


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
    ("board", "A program's item can be posted to the board, marked done and removed", exercise_board, False),
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
        "--supervisor-apk",
        type=pathlib.Path,
        help="OTA supervisor signed with the same key as --apk, for the check that Mirror Home holds it "
        "(default: the debug one, with the debug build)",
    )
    emulator.add_argument(
        "--upgrade-from",
        type=pathlib.Path,
        help="rehearse an update: install this earlier APK, set it up, then install the build under test over it",
    )
    emulator.add_argument(
        "--voice-model",
        type=pathlib.Path,
        help="speech model archive for the voice checks (default: the one tools/voice.py fetch-model keeps, if present)",
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
