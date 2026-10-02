#!/usr/bin/env python3
"""Drive the Mirror Voice Lab app over adb.

The lab app (android/voice-lab) answers two questions on a real Mirror: how
well its microphone hears, and whether speech can be recognised on the device
itself. A Mirror has no screen to touch, so every experiment is started from
here and its result fetched as JSON. See docs/voice-lab.md.

    python tools/voice_lab.py --serial SERIAL setup --model MODEL_FOLDER --commands commands.txt
    python tools/voice_lab.py --serial SERIAL info
    python tools/voice_lab.py --serial SERIAL record --seconds 6 --name near
    python tools/voice_lab.py --serial SERIAL selftest --clips CLIP_FOLDER --commands commands.txt
    python tools/voice_lab.py --serial SERIAL decode --commands commands.txt
    python tools/voice_lab.py --serial SERIAL pair
    python tools/voice_lab.py --serial SERIAL listen --commands commands.txt --seconds 60 --act
    python tools/voice_lab.py --serial SERIAL remove
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

import android_emulator


REPO = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "dev.mirror.repurpose.voicelab"
SERVICE = f"{PACKAGE}/.LabService"
DEVICE_FOLDER = f"/sdcard/Android/data/{PACKAGE}/files"
APK = REPO / "android" / "voice-lab" / "build" / "outputs" / "apk" / "debug" / "voice-lab-debug.apk"
DEFAULT_OUTPUT = REPO / "build" / "voice-lab"
DEFAULT_MIRROR_CONFIG = REPO / ".secrets" / "mirror-background-video.json"
# The name the lab is paired under, so that an owner recognises and can revoke it.
PAIRED_NAME = "Voice lab (temporary)"
# Media volume step, of 15, for test sounds unless another is asked for. A
# Mirror's speakers are loud in a room: step 9 was too loud for its owner.
QUIET_VOLUME = 4
# Extras travel through a shell on the device; keep them to plain words.
SAFE_VALUE = re.compile(r"[A-Za-z0-9_./:-]+")


class LabError(RuntimeError):
    pass


def find_adb() -> str:
    found = shutil.which("adb")
    if found:
        return found
    try:
        return str(android_emulator.adb_path(android_emulator.sdk_root()))
    except android_emulator.EmulatorError as error:
        raise LabError(str(error)) from None


class Lab:
    def __init__(self, serial: str, adb: str | None = None):
        self.serial = serial
        self.adb = adb or find_adb()

    def run(self, *arguments: str, timeout: float = 300.0, check: bool = True) -> bytes:
        completed = subprocess.run(
            [self.adb, "-s", self.serial, *arguments], capture_output=True, timeout=timeout
        )
        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).decode("utf-8", "replace").strip()
            raise LabError(f"adb {' '.join(arguments)} failed: {detail}")
        return completed.stdout

    def shell(self, command: str, **options) -> str:
        return self.run("shell", command, **options).decode("utf-8", "replace")

    def install(self, apk: pathlib.Path) -> None:
        """Install the lab app with its microphone permission granted."""
        output = self.run("install", "-r", "-g", str(apk)).decode("utf-8", "replace")
        if "Success" not in output:
            raise LabError(f"The lab app did not install: {output.strip()}")
        self.shell(f"mkdir -p {DEVICE_FOLDER}/results {DEVICE_FOLDER}/clips")

    def remove(self) -> None:
        self.run("uninstall", PACKAGE, check=False)
        # Its recordings are speech from the room; do not leave them behind.
        self.shell(f"rm -rf /sdcard/Android/data/{PACKAGE}", check=False)

    def push(self, source: pathlib.Path, name: str) -> None:
        """Copy a file or folder into the lab's folder under ``name``."""
        target = f"{DEVICE_FOLDER}/{name}"
        self.shell(f"rm -rf {target}", check=False)
        self.run("push", str(source), target, timeout=900)

    def pull(self, name: str, target: pathlib.Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        self.run("pull", f"{DEVICE_FOLDER}/{name}", str(target), timeout=900)

    def experiment(
        self,
        command: str,
        *,
        name: str | None = None,
        timeout: float = 120.0,
        keep: pathlib.Path | None = None,
        **extras,
    ) -> dict:
        """Start one experiment and wait for its result, keeping a copy under ``keep``."""
        name = name or f"{command}-{int(time.time())}"
        values = {"cmd": command, "out": name}
        values.update({key: str(value) for key, value in extras.items() if value is not None})
        for key, value in values.items():
            if not SAFE_VALUE.fullmatch(value):
                raise LabError(f"{key}={value!r} cannot be passed to the device")
        result_path = f"{DEVICE_FOLDER}/results/{name}.json"
        self.shell(f"rm -f {result_path}", check=False)
        extras_text = " ".join(f"--es {key} {value}" for key, value in values.items())
        started = self.shell(f"am startservice -n {SERVICE} {extras_text}")
        if "Error" in started:
            raise LabError(f"The lab app did not start: {started.strip()}")
        deadline = time.monotonic() + timeout
        missing = 0
        while time.monotonic() < deadline:
            if self.shell(f"ls {result_path} 2>/dev/null", check=False).strip():
                raw = self.run("exec-out", "cat", result_path)
                if keep is not None:
                    keep.mkdir(parents=True, exist_ok=True)
                    (keep / f"{name}.json").write_bytes(raw)
                result = json.loads(raw.decode("utf-8"))
                if not result.get("ok"):
                    raise LabError(
                        f"{command} failed on the device: {result.get('error') or self.last_errors()}"
                    )
                return result
            # A crash in the recogniser's own code ends the app without a result.
            missing = 0 if self.running() else missing + 1
            if missing >= 6:
                raise LabError(f"the lab app stopped during {command}:\n{self.last_errors()}")
            time.sleep(0.5)
        raise LabError(f"{command} gave no result within {int(timeout)} s:\n{self.last_errors()}")

    def running(self) -> bool:
        return PACKAGE in self.shell("ps", check=False)

    def last_errors(self) -> str:
        """What Android logged about the lab app's failure."""
        log = self.run("logcat", "-d", "-v", "brief", "-t", "400", check=False).decode("utf-8", "replace")
        lines = [
            line for line in log.splitlines()
            if line.startswith(("E/AndroidRuntime", "F/libc", "F/DEBUG", "E/VoiceLab", "I/VoiceLab", "I/DEBUG"))
        ]
        return "\n".join(lines[-25:]) or "Android logged nothing about it"

    def stop(self) -> None:
        self.shell(f"am startservice -n {SERVICE} --es cmd stop", check=False)

    def memory_kb(self) -> int | None:
        """The lab process's memory as Android accounts for it, in kB."""
        found = re.search(r"TOTAL\s+(\d+)", self.shell(f"dumpsys meminfo {PACKAGE}", check=False))
        return int(found.group(1)) if found else None

    def addresses(self) -> list[str]:
        """The device's own IPv4 addresses, asked for in the ways an old Android answers."""
        listed = self.shell("ip -o -4 addr; ifconfig wlan0; getprop dhcp.wlan0.ipaddress", check=False)
        return re.findall(r"(?:inet (?:addr:)?|: ip |^)(\d+\.\d+\.\d+\.\d+)", listed, re.MULTILINE)


def mirror_api(config_path: pathlib.Path):
    """Mirror Home's API as the owner's saved credential reaches it, and its address."""
    import validate

    try:
        config = validate.load_mirror_config(config_path)
    except validate.CheckFailed as error:
        raise LabError(str(error)) from None
    return validate.Api(config["host"], int(config.get("port", validate.DEVICE_PORT)), config["token"]), config["host"]


def pair(lab: Lab, config_path: pathlib.Path, output: pathlib.Path) -> str:
    """Pair the lab with Mirror Home as a device of its own and hand it the credential."""
    import validate

    api, host = mirror_api(config_path)
    if host not in lab.addresses():
        raise LabError(
            f"{lab.serial} is not the Mirror at {host} that {config_path.name} names; "
            "the lab is only paired with the Mirror it runs on"
        )
    record = output / "pairing.json"
    if record.is_file():
        raise LabError(f"The lab is already paired ({record}); run remove first")
    try:
        window = api.expect("POST", "/api/v1/pair/window", {})
        paired = api.expect("POST", "/api/v1/pair", {"code": window["code"], "name": PAIRED_NAME}, token=None)
    except (validate.CheckFailed, OSError) as error:
        raise LabError(f"Mirror Home did not pair the lab: {error}") from None
    output.mkdir(parents=True, exist_ok=True)
    # Written first: a credential that reached the Mirror can then always be revoked.
    record.write_text(json.dumps({"clientId": paired["clientId"], "host": host}), encoding="utf-8")
    with tempfile.TemporaryDirectory() as directory:
        token = pathlib.Path(directory) / "token.txt"
        token.write_text(paired["token"], encoding="ascii")
        lab.push(token, "token.txt")
    return paired["clientId"]


def unpair(config_path: pathlib.Path, output: pathlib.Path) -> bool:
    """Revoke the lab's pairing, if it has one; True when one was revoked."""
    import validate

    record = output / "pairing.json"
    if not record.is_file():
        return False
    client = json.loads(record.read_text(encoding="utf-8"))["clientId"]
    api, _ = mirror_api(config_path)
    try:
        api.expect("POST", "/api/v1/clients/revoke", {"id": client})
    except (validate.CheckFailed, OSError) as error:
        raise LabError(
            f"The lab's pairing could not be revoked ({error}); remove \"{PAIRED_NAME}\" "
            "under Settings > Paired devices in the Mirror controls"
        ) from None
    record.unlink()
    return True


def mirror_state(lab: Lab, config_path: pathlib.Path):
    """Mirror Home's API and what spoken commands may change, so that it can be put back."""
    import validate

    api, host = mirror_api(config_path)
    if host not in lab.addresses():
        raise LabError(
            f"{lab.serial} is not the Mirror at {host} that {config_path.name} names; "
            "--act is for the Mirror the lab runs on"
        )
    try:
        automation = api.expect("GET", "/api/v1/automation")
        brightness = api.expect("GET", "/api/v1/status").get("brightness")
    except (validate.CheckFailed, OSError) as error:
        raise LabError(f"Mirror Home's settings could not be read: {error}") from None
    return api, {
        "settings": {key: automation[key] for key in validate.AUTOMATION_SETTINGS},
        "manualOverride": bool(automation.get("manualOverride")),
        "sleeping": bool(automation["sleeping"]),
        "brightness": brightness,
    }


def restore_mirror(api, state: dict) -> None:
    """Undo what spoken commands left: a manual sleep or wake, and the brightness."""
    import validate

    try:
        # Saving the schedule ends the manual override that a sleep or wake command leaves.
        api.expect("PUT", "/api/v1/automation", state["settings"])
        if state["manualOverride"]:
            api.expect("POST", f"/api/v1/automation/{'sleep' if state['sleeping'] else 'wake'}", {})
        elif not state["sleeping"] and state["brightness"]:
            api.expect("POST", "/api/v1/control/brightness", {"value": state["brightness"]})
    except (validate.CheckFailed, OSError) as error:
        raise LabError(
            f"Mirror Home could not be put back as it was ({error}); in the Mirror controls, "
            "save the sleep schedule again and set the brightness"
        ) from None


def selftest(lab: Lab, options: argparse.Namespace, results: pathlib.Path) -> str:
    """Have the device say each clip through its speakers and recognise what its microphone heard."""
    clips = sorted(options.clips.glob("*.wav"))
    if not clips:
        raise LabError(f"No .wav files in {options.clips}")
    lab.push(options.clips, "clips/selftest")
    lab.shell(f"rm -f {DEVICE_FOLDER}/recordings/self-*.wav", check=False)
    lines = []
    for clip in clips:
        name = f"self-{clip.stem}"
        result = lab.experiment(
            "record", name=name, keep=results, timeout=120, source=options.source,
            play=f"clips/selftest/{clip.name}", volume=options.volume,
        )
        lab.pull(result["file"], options.output / "recordings" / f"{name}.wav")
        lines.append(f"  {clip.name}, media volume {result['mediaVolume']}:\n{summarize_recording(result)}")
    decoded = lab.experiment(
        "decode", name="selftest", keep=results, timeout=1800, clips="recordings", only="self-",
        commands=options.commands, confidence=options.confidence,
    )
    return "\n".join(lines) + "\n" + summarize_decode(decoded)


def summarize_info(info: dict) -> str:
    opened = {}
    for item in info["configurations"]:
        if item["opens"]:
            opened.setdefault(item["source"], []).append(
                f"{item['rate'] // 1000} kHz {'stereo' if item['channels'] == 2 else 'mono'}"
            )
    lines = [
        f"  {info['fingerprint']}",
        f"  Android API {info['sdk']}, {', '.join(info['abis'])}, {info['processors']} processors"
        + (f" up to {int(info['cpuMaxKhz']) // 1000} MHz" if info["cpuMaxKhz"] else ""),
        f"  memory {info['memoryAvailableMb']} MB free of {info['memoryTotalMb']} MB; "
        f"Android calls it low below {info['memoryLowThresholdMb']} MB",
        f"  microphone declared: {info['microphoneFeature']}; permission granted: {info['mayRecord']}",
        "  inputs: " + ("; ".join(
            f"{item['name']} (type {item['type']}, channels {item['channelCounts']}, rates {item['sampleRates']})"
            for item in info["inputDevices"]) or "none listed"),
        "  the device's own processing: " + (", ".join(
            name for name in ("echoCanceler", "noiseSuppressor", "gainControl") if info[name]) or "none"),
        f"  built-in speech recognition: {', '.join(info['recognitionServices']) or 'none'}",
        f"  speech engines for replies: {', '.join(info['speechEngines']) or 'none'}",
        f"  media volume {info['mediaVolume']} of {info['mediaVolumeMax']}",
    ]
    for source, formats in opened.items():
        lines.append(f"  {source} opens as: {', '.join(formats)}")
    if not opened:
        lines.append("  no microphone configuration could be opened")
    return "\n".join(lines)


def summarize_recording(result: dict) -> str:
    lines = []
    for index, stats in enumerate(result["stats"]):
        lines.append(
            f"  channel {index + 1}: peak {stats['peakDb']} dB, room {stats['noiseFloorDb']} dB, "
            f"speech {stats['speechAboveNoiseDb']} dB above the room, "
            f"{stats['clippedSamples']} clipped"
            + (", SILENT" if stats["silent"] else "")
        )
    if "channelDifferenceDb" in result:
        lines.append(f"  difference between the channels: {result['channelDifferenceDb']} dB")
    return "\n".join(lines)


def summarize_decode(result: dict) -> str:
    lines = [
        f"  model loaded in {result['modelLoadMs']} ms; memory {result['memoryBefore']['pssKb'] // 1024} MB "
        f"-> {result['memoryLoaded']['pssKb'] // 1024} MB",
        f"  {result['audioSeconds']} s of sound recognised in {result['wallMs'] / 1000:.1f} s "
        f"(real-time factor {result['realTimeFactor']})",
    ]
    for clip in result["clips"]:
        heard = "; ".join(
            f"{item['text']!r}"
            + (f" conf {item['lowestConfidence']}" if item["lowestConfidence"] is not None else "")
            + (" -> command" if item["command"] else "")
            for item in clip["heard"]
        ) or "(nothing)"
        lines.append(f"  {clip['clip']:<34} {heard}")
    return "\n".join(lines)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serial", required=True, help="adb serial of the device, as listed by adb devices")
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="install the lab app and copy a speech model to it")
    setup.add_argument("--apk", type=pathlib.Path, default=APK)
    setup.add_argument("--model", type=pathlib.Path, help="unpacked Vosk model folder")
    setup.add_argument("--clips", type=pathlib.Path, help="folder of .wav clips to recognise")
    setup.add_argument("--commands", type=pathlib.Path, help="command list, see docs/voice-lab.md")

    commands.add_parser("info", help="what the device offers for hearing and speaking")

    record = commands.add_parser("record", help="record the microphone and fetch the recording")
    record.add_argument("--name", required=True)
    record.add_argument("--seconds", type=int, default=6)
    record.add_argument("--source", default="MIC")
    record.add_argument("--rate", type=int, default=16000)
    record.add_argument("--channels", type=int, default=1, choices=(1, 2))
    record.add_argument("--effects", action="store_true", help="turn on the device's own noise and echo processing")
    record.add_argument("--tone", type=int, default=0, help="play a tone of this many Hz on the speakers meanwhile")
    record.add_argument("--play", help="a clip on the device to play on the speakers meanwhile")
    record.add_argument(
        "--volume", type=int, default=QUIET_VOLUME, help=f"media volume step to play at (default {QUIET_VOLUME})"
    )

    self_test = commands.add_parser(
        "selftest", help="play clips on the device's speakers and recognise what its microphone heard"
    )
    self_test.add_argument("--clips", type=pathlib.Path, required=True, help="folder of .wav clips to play")
    self_test.add_argument("--commands", help="name of the command list on the device")
    self_test.add_argument("--source", default="VOICE_RECOGNITION")
    self_test.add_argument(
        "--volume", type=int, default=QUIET_VOLUME, help=f"media volume step to play at (default {QUIET_VOLUME})"
    )
    self_test.add_argument("--confidence", default="0.5")

    decode = commands.add_parser("decode", help="recognise the clips on the device and time it")
    decode.add_argument("--name", help="name for the result; one is made up if left out")
    decode.add_argument("--commands", help="name of the command list on the device; any speech if left out")
    decode.add_argument("--clips", default="clips", help="folder on the device, such as clips or recordings")
    decode.add_argument("--only", help="only the clips whose names start with this")
    decode.add_argument("--gate", action="store_true", help="only recognise while someone seems to speak")
    decode.add_argument("--confidence", default="0.5")

    listen = commands.add_parser("listen", help="recognise the live microphone")
    listen.add_argument("--name", help="name for the result; one is made up if left out")
    listen.add_argument("--commands")
    listen.add_argument("--seconds", type=int, default=60)
    listen.add_argument("--source", default="VOICE_RECOGNITION")
    listen.add_argument("--no-gate", action="store_true")
    listen.add_argument(
        "--act", action="store_true",
        help="send what is understood to Mirror Home; its sleep schedule and brightness are put back afterwards",
    )
    listen.add_argument("--input", help="a clip on the device to hear in place of the microphone")
    listen.add_argument("--save", help="keep what the microphone heard under this name and fetch it")
    listen.add_argument("--confidence", default="0.5")
    listen.add_argument("--config", type=pathlib.Path, default=DEFAULT_MIRROR_CONFIG)

    tone = commands.add_parser("tone", help="play a tone on the speakers")
    tone.add_argument("--hz", type=int, default=440)
    tone.add_argument("--seconds", type=int, default=2)
    tone.add_argument(
        "--volume", type=int, default=QUIET_VOLUME, help=f"media volume step to play at (default {QUIET_VOLUME})"
    )

    pairing = commands.add_parser("pair", help="pair the lab with Mirror Home so that --act can command it")
    pairing.add_argument("--config", type=pathlib.Path, default=DEFAULT_MIRROR_CONFIG)

    remove = commands.add_parser("remove", help="uninstall the lab app, delete its recordings and revoke its pairing")
    remove.add_argument("--config", type=pathlib.Path, default=DEFAULT_MIRROR_CONFIG)

    options = parser.parse_args(arguments)
    results = options.output / "results"
    try:
        lab = Lab(options.serial)
        if options.command == "setup":
            if not options.apk.is_file():
                raise LabError(f"APK not found: {options.apk}; build it with gradlew :android:voice-lab:assembleDebug")
            lab.install(options.apk)
            if options.model:
                lab.push(options.model, "model")
            if options.clips:
                lab.push(options.clips, "clips")
            if options.commands:
                lab.push(options.commands, "commands.txt")
            print("The lab app is installed.")
        elif options.command == "info":
            print(summarize_info(lab.experiment("info", keep=results)["info"]))
        elif options.command == "record":
            result = lab.experiment(
                "record", name=options.name, keep=results, timeout=options.seconds + 60, seconds=options.seconds,
                source=options.source, rate=options.rate, channels=options.channels,
                effects="1" if options.effects else None, tone=options.tone or None,
                play=options.play, volume=options.volume if options.play or options.tone else None,
            )
            target = options.output / "recordings" / f"{options.name}.wav"
            lab.pull(result["file"], target)
            print(f"{target}\n{summarize_recording(result)}")
        elif options.command == "decode":
            result = lab.experiment(
                "decode", name=options.name, keep=results, timeout=3600,
                commands=options.commands, clips=options.clips, only=options.only,
                gate="1" if options.gate else None, confidence=options.confidence,
            )
            print(summarize_decode(result))
        elif options.command == "selftest":
            print(selftest(lab, options, results))
        elif options.command == "pair":
            pair(lab, options.config, options.output)
            print(f'The lab is paired with Mirror Home as "{PAIRED_NAME}".')
        elif options.command == "listen":
            api, state = mirror_state(lab, options.config) if options.act else (None, None)
            try:
                result = lab.experiment(
                    "listen", name=options.name, keep=results, timeout=options.seconds + 120,
                    commands=options.commands, seconds=options.seconds, source=options.source,
                    gate="0" if options.no_gate else "1", act="1" if options.act else None,
                    input=options.input, save=options.save, confidence=options.confidence,
                )
            finally:
                if state is not None:
                    restore_mirror(api, state)
            if result.get("file"):
                target = options.output / "recordings" / pathlib.PurePosixPath(result["file"]).name
                lab.pull(result["file"], target)
                print(target)
            for event in result["events"]:
                answered = event.get("mirrorAnswered", event.get("mirrorError"))
                print(
                    f"  {event['atMs'] / 1000:6.1f} s  {event['text']!r}"
                    + (f" conf {event['lowestConfidence']}" if event["lowestConfidence"] is not None else "")
                    + (" -> command" if event["command"] else "")
                    + (f", Mirror Home answered {answered}" if answered is not None else "")
                )
            print(
                f"  listened {result['listenedSeconds']} s using {result['cpuShare']:.0%} of one core; "
                f"{result['fedShare']:.0%} of the sound reached the recogniser"
                + (f"; fell {result['behindMs']} ms behind at worst" if result.get("behindMs") else "")
            )
        elif options.command == "tone":
            print(json.dumps(lab.experiment(
                "tone", keep=results, hz=options.hz, seconds=options.seconds, volume=options.volume)))
        elif options.command == "remove":
            revoked = unpair(options.config, options.output)
            lab.remove()
            print(
                "The lab app and its recordings are removed"
                + (" and its pairing is revoked." if revoked else ".")
            )
        return 0
    except (LabError, subprocess.TimeoutExpired) as error:
        print(f"voice_lab: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
