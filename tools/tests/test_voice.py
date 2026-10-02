import contextlib
import hashlib
import http.server
import io
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import voice
from voice import VoiceClient, VoiceError


MODEL = b"a speech model, zipped" * 4


def listening_state(**changes):
    state = {
        "enabled": True,
        "state": "listening",
        "detail": "Listening",
        "wakeWord": "mirror",
        "commands": [
            {"id": "sleep", "caption": "Sleeping", "say": ["mirror go to sleep", "mirror good night"]},
            {"id": "wake", "caption": "Awake", "say": ["mirror wake up", "mirror good morning"]},
        ],
        "model": {"name": "vosk-model-small-en-us-0.15", "bytes": 70_898_967, "installedAt": 1_790_967_492_077},
        "permissionGranted": True,
        "process": {"pid": 4322, "pssKb": 120_497, "restarts": 0},
        "recogniser": {"modelLoadMs": 4100, "cpuShare": 0.34, "behindMs": 0, "listenedSeconds": 600},
        "microphone": {"levelDb": -58.2, "peakDb": -41.0, "silent": False},
        "counts": {"sentences": 40, "wakeWords": 3, "commands": 9, "notUnderstood": 1, "unsure": 2},
        "lastCommand": {"id": "sleep", "at": 1_790_967_511_029, "shown": "Sleeping"},
        "recent": [
            {"at": 1_790_967_511_029, "heard": "mirror go to sleep", "confidence": 1, "outcome": "command", "command": "sleep", "shown": "Sleeping"},
            {"at": 1_790_967_600_000, "heard": "mirror [unk]", "confidence": 1, "outcome": "not-understood", "command": None, "shown": None},
        ],
        "testHooks": False,
    }
    state.update(changes)
    return state


class Download:
    """What a web server sends, read in pieces as the tool reads it."""

    def __init__(self, content):
        self.stream = io.BytesIO(content)

    def read(self, size):
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_error):
        return False


class FetchModelTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.target = pathlib.Path(self.directory.name) / "voice" / "model.zip"
        for name, value in (("MODEL_SHA256", hashlib.sha256(MODEL).hexdigest()), ("MODEL_BYTES", len(MODEL))):
            patcher = mock.patch.object(voice, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.requests = []

    def opener(self, content):
        def open_url(url, timeout):
            self.requests.append(url)
            return Download(content)

        return open_url

    def test_downloads_the_model_from_its_makers_and_keeps_it(self):
        said = []
        self.assertEqual(self.target, voice.fetch_model(self.target, opener=self.opener(MODEL), report=said.append))
        self.assertEqual(MODEL, self.target.read_bytes())
        self.assertEqual([voice.MODEL_URL], self.requests)
        self.assertIn("https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip", said[0])
        self.assertEqual(["model.zip"], [path.name for path in self.target.parent.iterdir()])

    def test_a_model_that_is_here_already_is_not_downloaded_again(self):
        self.target.parent.mkdir()
        self.target.write_bytes(MODEL)
        voice.fetch_model(self.target, opener=self.opener(b"never asked for"))
        self.assertEqual([], self.requests)

    def test_a_file_that_is_not_the_model_is_replaced(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b"half a download")
        said = []
        voice.fetch_model(self.target, opener=self.opener(MODEL), report=said.append)
        self.assertEqual(MODEL, self.target.read_bytes())
        self.assertIn("is not the speech model this tool knows", said[0])

    def test_nothing_is_kept_of_a_download_that_is_not_the_model(self):
        for content in (b"something else entirely", MODEL + b"and more", MODEL[:-1]):
            with self.subTest(len(content)):
                with self.assertRaisesRegex(VoiceError, "is not the speech model this tool knows .* nothing was kept"):
                    voice.fetch_model(self.target, opener=self.opener(content))
                self.assertEqual([], list(self.target.parent.iterdir()))

    def test_a_download_that_fails_says_so_and_leaves_nothing(self):
        def unreachable(url, timeout):
            raise OSError("no route to host")

        with self.assertRaisesRegex(VoiceError, "Unable to download https://alphacephei.com.*no route to host"):
            voice.fetch_model(self.target, opener=unreachable)
        self.assertEqual([], list(self.target.parent.iterdir()))


class KnownModelTest(unittest.TestCase):
    def test_the_model_is_named_by_a_whole_checksum_and_fits_a_mirror(self):
        self.assertRegex(voice.MODEL_SHA256, r"^[0-9a-f]{64}$")
        self.assertLess(voice.MODEL_BYTES, voice.MAX_MODEL_BYTES)
        self.assertTrue(voice.MODEL_URL.startswith("https://"))
        self.assertTrue(voice.MODEL_URL.endswith(voice.DEFAULT_MODEL.name))

    def test_mirror_home_takes_an_archive_as_large_as_the_tool_sends(self):
        source = (
            TOOLS.parent / "android" / "mirror-home" / "src" / "main" / "java" / "dev" / "mirror"
            / "repurpose" / "VoiceModelArchive.java"
        ).read_text(encoding="utf-8")
        self.assertIn("MAX_ARCHIVE_BYTES = 96L * 1024 * 1024", source)
        self.assertEqual(96 * 1024 * 1024, voice.MAX_MODEL_BYTES)


class Mirror(http.server.BaseHTTPRequestHandler):
    """A Mirror's voice API, as much of it as the tool uses."""

    seen = []
    state = {}
    refuse = None

    def answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).seen.append({
            "method": self.command, "path": self.path, "body": body,
            "authorization": self.headers.get("Authorization"),
            "content_type": self.headers.get("Content-Type"),
            "checksum": self.headers.get("X-Content-SHA256"),
        })
        status, reply = 200, type(self).state
        if type(self).refuse:
            status, reply = type(self).refuse
        elif self.command == "PUT" and self.path == "/api/v1/voice/model":
            status = 201
        elif self.command == "PUT" and self.path == "/api/v1/voice":
            reply = dict(reply, enabled=json.loads(body)["enabled"])
        data = json.dumps(reply).encode("utf-8") if reply is not None else b"[1, 2]"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_PUT = do_DELETE = answer

    def log_message(self, *_arguments):
        pass


class VoiceClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Mirror)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        del Mirror.seen[:]
        Mirror.state = listening_state()
        Mirror.refuse = None
        self.client = VoiceClient("127.0.0.1", "paired-credential", self.server.server_address[1])
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive = pathlib.Path(self.directory.name) / "model.zip"
        self.archive.write_bytes(MODEL)

    def test_reads_the_state_with_the_pairing(self):
        self.assertEqual("listening", self.client.state()["state"])
        request = Mirror.seen[0]
        self.assertEqual(("GET", "/api/v1/voice"), (request["method"], request["path"]))
        self.assertEqual("Bearer " + "paired-credential", request["authorization"])

    def test_switches_voice_on_and_off(self):
        self.assertFalse(self.client.set_enabled(False)["enabled"])
        self.assertTrue(self.client.set_enabled(True)["enabled"])
        self.assertEqual(
            [{"enabled": False}, {"enabled": True}],
            [json.loads(request["body"]) for request in Mirror.seen],
        )

    def test_sends_the_model_as_an_archive_with_its_checksum(self):
        steps = []
        self.client.install_model(
            self.archive, progress=lambda percent, sent, total: steps.append((percent, sent, total))
        )
        request = Mirror.seen[0]
        self.assertEqual(("PUT", "/api/v1/voice/model"), (request["method"], request["path"]))
        self.assertEqual(MODEL, request["body"])
        self.assertEqual("application/zip", request["content_type"])
        self.assertEqual(hashlib.sha256(MODEL).hexdigest(), request["checksum"])
        self.assertEqual("Bearer " + "paired-credential", request["authorization"])
        self.assertEqual((100, len(MODEL), len(MODEL)), steps[-1])

    def test_removes_the_model(self):
        self.client.remove_model()
        self.assertEqual(("DELETE", "/api/v1/voice/model"), (Mirror.seen[0]["method"], Mirror.seen[0]["path"]))

    def test_a_refusal_is_reported_in_the_mirrors_words(self):
        Mirror.refuse = (400, {"error": "This is not a speech model: am/final.mdl is missing"})
        with self.assertRaisesRegex(
            VoiceError, r"The Mirror refused \(400\): This is not a speech model: am/final.mdl is missing"
        ):
            self.client.install_model(self.archive)

    def test_a_mirror_home_from_before_voice_is_named_as_that(self):
        Mirror.refuse = (404, {"error": "Not found"})
        with self.assertRaisesRegex(VoiceError, "has no voice commands; they came with version 2.3.0"):
            self.client.state()

    def test_an_answer_that_is_not_the_api_is_not_passed_on(self):
        Mirror.refuse = (200, None)
        with self.assertRaisesRegex(VoiceError, "something unexpected"):
            self.client.state()

    def test_a_mirror_that_cannot_be_reached_is_named(self):
        with mock.patch.object(voice, "JSON_TIMEOUT_SECONDS", 2):
            unreachable = VoiceClient("127.0.0.1", "credential", 9)
            with self.assertRaisesRegex(VoiceError, r"Unable to reach the Mirror at 127\.0\.0\.1:9"):
                unreachable.state()

    def test_an_archive_too_large_or_missing_is_not_sent(self):
        with self.assertRaisesRegex(VoiceError, "No such file"):
            self.client.install_model(pathlib.Path(self.directory.name) / "missing.zip")
        with mock.patch.object(voice, "MAX_MODEL_BYTES", len(MODEL) - 1):
            with self.assertRaisesRegex(VoiceError, "a Mirror takes a model archive of at most"):
                self.client.install_model(self.archive)
        self.assertEqual([], Mirror.seen)

    def test_only_a_mirror_on_the_home_network_is_spoken_to(self):
        with self.assertRaisesRegex(VoiceError, "private or loopback"):
            VoiceClient("8.8.8.8", "credential")
        with self.assertRaisesRegex(VoiceError, "no token"):
            VoiceClient("192.168.1.20", "")


class SettleTest(unittest.TestCase):
    class Starting:
        def __init__(self, states):
            self.states = list(states)

        def state(self):
            return {"state": self.states.pop(0)}

    def test_waits_while_the_recogniser_starts(self):
        waited = []
        client = self.Starting(["loading", "listening"])
        state = voice.settle(client, {"state": "starting"}, sleep=waited.append, clock=lambda: 0.0)
        self.assertEqual("listening", state["state"])
        self.assertEqual([1.0, 1.0], waited)

    def test_what_has_settled_is_not_asked_again(self):
        for settled in ("listening", "off", "no-model", "no-permission", "error"):
            self.assertEqual(settled, voice.settle(self.Starting([]), {"state": settled})["state"])

    def test_gives_up_after_its_time_and_reports_where_it_stands(self):
        ticks = iter(range(0, 1000, 30))
        client = self.Starting(["starting"] * 10)
        state = voice.settle(
            client, {"state": "starting"}, timeout=90, sleep=lambda _: None, clock=lambda: next(ticks)
        )
        self.assertEqual("starting", state["state"])
        self.assertGreater(len(client.states), 5)


class DescribeTest(unittest.TestCase):
    def text(self, **changes):
        return "\n".join(voice.describe(listening_state(**changes)))

    def test_a_listening_mirror_is_described_with_what_it_heard(self):
        text = self.text()
        self.assertIn("Voice commands: on - Listening", text)
        self.assertIn("vosk-model-small-en-us-0.15 (68 MB)", text)
        self.assertIn("allowed; level -58.2 dBFS, peak -41.0 dBFS", text)
        self.assertIn("process 4322, 117 MB, 34 % of one core, model loaded in 4.1 s", text)
        self.assertIn("9 command(s), 3 time(s) its name alone, 1 not understood, 2 too unsure to act on", text)
        self.assertIn("Last command:   sleep at ", text)
        self.assertIn('"mirror [unk]"  ->  not-understood', text)
        self.assertIn('"mirror go to sleep"  ->  command sleep, showed "Sleeping"', text)
        self.assertIn('Say, for example: "mirror go to sleep"; "mirror wake up"', text)

    def test_what_is_missing_comes_with_what_to_do(self):
        without_model = self.text(state="no-model", detail="No speech model is installed", model=None)
        self.assertIn("none. Install it with: python tools/voice.py install-model", without_model)
        not_allowed = self.text(
            state="no-permission", detail="Mirror Home may not use the microphone", permissionGranted=False
        )
        self.assertIn("python tools/otactl.py grant-permission microphone --confirm", not_allowed)
        self.assertIn("adb shell pm grant dev.mirror.repurpose android.permission.RECORD_AUDIO", not_allowed)
        off = self.text(enabled=False, state="off", detail="Voice commands are switched off")
        self.assertIn("Voice commands: off - Voice commands are switched off", off)
        self.assertIn("Switch it on with: python tools/voice.py on", off)

    def test_a_silent_microphone_and_restarts_are_pointed_out(self):
        text = self.text(
            microphone={"levelDb": -120.0, "peakDb": -120.0, "silent": True},
            process={"pid": 4400, "pssKb": 120_000, "restarts": 3},
        )
        self.assertIn("allowed, but it delivers only silence", text)
        self.assertIn("restarted 3 time(s) since Mirror Home started", text)

    def test_a_mirror_that_is_off_is_described_without_a_recogniser(self):
        text = self.text(
            enabled=False, state="off", detail="Voice commands are switched off",
            process={"pid": None, "pssKb": None, "restarts": 0},
            recogniser={"modelLoadMs": None, "cpuShare": None, "behindMs": 0, "listenedSeconds": 0},
            microphone={"levelDb": None, "peakDb": None, "silent": False},
            lastCommand=None, recent=[],
        )
        self.assertNotIn("Recogniser:", text)
        self.assertNotIn("Last command:", text)
        self.assertIn("Microphone:     allowed", text)


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.client = mock.Mock()
        self.client.state.return_value = listening_state()
        self.client.set_enabled.side_effect = lambda enabled: listening_state(
            enabled=enabled, state="listening" if enabled else "off"
        )
        self.client.install_model.return_value = listening_state()
        self.client.remove_model.return_value = listening_state(model=None, state="no-model")
        patcher = mock.patch.object(voice, "client_from_config", return_value=self.client)
        self.from_config = patcher.start()
        self.addCleanup(patcher.stop)

    def run_tool(self, *arguments):
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            code = voice.main(list(arguments))
        return code, output.getvalue(), errors.getvalue()

    def test_status_is_for_people_unless_json_is_asked_for(self):
        code, output, _ = self.run_tool("status")
        self.assertEqual(0, code)
        self.assertIn("Voice commands: on - Listening", output)
        code, output, _ = self.run_tool("--json", "status")
        self.assertEqual("listening", json.loads(output)["state"])

    def test_on_and_off_switch_and_report(self):
        self.assertIn("Voice commands: on", self.run_tool("on")[1])
        self.client.set_enabled.assert_called_with(True)
        self.assertIn("Voice commands: off", self.run_tool("off")[1])
        self.client.set_enabled.assert_called_with(False)

    def test_install_downloads_the_usual_model_unless_a_file_is_named(self):
        with mock.patch.object(voice, "fetch_model", return_value=pathlib.Path("kept.zip")) as fetch:
            self.assertEqual(0, self.run_tool("install-model")[0])
            fetch.assert_called_once()
            self.assertEqual(pathlib.Path("kept.zip"), self.client.install_model.call_args.args[0])
            self.run_tool("install-model", "--file", "other-model.zip")
            fetch.assert_called_once()
            self.assertEqual(pathlib.Path("other-model.zip"), self.client.install_model.call_args.args[0])

    def test_remove_model_takes_it_off_the_mirror(self):
        code, output, _ = self.run_tool("remove-model")
        self.assertEqual(0, code)
        self.client.remove_model.assert_called_once()
        self.assertIn("Speech model:   none", output)

    def test_fetch_model_needs_no_mirror(self):
        with mock.patch.object(voice, "fetch_model", return_value=pathlib.Path("kept.zip")) as fetch:
            code, output, _ = self.run_tool("fetch-model", "--file", "kept.zip")
        self.assertEqual((0, "kept.zip"), (code, output.strip()))
        self.assertEqual(pathlib.Path("kept.zip"), fetch.call_args.args[0])
        self.from_config.assert_not_called()

    def test_a_fault_is_one_line_and_a_failing_exit_code(self):
        self.client.state.side_effect = VoiceError("Unable to reach the Mirror at 192.168.1.20:8787: timed out")
        code, output, errors = self.run_tool("status")
        self.assertEqual((1, ""), (code, output))
        self.assertEqual("voice: Unable to reach the Mirror at 192.168.1.20:8787: timed out\n", errors)


class ConfigTest(unittest.TestCase):
    def test_uses_the_pairing_the_other_tools_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "pairing.json"
            path.write_text(
                json.dumps({"host": "192.168.1.20", "port": 8788, "token": "paired"}), encoding="utf-8"
            )
            client = voice.client_from_config(path, None, None)
            self.assertEqual(("192.168.1.20", 8788, "paired"), (client.host, client.port, client.token))
            other = voice.client_from_config(path, "192.168.1.21", 8787)
            self.assertEqual(("192.168.1.21", 8787), (other.host, other.port))

    def test_without_a_pairing_it_says_how_to_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(VoiceError, "Pair first with tools/background-video.ps1 pair"):
                voice.client_from_config(pathlib.Path(directory) / "none.json", None, None)


if __name__ == "__main__":
    unittest.main()
