import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import validate
import voice_lab
from voice_lab import LabError


class FakeLab(voice_lab.Lab):
    """A lab whose device is a table of answers."""

    def __init__(self, result=None, *, started="Starting service: Intent { cmp=... }", running=True):
        self.serial = "SERIAL"
        self.adb = "adb"
        self.calls = []
        self.result = result
        self.started = started
        self.is_running = running
        self.pushed = {}
        self.network = "1: lo    inet 127.0.0.1/8 scope host lo\n9: wlan0    inet 10.0.0.196/24 brd 10.0.0.255\n"

    def run(self, *arguments, timeout=300.0, check=True):
        self.calls.append(arguments)
        command = " ".join(arguments)
        if arguments[0] == "push":
            source = pathlib.Path(arguments[1])
            self.pushed[arguments[2]] = source.read_text(encoding="ascii") if source.is_file() else None
            return b""
        if "am startservice" in command:
            return self.started.encode()
        if arguments[:2] == ("shell", "ps"):
            return f"u0_a55 2961 {voice_lab.PACKAGE}".encode() if self.is_running else b"root 1 /init"
        if command.startswith("shell ls "):
            return b"found" if self.result is not None else b""
        if arguments[:2] == ("exec-out", "cat"):
            return json.dumps(self.result).encode()
        if arguments[0] == "logcat":
            return b"I/art: noise\nE/AndroidRuntime( 2961): java.lang.ExceptionInInitializerError\n"
        if command.startswith("shell ip -o -4 addr"):
            return self.network.encode()
        return b""

    def started_with(self):
        return next(" ".join(call) for call in self.calls if "am startservice" in " ".join(call))


@mock.patch("voice_lab.time.sleep", lambda _seconds: None)
class ExperimentTest(unittest.TestCase):
    def test_an_experiment_is_started_with_every_setting_as_a_string(self):
        lab = FakeLab({"ok": True})
        lab.experiment("record", name="near", seconds=6, source="MIC", effects=None)
        self.assertEqual(
            f"shell am startservice -n {voice_lab.SERVICE} "
            "--es cmd record --es out near --es seconds 6 --es source MIC",
            lab.started_with(),
        )

    def test_an_earlier_result_of_the_same_name_is_removed_first(self):
        lab = FakeLab({"ok": True})
        lab.experiment("info", name="again")
        commands = [" ".join(call) for call in lab.calls]
        removal = f"shell rm -f {voice_lab.DEVICE_FOLDER}/results/again.json"
        self.assertLess(commands.index(removal), commands.index(lab.started_with()))

    def test_a_setting_a_shell_would_read_as_more_than_a_word_is_refused(self):
        for value in ("two words", "a;reboot", "$(id)", "", "quote'"):
            lab = FakeLab({"ok": True})
            with self.assertRaisesRegex(LabError, "cannot be passed"):
                lab.experiment("decode", clips=value)
            self.assertEqual([], lab.calls)

    def test_the_result_is_returned_and_a_copy_kept(self):
        lab = FakeLab({"ok": True, "info": {"sdk": 23}})
        with tempfile.TemporaryDirectory() as directory:
            keep = pathlib.Path(directory) / "results"
            result = lab.experiment("info", name="first", keep=keep)
            self.assertEqual(23, result["info"]["sdk"])
            self.assertEqual(result, json.loads((keep / "first.json").read_text(encoding="utf-8")))

    def test_a_failure_on_the_device_is_reported_in_its_words(self):
        lab = FakeLab({"ok": False, "error": "java.io.IOException: No speech model in /sdcard/x"})
        with self.assertRaisesRegex(LabError, "decode failed on the device: .*No speech model"):
            lab.experiment("decode")

    def test_a_result_cut_short_is_reported_with_what_android_logged(self):
        lab = FakeLab({"command": "decode"})
        with self.assertRaisesRegex(LabError, "ExceptionInInitializerError"):
            lab.experiment("decode")

    def test_a_lab_app_that_is_not_installed_is_reported(self):
        lab = FakeLab(started="Error: Not found; no service started.")
        with self.assertRaisesRegex(LabError, "did not start"):
            lab.experiment("info")

    def test_a_lab_app_that_dies_without_a_result_is_noticed(self):
        lab = FakeLab(running=False)
        with self.assertRaisesRegex(LabError, "stopped during listen:\n.*ExceptionInInitializerError"):
            lab.experiment("listen", timeout=3600)

    def test_an_experiment_that_never_ends_stops_being_waited_for(self):
        lab = FakeLab()
        with mock.patch("voice_lab.time.monotonic", side_effect=[0, 1, 2, 500, 501]):
            with self.assertRaisesRegex(LabError, "no result within 120 s"):
                lab.experiment("listen")


class SummaryTest(unittest.TestCase):
    STATS = {
        "peakDb": -3.1, "noiseFloorDb": -61.0, "speechAboveNoiseDb": 34.5,
        "clippedSamples": 0, "silent": False,
    }

    def test_a_recording_is_described_by_channel(self):
        text = voice_lab.summarize_recording(
            {"stats": [self.STATS, dict(self.STATS, silent=True)], "channelDifferenceDb": -120.0}
        )
        self.assertIn("channel 1: peak -3.1 dB, room -61.0 dB, speech 34.5 dB above the room, 0 clipped", text)
        self.assertTrue(text.splitlines()[1].endswith("SILENT"))
        self.assertIn("difference between the channels: -120.0 dB", text)

    def test_recognition_is_listed_clip_by_clip(self):
        text = voice_lab.summarize_decode({
            "modelLoadMs": 2857, "memoryBefore": {"pssKb": 4096}, "memoryLoaded": {"pssKb": 115_000},
            "audioSeconds": 3.14, "wallMs": 2500, "realTimeFactor": 0.8,
            "clips": [
                {"clip": "sleep.wav", "heard": [
                    {"text": "mirror go to sleep", "lowestConfidence": 1, "command": "mirror go to sleep"}]},
                {"clip": "talk.wav", "heard": [{"text": "[unk]", "lowestConfidence": None, "command": None}]},
                {"clip": "quiet.wav", "heard": []},
            ],
        })
        self.assertIn("model loaded in 2857 ms; memory 4 MB -> 112 MB", text)
        self.assertIn("3.14 s of sound recognised in 2.5 s (real-time factor 0.8)", text)
        self.assertRegex(text, r"sleep\.wav +'mirror go to sleep' conf 1 -> command")
        self.assertRegex(text, r"talk\.wav +'\[unk\]'\n")
        self.assertRegex(text, r"quiet\.wav +\(nothing\)")

    def test_a_device_is_described_with_the_microphone_settings_that_open(self):
        text = voice_lab.summarize_info({
            "fingerprint": "mirror/mirror/x", "sdk": 23, "abis": ["armeabi-v7a"], "processors": 4,
            "cpuMaxKhz": "1209600", "memoryAvailableMb": 310, "memoryTotalMb": 952,
            "memoryLowThresholdMb": 96, "microphoneFeature": True, "mayRecord": True,
            "inputDevices": [], "echoCanceler": True, "noiseSuppressor": False, "gainControl": False,
            "recognitionServices": [], "speechEngines": [], "mediaVolume": 7, "mediaVolumeMax": 15,
            "configurations": [
                {"source": "MIC", "rate": 16000, "channels": 1, "opens": True},
                {"source": "MIC", "rate": 48000, "channels": 2, "opens": True},
                {"source": "CAMCORDER", "rate": 16000, "channels": 1, "opens": False},
            ],
        })
        self.assertIn("Android API 23, armeabi-v7a, 4 processors up to 1209 MHz", text)
        self.assertIn("the device's own processing: echoCanceler", text)
        self.assertIn("MIC opens as: 16 kHz mono, 48 kHz stereo", text)
        self.assertNotIn("CAMCORDER", text)
        self.assertIn("inputs: none listed", text)


class AddressesTest(unittest.TestCase):
    def addresses(self, network):
        lab = FakeLab()
        lab.network = network
        return lab.addresses()

    def test_addresses_are_read_from_the_ip_tool(self):
        self.assertEqual(
            ["127.0.0.1", "10.0.0.196"],
            self.addresses("1: lo    inet 127.0.0.1/8 scope host lo\n9: wlan0    inet 10.0.0.196/24 brd 10.0.0.255\n"),
        )

    def test_addresses_are_read_from_either_kind_of_ifconfig(self):
        self.assertEqual(["10.0.0.196"], self.addresses(
            "/system/bin/sh: ip: not found\nwlan0     Link encap:Ethernet\n          inet addr:10.0.0.196  Bcast:10.0.0.255\n"))
        self.assertEqual(["10.0.0.196"], self.addresses(
            "wlan0: ip 10.0.0.196 mask 255.255.255.0 flags [up broadcast running multicast]\n"))

    def test_the_address_android_was_leased_is_read_too(self):
        self.assertEqual(["10.0.0.196"], self.addresses("ifconfig: wlan0: No such device\n10.0.0.196\n"))

    def test_a_device_without_a_network_has_none(self):
        self.assertEqual([], self.addresses("\n"))


class PairingTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output = pathlib.Path(directory.name) / "voice-lab"
        self.config = pathlib.Path(directory.name) / "mirror.json"
        self.config.write_text(json.dumps({"host": "10.0.0.196", "token": "owner"}), encoding="utf-8")
        self.api = mock.Mock()
        self.api.expect.side_effect = self.answer
        self.requests = []
        patcher = mock.patch("voice_lab.mirror_api", return_value=(self.api, "10.0.0.196"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def answer(self, method, path, body=None, **options):
        self.requests.append((method, path, body, options))
        if path == "/api/v1/pair/window":
            return {"code": "123456"}
        if path == "/api/v1/pair":
            return {"token": "lab-credential", "clientId": "client-7"}
        return {}

    def test_the_lab_is_paired_under_a_name_its_owner_will_recognise(self):
        lab = FakeLab()
        self.assertEqual("client-7", voice_lab.pair(lab, self.config, self.output))
        self.assertEqual(
            ("POST", "/api/v1/pair", {"code": "123456", "name": "Voice lab (temporary)"}, {"token": None}),
            self.requests[1],
        )
        self.assertEqual({f"{voice_lab.DEVICE_FOLDER}/token.txt": "lab-credential"}, lab.pushed)
        record = json.loads((self.output / "pairing.json").read_text(encoding="utf-8"))
        self.assertEqual({"clientId": "client-7", "host": "10.0.0.196"}, record)
        self.assertNotIn("lab-credential", (self.output / "pairing.json").read_text(encoding="utf-8"))

    def test_a_device_that_is_not_that_mirror_is_not_given_its_credential(self):
        lab = FakeLab()
        lab.addresses = lambda: ["10.0.2.15"]
        with self.assertRaisesRegex(LabError, "is not the Mirror at 10.0.0.196"):
            voice_lab.pair(lab, self.config, self.output)
        self.assertEqual([], self.requests)

    def test_a_lab_already_paired_is_not_paired_twice(self):
        voice_lab.pair(FakeLab(), self.config, self.output)
        with self.assertRaisesRegex(LabError, "already paired"):
            voice_lab.pair(FakeLab(), self.config, self.output)
        self.assertEqual(2, len(self.requests))

    def test_a_mirror_that_refuses_leaves_nothing_behind(self):
        self.api.expect.side_effect = validate.CheckFailed("POST /api/v1/pair/window answered 401")
        with self.assertRaisesRegex(LabError, "did not pair the lab: .*401"):
            voice_lab.pair(FakeLab(), self.config, self.output)
        self.assertFalse((self.output / "pairing.json").exists())

    def test_removing_the_lab_revokes_its_pairing(self):
        voice_lab.pair(FakeLab(), self.config, self.output)
        self.assertTrue(voice_lab.unpair(self.config, self.output))
        self.assertEqual(("POST", "/api/v1/clients/revoke", {"id": "client-7"}, {}), self.requests[-1])
        self.assertFalse((self.output / "pairing.json").exists())
        self.assertFalse(voice_lab.unpair(self.config, self.output))

    def test_a_pairing_that_cannot_be_revoked_is_kept_on_record(self):
        voice_lab.pair(FakeLab(), self.config, self.output)
        self.api.expect.side_effect = OSError("timed out")
        with self.assertRaisesRegex(LabError, "could not be revoked.*Paired devices"):
            voice_lab.unpair(self.config, self.output)
        self.assertTrue((self.output / "pairing.json").is_file())


class MirrorStateTest(unittest.TestCase):
    AUTOMATION = {
        "enabled": False, "wakeTime": "07:30", "sleepTime": "22:30", "wakeBrightness": 190,
        "ambientEnabled": False, "ambientMinimum": 20, "ambientMaximum": 220, "motionEnabled": True,
        "motionTimeoutSeconds": 300, "motionSensitivity": 6, "sleeping": False, "manualOverride": False,
        "motion": {"state": "monitoring"},
    }

    def api(self, automation=None, brightness=190):
        api = mock.Mock()
        api.expect.side_effect = lambda method, path, body=None, **_options: (
            dict(self.AUTOMATION, **(automation or {})) if path == "/api/v1/automation" and method == "GET"
            else {"brightness": brightness} if path == "/api/v1/status" else {}
        )
        return api

    def state(self, **options):
        api = self.api(**options)
        with mock.patch("voice_lab.mirror_api", return_value=(api, "10.0.0.196")):
            _, state = voice_lab.mirror_state(FakeLab(), pathlib.Path("mirror.json"))
        api.expect.reset_mock()
        return api, state

    def sent(self, api):
        return [(call.args[0], call.args[1], call.args[2]) for call in api.expect.call_args_list]

    def test_only_the_settings_an_owner_chose_are_remembered(self):
        _, state = self.state()
        self.assertEqual(set(validate.AUTOMATION_SETTINGS), set(state["settings"]))
        self.assertEqual(190, state["brightness"])
        self.assertFalse(state["sleeping"])

    def test_an_awake_mirror_gets_its_schedule_and_brightness_back(self):
        api, state = self.state()
        voice_lab.restore_mirror(api, state)
        self.assertEqual(
            [("PUT", "/api/v1/automation", state["settings"]),
             ("POST", "/api/v1/control/brightness", {"value": 190})],
            self.sent(api),
        )

    def test_a_mirror_that_slept_by_itself_is_left_to_its_schedule(self):
        api, state = self.state(automation={"sleeping": True}, brightness=0)
        voice_lab.restore_mirror(api, state)
        self.assertEqual([("PUT", "/api/v1/automation", state["settings"])], self.sent(api))

    def test_a_mirror_its_owner_had_put_to_sleep_is_put_to_sleep_again(self):
        api, state = self.state(automation={"sleeping": True, "manualOverride": True}, brightness=0)
        voice_lab.restore_mirror(api, state)
        self.assertEqual(("POST", "/api/v1/automation/sleep", {}), self.sent(api)[-1])

    def test_a_device_that_is_not_that_mirror_is_not_commanded(self):
        lab = FakeLab()
        lab.addresses = lambda: ["10.0.2.15"]
        api = self.api()
        with mock.patch("voice_lab.mirror_api", return_value=(api, "10.0.0.196")):
            with self.assertRaisesRegex(LabError, "--act is for the Mirror the lab runs on"):
                voice_lab.mirror_state(lab, pathlib.Path("mirror.json"))
        api.expect.assert_not_called()

    def test_a_mirror_that_cannot_be_put_back_says_what_to_do(self):
        api, state = self.state()
        api.expect.side_effect = validate.CheckFailed("PUT /api/v1/automation answered 500")
        with self.assertRaisesRegex(LabError, "save the sleep schedule again"):
            voice_lab.restore_mirror(api, state)


@mock.patch("voice_lab.time.sleep", lambda _seconds: None)
class SelfTestTest(unittest.TestCase):
    def test_each_clip_is_played_recorded_and_then_recognised(self):
        recording = {
            "ok": True, "file": "recordings/x.wav", "mediaVolume": 9, "stats": [SummaryTest.STATS],
            "modelLoadMs": 1, "memoryBefore": {"pssKb": 1024}, "memoryLoaded": {"pssKb": 2048},
            "audioSeconds": 1, "wallMs": 1000, "realTimeFactor": 1.0, "clips": [],
        }
        lab = FakeLab(recording)
        with tempfile.TemporaryDirectory() as directory:
            clips = pathlib.Path(directory) / "clips"
            clips.mkdir()
            for name in ("b-wake.wav", "a-sleep.wav", "notes.txt"):
                (clips / name).write_bytes(b"")
            options = mock.Mock(
                clips=clips, output=pathlib.Path(directory) / "out", source="MIC", volume=9,
                commands="commands.txt", confidence="0.5",
            )
            text = voice_lab.selftest(lab, options, options.output / "results")
        started = [" ".join(call) for call in lab.calls if "am startservice" in " ".join(call)]
        self.assertEqual(3, len(started))
        self.assertIn("--es out self-a-sleep --es source MIC --es play clips/selftest/a-sleep.wav --es volume 9", started[0])
        self.assertIn("--es out self-b-wake", started[1])
        self.assertIn("--es cmd decode --es out selftest --es clips recordings --es only self-", started[2])
        self.assertIn("a-sleep.wav, media volume 9:", text)

    def test_a_folder_without_clips_is_refused_before_anything_is_played(self):
        lab = FakeLab({"ok": True})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LabError, "No .wav files"):
                voice_lab.selftest(lab, mock.Mock(clips=pathlib.Path(directory)), pathlib.Path(directory))
        self.assertEqual([], lab.calls)


class CommandLineTest(unittest.TestCase):
    def run_main(self, *arguments, lab=None):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch("voice_lab.Lab", return_value=lab or FakeLab({"ok": True})):
            with redirect_stdout(output), redirect_stderr(errors):
                code = voice_lab.main(["--serial", "SERIAL", *arguments])
        return code, output.getvalue(), errors.getvalue()

    def test_a_device_must_be_named(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            voice_lab.main(["info"])

    @mock.patch("voice_lab.time.sleep", lambda _seconds: None)
    def test_a_failure_ends_with_its_reason_and_a_failing_exit_code(self):
        code, _, errors = self.run_main("decode", lab=FakeLab({"ok": False, "error": "No .wav files in clips"}))
        self.assertEqual(1, code)
        self.assertIn("voice_lab: decode failed on the device: No .wav files in clips", errors)

    def test_setup_needs_the_apk_to_have_been_built(self):
        code, _, errors = self.run_main("setup", "--apk", "missing.apk")
        self.assertEqual(1, code)
        self.assertIn("build it with gradlew :android:voice-lab:assembleDebug", errors)

    @mock.patch("voice_lab.time.sleep", lambda _seconds: None)
    def test_listening_reports_what_was_heard_and_what_mirror_home_answered(self):
        lab = FakeLab({
            "ok": True, "listenedSeconds": 60.0, "cpuShare": 0.31, "fedShare": 0.12, "behindMs": 140,
            "events": [
                {"atMs": 7100, "text": "mirror go to sleep", "lowestConfidence": 1,
                 "command": "mirror go to sleep", "mirrorAnswered": 200},
                {"atMs": 13000, "text": "[unk]", "lowestConfidence": None, "command": None},
            ],
        })
        state = {"settings": {}, "manualOverride": False, "sleeping": False, "brightness": 190}
        with mock.patch("voice_lab.mirror_state", return_value=("API", state)) as remembered, \
                mock.patch("voice_lab.restore_mirror") as restored:
            code, output, _ = self.run_main("listen", "--commands", "commands.txt", "--act", lab=lab)
        self.assertEqual(0, code)
        remembered.assert_called_once()
        restored.assert_called_once_with("API", state)
        self.assertIn("7.1 s  'mirror go to sleep' conf 1 -> command, Mirror Home answered 200", output)
        self.assertIn("13.0 s  '[unk]'\n", output)
        self.assertIn("using 31% of one core; 12% of the sound reached the recogniser; fell 140 ms behind", output)
        self.assertIn("--es commands commands.txt --es seconds 60 --es source VOICE_RECOGNITION --es gate 1 --es act 1",
                      lab.started_with())

    @mock.patch("voice_lab.time.sleep", lambda _seconds: None)
    def test_the_mirror_is_put_back_even_when_listening_fails(self):
        state = {"settings": {}, "manualOverride": False, "sleeping": False, "brightness": 190}
        with mock.patch("voice_lab.mirror_state", return_value=("API", state)), \
                mock.patch("voice_lab.restore_mirror") as restored:
            code, _, errors = self.run_main(
                "listen", "--act", lab=FakeLab({"ok": False, "error": "No token.txt beside the model"}))
        self.assertEqual(1, code)
        self.assertIn("No token.txt", errors)
        restored.assert_called_once_with("API", state)

    @mock.patch("voice_lab.time.sleep", lambda _seconds: None)
    def test_listening_without_acting_leaves_mirror_home_alone(self):
        lab = FakeLab({"ok": True, "listenedSeconds": 5.0, "cpuShare": 0.0, "fedShare": 0.0, "events": [],
                       "file": "recordings/kitchen.wav"})
        with mock.patch("voice_lab.mirror_state") as remembered:
            code, output, _ = self.run_main("listen", "--save", "kitchen", "--seconds", "5", lab=lab)
        self.assertEqual(0, code)
        remembered.assert_not_called()
        self.assertIn("--es save kitchen", lab.started_with())
        self.assertIn(("pull", f"{voice_lab.DEVICE_FOLDER}/recordings/kitchen.wav"),
                      [call[:2] for call in lab.calls])
        self.assertIn("kitchen.wav", output)

    @mock.patch("voice_lab.time.sleep", lambda _seconds: None)
    def test_sounds_are_played_quietly_unless_a_volume_is_asked_for(self):
        recording = {"ok": True, "file": "recordings/x.wav", "stats": [SummaryTest.STATS]}
        for arguments, expected in (
            (("tone",), "--es volume 4"),
            (("tone", "--volume", "7"), "--es volume 7"),
            (("record", "--name", "x", "--play", "clips/a.wav"), "--es play clips/a.wav --es volume 4"),
            (("record", "--name", "x", "--tone", "440"), "--es tone 440 --es volume 4"),
        ):
            lab = FakeLab(recording)
            self.assertEqual(0, self.run_main(*arguments, lab=lab)[0])
            self.assertIn(expected, lab.started_with())
        self.assertEqual(4, voice_lab.QUIET_VOLUME)

    @mock.patch("voice_lab.time.sleep", lambda _seconds: None)
    def test_a_silent_recording_leaves_the_volume_alone(self):
        lab = FakeLab({"ok": True, "file": "recordings/x.wav", "stats": [SummaryTest.STATS]})
        self.assertEqual(0, self.run_main("record", "--name", "x", lab=lab)[0])
        self.assertNotIn("volume", lab.started_with())

    def test_the_self_test_is_quiet_by_default(self):
        with mock.patch("voice_lab.selftest", return_value="done") as tested:
            self.assertEqual(0, self.run_main("selftest", "--clips", "clips")[0])
        self.assertEqual(4, tested.call_args.args[1].volume)

    def test_removing_uninstalls_and_deletes_the_recordings(self):
        lab = FakeLab()
        with mock.patch("voice_lab.unpair", return_value=True):
            code, output, _ = self.run_main("remove", lab=lab)
        self.assertEqual(0, code)
        self.assertIn(("uninstall", voice_lab.PACKAGE), lab.calls)
        self.assertIn(("shell", f"rm -rf /sdcard/Android/data/{voice_lab.PACKAGE}"), lab.calls)
        self.assertIn("removed and its pairing is revoked", output)


class FindAdbTest(unittest.TestCase):
    def test_adb_on_the_path_is_used(self):
        with mock.patch("voice_lab.shutil.which", return_value="/usr/bin/adb"):
            self.assertEqual("/usr/bin/adb", voice_lab.find_adb())

    def test_otherwise_the_sdks_adb_is_used(self):
        with mock.patch("voice_lab.shutil.which", return_value=None), \
                mock.patch("voice_lab.android_emulator.sdk_root", return_value=pathlib.Path("sdk")), \
                mock.patch("voice_lab.android_emulator.adb_path", return_value=pathlib.Path("sdk/adb")):
            self.assertEqual(str(pathlib.Path("sdk/adb")), voice_lab.find_adb())

    def test_no_adb_at_all_is_an_error_that_says_so(self):
        with mock.patch("voice_lab.shutil.which", return_value=None), \
                mock.patch("voice_lab.android_emulator.sdk_root",
                           side_effect=voice_lab.android_emulator.EmulatorError("Android SDK not found")):
            with self.assertRaisesRegex(LabError, "Android SDK not found"):
                voice_lab.find_adb()


if __name__ == "__main__":
    unittest.main()
