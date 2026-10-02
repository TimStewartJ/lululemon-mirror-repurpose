#!/usr/bin/env python3
"""Voice commands on a paired Mirror: the speech model, the switch, and what it has heard.

    python tools/voice.py status           what voice is doing on the Mirror
    python tools/voice.py install-model    download the speech model and give it to the Mirror
    python tools/voice.py on               switch voice commands on
    python tools/voice.py off              switch them off; nothing listens then
    python tools/voice.py remove-model     take the speech model off the Mirror again
    python tools/voice.py fetch-model      only download the model to this computer

Mirror Home recognises speech on the Mirror itself. The recogniser's speech
model is larger than an update package may be, so it travels by itself: this
tool downloads it once from its makers, checks it against the checksum kept
here, and sends it to the Mirror over the home network.

The Mirror is the one paired with ``tools/background-video.ps1 pair``.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import http.client
import json
import os
import pathlib
import sys
import time
import urllib.request
from typing import Callable

import background_video


REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = background_video.DEFAULT_CONFIG_PATH
DEFAULT_PORT = background_video.DEFAULT_PORT
# Vosk's small model for US English, Apache 2.0: the one the command list was tried with.
MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODEL_SHA256 = "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"
MODEL_BYTES = 41_205_931
DEFAULT_MODEL = REPO / "build" / "voice" / f"{MODEL_NAME}.zip"
# Mirror Home takes no larger archive (VoiceModelArchive.MAX_ARCHIVE_BYTES).
MAX_MODEL_BYTES = 96 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
JSON_TIMEOUT_SECONDS = 30
# Per socket operation. The Mirror unpacks and checks the model before it answers.
UPLOAD_TIMEOUT_SECONDS = 300
DOWNLOAD_TIMEOUT_SECONDS = 60
# A Mirror loads the model in about four seconds; allow for one that is busy.
SETTLE_SECONDS = 90
UNSETTLED_STATES = ("starting", "loading")
PACKAGE = "dev.mirror.repurpose"


class VoiceError(RuntimeError):
    pass


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_model(
    target: pathlib.Path = DEFAULT_MODEL,
    *,
    opener: Callable = urllib.request.urlopen,
    report: Callable[[str], None] = lambda line: None,
) -> pathlib.Path:
    """The speech model as a file on this computer, downloaded unless it is there already.

    Nothing is kept that does not have the checksum this tool knows.
    """
    if target.is_file():
        if sha256_file(target) == MODEL_SHA256:
            return target
        report(f"{target} is not the speech model this tool knows; downloading it again")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    received = 0
    report(f"Downloading {MODEL_URL} ({MODEL_BYTES // (1024 * 1024)} MB)")
    try:
        with opener(MODEL_URL, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, partial.open("wb") as output:
            while True:
                chunk = response.read(CHUNK_BYTES)
                if not chunk:
                    break
                received += len(chunk)
                if received > MODEL_BYTES:
                    break
                digest.update(chunk)
                output.write(chunk)
    except (OSError, http.client.HTTPException) as error:
        partial.unlink(missing_ok=True)
        raise VoiceError(f"Unable to download {MODEL_URL}: {error}") from error
    if received != MODEL_BYTES or digest.hexdigest() != MODEL_SHA256:
        partial.unlink(missing_ok=True)
        raise VoiceError(
            f"What {MODEL_URL} sent is not the speech model this tool knows "
            f"({received} bytes, SHA-256 {digest.hexdigest()}); nothing was kept"
        )
    os.replace(partial, target)
    return target


def upload_progress(percent: int, sent: int, total: int) -> None:
    sys.stderr.write(f"\rSending the speech model: {percent:3d}% ({sent // (1024 * 1024)}/{total // (1024 * 1024)} MB)")
    if percent >= 100:
        sys.stderr.write("\nThe Mirror is unpacking it\n")
    sys.stderr.flush()


class VoiceClient:
    """The voice part of a paired Mirror's control API."""

    def __init__(
        self,
        host: str,
        token: str,
        port: int = DEFAULT_PORT,
        *,
        connect: Callable = http.client.HTTPConnection,
    ):
        try:
            self.host = str(background_video.validate_host(host))
        except background_video.BackgroundVideoClientError as error:
            raise VoiceError(str(error)) from error
        if not token:
            raise VoiceError("The pairing has no token; pair again with tools/background-video.ps1 pair")
        self.port = port
        self.token = token
        self.connect = connect

    def _answer(self, response, data: bytes) -> dict:
        text = data.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text else {}
        except ValueError:
            parsed = {}
        if response.status == 404:
            raise VoiceError(
                "This Mirror Home has no voice commands; they came with version 2.3.0"
            )
        if response.status >= 400:
            reason = parsed.get("error") if isinstance(parsed, dict) else None
            raise VoiceError(f"The Mirror refused ({response.status}): {reason or text or response.reason}")
        if not isinstance(parsed, dict):
            raise VoiceError("The Mirror answered with something unexpected")
        return parsed

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.token}"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection = self.connect(self.host, self.port, timeout=JSON_TIMEOUT_SECONDS)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
        except (http.client.HTTPException, OSError) as error:
            raise VoiceError(f"Unable to reach the Mirror at {self.host}:{self.port}: {error}") from error
        finally:
            connection.close()
        return self._answer(response, data)

    def state(self) -> dict:
        return self._call("GET", "/api/v1/voice")

    def set_enabled(self, enabled: bool) -> dict:
        return self._call("PUT", "/api/v1/voice", {"enabled": enabled})

    def remove_model(self) -> dict:
        return self._call("DELETE", "/api/v1/voice/model")

    def install_model(self, path: pathlib.Path, *, progress: Callable | None = None) -> dict:
        if not path.is_file():
            raise VoiceError(f"No such file: {path}")
        size = path.stat().st_size
        if size < 1 or size > MAX_MODEL_BYTES:
            raise VoiceError(
                f"{path} is {size} bytes; a Mirror takes a model archive of at most "
                f"{MAX_MODEL_BYTES // (1024 * 1024)} MB"
            )
        checksum = sha256_file(path)
        connection = self.connect(self.host, self.port, timeout=UPLOAD_TIMEOUT_SECONDS)
        try:
            connection.putrequest("PUT", "/api/v1/voice/model")
            for name, value in (
                ("Accept", "application/json"),
                ("Authorization", f"Bearer {self.token}"),
                ("Content-Type", "application/zip"),
                ("Content-Length", str(size)),
                ("X-Content-SHA256", checksum),
            ):
                connection.putheader(name, value)
            connection.endheaders()
            sent = 0
            shown = -1
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
                    connection.send(chunk)
                    sent += len(chunk)
                    percent = sent * 100 // size
                    if progress is not None and percent != shown:
                        progress(percent, sent, size)
                        shown = percent
            response = connection.getresponse()
            data = response.read()
        except (http.client.HTTPException, OSError) as error:
            raise VoiceError(f"Sending the speech model to {self.host}:{self.port} failed: {error}") from error
        finally:
            connection.close()
        return self._answer(response, data)


def settle(
    client: VoiceClient,
    state: dict,
    *,
    timeout: float = SETTLE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Wait while the recogniser starts, so that what is reported is where it ended up."""
    deadline = clock() + timeout
    while state.get("state") in UNSETTLED_STATES and clock() < deadline:
        sleep(1.0)
        state = client.state()
    return state


def megabytes(count: object) -> str:
    return f"{int(count) / (1024 * 1024):.0f} MB" if isinstance(count, (int, float)) else "unknown size"


def local_time(epoch_ms: object) -> str:
    if not isinstance(epoch_ms, (int, float)):
        return "unknown time"
    return datetime.datetime.fromtimestamp(epoch_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def describe(state: dict) -> list[str]:
    """What voice is doing, in lines for a person; and what to do if it is not listening."""
    lines = [f"Voice commands: {'on' if state.get('enabled') else 'off'} - {state.get('detail') or state.get('state')}"]
    model = state.get("model")
    if model:
        lines.append(
            f"Speech model:   {model.get('name')} ({megabytes(model.get('bytes'))}), "
            f"installed {local_time(model.get('installedAt'))}"
        )
    else:
        lines.append("Speech model:   none. Install it with: python tools/voice.py install-model")
    if state.get("permissionGranted"):
        microphone = state.get("microphone") or {}
        if microphone.get("levelDb") is None:
            lines.append("Microphone:     allowed")
        elif microphone.get("silent"):
            lines.append("Microphone:     allowed, but it delivers only silence")
        else:
            lines.append(
                f"Microphone:     allowed; level {microphone['levelDb']} dBFS, peak {microphone.get('peakDb')} dBFS"
            )
    else:
        lines.append("Microphone:     not allowed. Allow it with one of:")
        lines.append("                  python tools/otactl.py grant-permission microphone --confirm")
        lines.append(f"                  adb shell pm grant {PACKAGE} android.permission.RECORD_AUDIO")
    process = state.get("process") or {}
    recogniser = state.get("recogniser") or {}
    if process.get("pid"):
        parts = [f"process {process['pid']}"]
        if process.get("pssKb") is not None:
            parts.append(f"{process['pssKb'] // 1024} MB")
        if recogniser.get("cpuShare") is not None:
            parts.append(f"{round(recogniser['cpuShare'] * 100)} % of one core")
        if recogniser.get("modelLoadMs"):
            parts.append(f"model loaded in {recogniser['modelLoadMs'] / 1000:.1f} s")
        if recogniser.get("behindMs"):
            parts.append(f"up to {recogniser['behindMs']} ms behind")
        lines.append("Recogniser:     " + ", ".join(parts))
    if process.get("restarts"):
        lines.append(f"                restarted {process['restarts']} time(s) since Mirror Home started")
    counts = state.get("counts") or {}
    if counts:
        lines.append(
            f"Heard:          {counts.get('commands', 0)} command(s), "
            f"{counts.get('wakeWords', 0)} time(s) its name alone, "
            f"{counts.get('notUnderstood', 0)} not understood, {counts.get('unsure', 0)} too unsure to act on; "
            f"{counts.get('sentences', 0)} stretch(es) of speech in all"
        )
    last = state.get("lastCommand")
    if last:
        lines.append(f"Last command:   {last.get('id')} at {local_time(last.get('at'))} (showed \"{last.get('shown')}\")")
    recent = state.get("recent") or []
    if recent:
        lines.append("Said to it lately:")
        for item in recent[-8:]:
            outcome = item.get("outcome")
            if item.get("command"):
                outcome = f"{outcome} {item['command']}"
            if item.get("shown"):
                outcome = f"{outcome}, showed \"{item['shown']}\""
            lines.append(
                f"                {local_time(item.get('at'))}  \"{item.get('heard')}\"  ->  {outcome} "
                f"(confidence {item.get('confidence')})"
            )
    say = [command["say"][0] for command in state.get("commands") or [] if command.get("say")]
    if say:
        lines.append("Say, for example: " + "; ".join(f'"{sentence}"' for sentence in say))
    if state.get("enabled") is False and model:
        lines.append("Switch it on with: python tools/voice.py on")
    return lines


def client_from_config(path: pathlib.Path, host: str | None, port: int | None) -> VoiceClient:
    try:
        config = background_video.load_config(path)
    except background_video.BackgroundVideoClientError as error:
        raise VoiceError(f"{error}. Pair first with tools/background-video.ps1 pair") from error
    return VoiceClient(
        host or config.get("host") or "",
        config.get("token") or "",
        port if port is not None else int(config.get("port", DEFAULT_PORT)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice.py", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"credential file of the pairing (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument("--host", help="the Mirror's address, if not the paired one")
    parser.add_argument("--port", type=int, help=f"the Mirror's port (default: {DEFAULT_PORT})")
    parser.add_argument("--json", action="store_true", help="print what the Mirror reports as JSON")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="what voice is doing on the Mirror")
    install = commands.add_parser("install-model", help="download the speech model and give it to the Mirror")
    install.add_argument(
        "--file",
        type=pathlib.Path,
        help="send this Vosk model archive instead of downloading the usual one",
    )
    commands.add_parser("on", help="switch voice commands on")
    commands.add_parser("off", help="switch voice commands off; nothing listens then")
    commands.add_parser("remove-model", help="take the speech model off the Mirror")
    fetch = commands.add_parser("fetch-model", help="only download the model to this computer")
    fetch.add_argument("--file", type=pathlib.Path, default=DEFAULT_MODEL, help="where to keep it")
    return parser


def main(argv: list[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    try:
        if options.command == "fetch-model":
            print(fetch_model(options.file, report=print))
            return 0
        client = client_from_config(options.config, options.host, options.port)
        if options.command == "status":
            state = client.state()
        elif options.command == "install-model":
            archive = options.file or fetch_model(report=print)
            state = settle(client, client.install_model(archive, progress=upload_progress))
        elif options.command == "on":
            state = settle(client, client.set_enabled(True))
        elif options.command == "off":
            state = client.set_enabled(False)
        elif options.command == "remove-model":
            state = client.remove_model()
        else:
            raise AssertionError(f"Unhandled command: {options.command}")
    except VoiceError as error:
        print(f"voice: {error}", file=sys.stderr)
        return 1
    print(json.dumps(state, indent=2) if options.json else "\n".join(describe(state)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
