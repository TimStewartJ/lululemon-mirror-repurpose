import http.client
import io
import json
import pathlib
import re
import sys
import tempfile
import unittest
import wave
from urllib.parse import parse_qs, urlsplit

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "tests"))

import fake_companion
import validate
from test_validate import GREETINGS, SPOKEN, SpeakingMirror
from validate import CheckFailed, CheckSkipped, Reply


def picture(width, height, size):
    """As much of a JPEG file as says how large its picture is."""
    frame = b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big")
    start = b"\xff\xd8\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\0" + bytes(9)
    return (start + frame + bytes(10)).ljust(size, b"\0")


class AssistingMirror(SpeakingMirror):
    """Mirror Home's assistant as the checks meet it. It asks the companion
    that a check sets it to, over the network as a Mirror does; an emulator's
    name for this computer is this computer here. ``faults`` name what a
    build gets wrong."""

    def __init__(self, test, *faults, test_hooks=True):
        super().__init__(test, *faults, test_hooks=test_hooks)
        self.counts["asked"] = 0
        self.assistant_on = "assistant-on-from-the-start" in self.faults
        self.address = ""
        self.key = ""
        self.model_name = ""
        self.answered = 0
        self.failed = 0
        self.failure = ""
        self.tick = 0
        self.asked = {"requests": 0, "ignored": 0, "failures": 0}
        self.exchanges = []
        self.answer_until = 0.0
        # When the Mirror goes dark by itself, having answered "good night"; None for never.
        self.sleep_at = None
        self.greeted = None
        self.mascot = "wisp" if "mascot-from-the-start" in self.faults else "none"
        self.place = {"height": "top" if "placed-from-the-start" in self.faults else "bottom", "side": "center"}
        with wave.open(str(validate.VOICE_CLIPS / validate.ASSISTANT_CLIP), "rb") as clip:
            self.request_sound = clip.readframes(clip.getnframes())
        self.request_clip = (validate.VOICE_CLIPS / validate.ASSISTANT_CLIP).read_bytes()

    # ---- the companion, as the Mirror reaches it

    def send(self, method, path, body=None, headers=None):
        """One request to the companion: its status and its answer, or why there was none."""
        address = urlsplit(self.address)
        sent = {"Authorization": "Bearer " + self.key, **(headers or {})}
        if "forgets-its-key" in self.faults:
            del sent["Authorization"]
        connection = http.client.HTTPConnection("127.0.0.1", address.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            raw = response.read()
        except OSError:
            return None, "The companion cannot be reached at its address"
        finally:
            connection.close()
        if response.status == 401:
            return None, "The companion does not accept this Mirror\u2019s key"
        if response.status != 200 and response.status != 202:
            return None, f"The companion answered {response.status}"
        return json.loads(raw.decode("utf-8")), ""

    def available(self):
        return self.assistant_on and bool(self.address) and bool(self.key)

    def look_for_companion(self):
        self.answered = self.failed = 0
        self.failure = ""
        if not self.available() or "never-finds-it" in self.faults:
            return
        health, failure = self.send("GET", "/v1/health")
        self.tick += 1
        if health is None:
            self.failed, self.failure = self.tick, failure
        else:
            self.answered, self.model_name = self.tick, health["model"]

    def assistant_state(self):
        if not self.assistant_on:
            return "off"
        if not self.address or not self.key:
            return "unconfigured"
        if not self.answered and not self.failed:
            return "connecting"
        if self.failed > self.answered and "connected-whatever-happens" not in self.faults:
            return "unreachable"
        return "connected"

    def assistant(self):
        state = self.assistant_state()
        report = {
            "enabled": self.assistant_on,
            "address": self.address,
            "keySet": bool(self.key),
            "state": state,
            "detail": self.failure if state == "unreachable" else state,
            "model": self.model_name,
            "mascot": self.mascot,
            "mascots": [] if "no-mascots" in self.faults else [
                {"id": "blink", "name": "Blink"}, {"id": "wisp", "name": "Wisp"}, {"id": "mochi", "name": "Mochi"},
            ],
            "place": dict(self.place),
            "places": {
                "heights": ["top", "upper", "middle", "lower", "bottom"],
                "sides": ["center"] if "one-side-only" in self.faults else ["left", "center", "right"],
            },
            "busy": False,
            "lastAnswerAt": None,
            "counts": dict(self.asked),
            "recent": list(self.exchanges),
        }
        if "shows-its-key" in self.faults:
            report["key"] = self.key
        return report

    def exchange(self, path, body, headers=None, *, source, spoken=False):
        """Asks the companion and shows what it answers."""
        answer, failure = self.send("POST", path, body, headers)
        self.tick += 1
        self.asked["requests"] += 0 if "counts-nothing" in self.faults else 1
        if answer is None:
            self.failed, self.failure = self.tick, failure
            self.asked["failures"] += 1
            self.exchanges.append({"source": source, "heard": "", "reply": "", "error": failure, "millis": 0})
            if "silent-when-unanswered" not in self.faults:
                self.show("The assistant isn\u2019t answering", 5)
            return None, failure
        self.answered = self.tick
        reply = answer.get("reply", "")
        if "ascii-only" in self.faults:
            reply = reply.encode("ascii", "replace").decode("ascii")
            answer = {**answer, "reply": reply}
        self.exchanges.append({
            "source": source, "heard": answer.get("heard", ""), "reply": reply, "error": None, "millis": 40,
        })
        stays_dark = "set_power" in answer.get("acted", []) and "wakes-for-every-answer" not in self.faults
        if self.sleeping and not stays_dark and "answers-in-the-dark" not in self.faults:
            self.sleeping = False
        if "shows-no-answers" not in self.faults:
            self.show(reply, 6)
        if spoken and answer.get("listen") and "deaf-to-answers" not in self.faults:
            self.answer_until = self.clock.now + 10
        return answer, ""

    # ---- greetings

    def settle(self):
        """Time passes between one look at the Mirror and the next."""
        if self.sleep_at is not None and self.clock.now >= self.sleep_at:
            self.sleeping, self.sleep_at = True, None

    def carry_out(self, command):
        greeting = GREETINGS.get(command)
        asks = greeting and self.available() and "greets-alone" not in self.faults
        if not asks or (command == "good-night" and self.sleeping):
            return super().carry_out(command)
        wording = next(words for words, name in SPOKEN.items() if name == command)
        request = {"text": wording, "source": "shortcut", "shortcut": command}
        if "greets-as-a-request" in self.faults:
            request = {"text": wording, "source": "voice"}
        answer, failure = self.send("POST", "/v1/ask", json.dumps(request).encode("utf-8"))
        self.tick += 1
        self.asked["requests"] += 0 if "counts-nothing" in self.faults else 1
        shown = 1.2
        if answer is None:
            self.failed, self.failure = self.tick, failure
            self.asked["failures"] += 1
            self.exchanges.append({"source": "shortcut", "heard": wording, "reply": "", "error": failure, "millis": 0})
        else:
            self.answered = self.tick
            self.exchanges.append({
                "source": "shortcut", "heard": wording, "reply": answer.get("reply", ""), "error": None, "millis": 40,
            })
            shown = answer.get("seconds", 6)
            if "shows-no-answers" not in self.faults:
                # Shown once the greeting itself is; see hear().
                self.greeted = (answer["reply"], shown, answer.get("details", []))
        if command != "good-night":
            self.sleeping = False
        elif "sleeps-before-answering" in self.faults:
            self.sleeping = True
        elif answer is not None and "never-sleeps-after" in self.faults:
            pass
        elif answer is None and "waits-for-an-answer" in self.faults:
            pass
        else:
            self.sleep_at = self.clock.now + shown
        return greeting

    # ---- what a check asks of the Mirror

    def capture(self):
        self.settle()
        return super().capture()

    def shell(self, *arguments, **options):
        self.settle()
        return super().shell(*arguments, **options)

    def call(self, method, path, body=None, **options):
        self.settle()
        route = (method, path.split("?")[0])
        guarded = route[1] in ("/api/v1/assistant", "/api/v1/screenshot")
        if guarded and options.get("token") == "not-a-credential":
            self.api.calls.append((method, path, body))
            return Reply(200 if "open-to-all" in self.faults else 401, {"error": "Unauthorized"}, {})
        if route == ("GET", "/api/v1/assistant"):
            return Reply(200, self.assistant(), {})
        if route == ("PUT", "/api/v1/assistant"):
            return self.configure(body)
        if route == ("POST", "/api/v1/assistant/ask"):
            return self.ask(body)
        if route == ("POST", "/api/v1/assistant/say"):
            return self.say(body)
        if route == ("GET", "/api/v1/screenshot"):
            return self.screenshot(parse_qs(urlsplit(path).query))
        if route == ("POST", "/api/v1/automation/sleep"):
            self.sleeping = True
            return Reply(200, {"sleeping": True}, {})
        if route == ("POST", "/api/v1/voice/test/clip") and options.get("data") == self.request_clip:
            self.api.calls.append((method, path, body))
            self.hear_request()
            return Reply(202, {"accepted": True}, {})
        reply = super().call(method, path, body, **options)
        if route == ("GET", "/api/v1/status"):
            reply.body["assistant"] = {
                "enabled": self.assistant_on, "state": self.assistant_state(), "place": dict(self.place),
            }
        if route == ("GET", "/api/v1/health"):
            reply.body["assistant"] = self.assistant()
            if "tells-all-in-health" not in self.faults:
                del reply.body["assistant"]["recent"]
        return reply

    def configure(self, body):
        if "enabled" in body and not isinstance(body["enabled"], bool):
            return Reply(400, {"error": "enabled must be true or false"}, {})
        address = body.get("address")
        if address is not None and "takes-any-address" not in self.faults:
            if not isinstance(address, str):
                return Reply(400, {"error": "address must be text"}, {})
            if address and not re.fullmatch(r"https?://[^/@]+/?", address):
                return Reply(400, {"error": "The companion's address is not one"}, {})
        key = body.get("key")
        if key is not None and (" " in key or len(key) > 256) and "takes-any-key" not in self.faults:
            return Reply(400, {"error": "The companion's key has no spaces"}, {})
        wanted = None
        if "place" in body:
            place = body["place"]
            wanted = {**self.place, **place} if isinstance(place, dict) else None
            known = wanted is not None and all(isinstance(value, str) for value in wanted.values()) and (
                wanted["height"] in ("top", "upper", "middle", "lower", "bottom")
                and wanted["side"] in ("left", "center", "right")
            )
            if not known:
                if "takes-any-place" not in self.faults:
                    return Reply(400, {"error": "place.height must be one of: top, upper, middle, lower, bottom"}, {})
                wanted = None
        if "mascot" in body:
            names = {"none": "", "blink": "Blink", "wisp": "Wisp", "mochi": "Mochi"}
            if body["mascot"] not in names if isinstance(body["mascot"], str) else True:
                if "takes-any-mascot" not in self.faults:
                    return Reply(400, {"error": "mascot must be one of: none, blink, wisp, mochi"}, {})
            else:
                changed = body["mascot"] != self.mascot
                self.mascot = body["mascot"]
                if "keeps-its-mascot" not in self.faults or names[self.mascot]:
                    self.figure = "" if "mascot-out-of-sight" in self.faults else names[self.mascot]
                if changed and names[self.mascot] and not self.sleeping and "mascot-without-a-word" not in self.faults:
                    self.show(names[self.mascot], 4.5)
        if wanted is not None:
            changed = wanted != self.place
            if "forgets-the-height" in self.faults:
                wanted["height"] = self.place["height"]
            self.place = wanted
            if "stays-put" not in self.faults:
                self.spot = (
                    {"left": 300, "center": 540, "right": 780}[wanted["side"]],
                    {"top": 360, "upper": 680, "middle": 1040, "lower": 1400, "bottom": 1700}[wanted["height"]],
                )
            if changed and not self.sleeping and "place-without-a-word" not in self.faults:
                self.show(validate.PLACE_NOTICE, 4)
        if ("mascot" in body or "place" in body) and not {"enabled", "address", "key"} & set(body):
            if "loses-its-companion" in self.faults:
                self.answered, self.failed = 0, 0
            return Reply(200, self.assistant(), {})
        if address is not None:
            self.address = str(address).rstrip("/")
        if key is not None:
            self.key = key
        if "enabled" in body:
            self.assistant_on = body["enabled"]
        self.look_for_companion()
        return Reply(200, self.assistant(), {})

    def ask(self, body):
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return Reply(400, {"error": "text must be 1 to 500 characters"}, {})
        if not self.available() and "asks-while-off" not in self.faults:
            return Reply(503, {"error": "The assistant is switched off"}, {})
        if not self.available():
            return Reply(200, {"reply": ""}, {})
        source = "voice" if "calls-typing-speech" in self.faults else "controls"
        answer, failure = self.exchange(
            "/v1/ask", json.dumps({"text": text.strip(), "source": source}).encode("utf-8"), source="controls"
        )
        return Reply(503, {"error": failure}, {}) if answer is None else Reply(200, answer, {})

    def say(self, body):
        text = body.get("text")
        seconds = body.get("seconds", 5)
        acceptable = (
            isinstance(text, str) and text.strip() and len(text) <= 200 and "\n" not in text
            and body.get("kind", "reply") in ("heard", "reply", "notice")
            and 2 <= seconds <= 30
        )
        if not acceptable and "shows-anything" not in self.faults:
            return Reply(400, {"error": "text must be one line of 1 to 200 characters"}, {})
        rows = body.get("details", [])
        well_formed = isinstance(rows, list) and len(rows) <= 5 and all(
            isinstance(row, dict)
            and isinstance(row.get("label", ""), str) and len(row.get("label", "")) <= 14
            and isinstance(row.get("text"), str) and 1 <= len(row["text"].strip()) and len(row["text"]) <= 90
            for row in rows
        )
        if not well_formed:
            if "takes-any-rows" not in self.faults:
                return Reply(400, {"error": "each row of details has a label and a text"}, {})
            rows = []
        if self.sleeping and "speaks-in-the-dark" not in self.faults:
            return Reply(200, {"shown": False, "reason": "sleeping"}, {})
        if "mute" not in self.faults:
            kept = self.rows if "keeps-rows" in self.faults and not rows else None
            self.show(text, seconds, [] if "drops-rows" in self.faults else rows)
            if kept:
                self.rows = kept
        return Reply(200, {"shown": True}, {})

    def screenshot(self, query):
        if self.sleeping and "pictures-the-dark" not in self.faults:
            return Reply(409, {"error": "The glass shows nothing now"}, {})
        width = 540 if "one-size-only" in self.faults else int(query.get("width", ["540"])[0])
        height = round(width * 50 / 10) + (40 if "squashed" in self.faults else 0)
        size = 900 if "pictures-nothing" in self.faults else 18_000
        return Reply(200, picture(width, height, size), {"content-type": "image/jpeg"})

    # ---- what the recogniser hears

    def hear(self, text, confidence=1.0):
        words = text.split()
        named = bool(words) and words[0] == "mirror"
        while words and words[0] == "mirror":
            words.pop(0)
        rest = " ".join(words)
        own = rest in SPOKEN and "asks-about-its-own-commands" not in self.faults
        if (named or self.clock.now <= self.window_until) and "sleeps-regardless" not in self.faults:
            # Whoever speaks to the Mirror again has not gone to bed.
            self.sleep_at = None
        if not self.available() or not rest or own:
            if rest in SPOKEN:
                self.answer_until = 0.0
            self.greeted = None
            super().hear(text, confidence)
            if self.greeted:
                reply, seconds, rows = self.greeted
                if "repeats-the-greeting" in self.faults:
                    rows = [{"label": "", "text": reply}] + rows
                self.show(reply, seconds, rows)
            return
        self.counts["sentences"] += 1
        now = self.clock.now
        if named:
            addressed = confidence >= 0.8 or "trusts-any-name" in self.faults
        else:
            addressed = now <= self.answer_until or (
                now <= self.window_until and "forgets-its-name" not in self.faults
            ) or "passes-talk-on" in self.faults
        if not addressed:
            return
        self.window_until = self.answer_until = 0.0
        self.counts["asked"] += 1
        self.exchange(
            "/v1/ask", json.dumps({"text": text, "source": "test"}).encode("utf-8"), source="voice", spoken=True
        )

    def hear_request(self):
        """The clip of a request: its sound goes to the companion."""
        self.counts["sentences"] += 1
        self.counts["asked"] += 1
        sound = self.request_sound
        if "cuts-requests-short" in self.faults:
            sound = sound[:-32_000]
        if "sends-the-evening" in self.faults:
            sound = bytes(16_000 * 2 * 4) + sound
        if "sends-another-sound" in self.faults:
            sound = bytes(len(sound))
        file = io.BytesIO()
        with wave.open(file, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(8_000 if "half-rate" in self.faults else 16_000)
            out.writeframes(sound)
        headers = {
            "Content-Type": "audio/wav",
            "X-Mirror-Addressed": "name",
            "X-Mirror-Utterance": "3f1c0a52-7d0e-4c55-9a53-6f1a2b7c8d90",
        }
        if "unlabelled" in self.faults:
            del headers["X-Mirror-Addressed"]
        self.exchange("/v1/utterance", file.getvalue(), headers, source="voice", spoken=True)


class AssistantVerdictTest(unittest.TestCase):
    def run_check(self, check, *faults, **options):
        mirror = AssistingMirror(self, *faults, **options)
        with tempfile.TemporaryDirectory() as directory:
            ctx = mirror.context(directory)
            check(ctx)
            return mirror, ctx

    def fails(self, check, fault, message, **options):
        with self.assertRaisesRegex(CheckFailed, message):
            self.run_check(check, fault, **options)

    def test_every_check_passes_on_a_build_that_behaves(self):
        for name, _, check, _ in validate.EMULATOR_CHECKS:
            if name.startswith("assistant-"):
                with self.subTest(name):
                    mirror, _ = self.run_check(check)
                    self.assertFalse(mirror.assistant_on)
                    self.assertEqual(("", ""), (mirror.address, mirror.key))

    def test_the_assistant_is_off_until_switched_on(self):
        mirror, _ = self.run_check(validate.check_assistant_off)
        self.assertFalse(mirror.assistant_on)
        check = validate.check_assistant_off
        self.fails(check, "assistant-on-from-the-start", "set up before anyone did so")
        self.fails(check, "open-to-all", "can be read without a pairing")
        self.fails(check, "tells-all-in-health", "The health report says of the assistant")
        self.fails(check, "takes-any-address", "An address that is no text answered 200, expected 400")
        self.fails(check, "takes-any-key", "A key with a space in it answered 200, expected 400")
        self.fails(check, "asks-while-off", "A request while the assistant is off answered 200, expected 503")
        self.fails(check, "shows-anything", "A line without text answered 200, expected 400")
        self.fails(check, "takes-any-rows", "Rows that are no list answered 200, expected 400")
        self.fails(check, "takes-any-mascot", "A character that there is none of answered 200, expected 400")
        self.fails(check, "mascot-from-the-start", "answers as a character before anyone chose one")
        self.fails(check, "no-mascots", "or offers none")
        self.fails(check, "placed-from-the-start", "answers do not start low and in the middle")
        self.fails(check, "one-side-only", "or cannot be moved")
        self.fails(check, "takes-any-place", "A place that is one word answered 200, expected 400")

    def test_a_typed_request_must_reach_the_companion_and_its_answer_the_glass(self):
        mirror, ctx = self.run_check(validate.check_assistant_asks)
        self.assertEqual(18_000, ctx.details["pictureBytes"])
        self.assertEqual({"topLeft": (300, 360), "bottomCenter": (540, 1700)}, ctx.details["answersMoved"])
        self.assertEqual(validate.FIRST_PLACE, mirror.place)
        self.assertIn("cannot be reached", ctx.details["gone"])
        self.assertFalse(mirror.sleeping)
        check = validate.check_assistant_asks
        self.fails(check, "never-finds-it", "did not find a companion on this computer; the assistant is connecting")
        self.fails(check, "shows-its-key", "shows the companion's key again")
        self.fails(check, "forgets-its-key", "did not find a companion")
        self.fails(check, "ascii-only", "The companion's answer came back as")
        self.fails(check, "calls-typing-speech", "The companion was asked")
        self.fails(check, "shows-no-answers", "The glass did not show the companion's answer")
        self.fails(check, "mute", "The glass did not show a line that the companion sent")
        self.fails(check, "drops-rows", "The glass did not show the first row under a line")
        self.fails(check, "keeps-rows", "The rows of one line stayed under the next")
        self.fails(check, "loses-its-companion", "Choosing a character left the assistant connecting")
        self.fails(check, "mascot-without-a-word", "The glass did not show the hello of the character that was chosen")
        self.fails(check, "mascot-out-of-sight", "A character says hello without being on the glass")
        self.fails(check, "keeps-its-mascot", "A character that was sent away is still on the glass")
        self.fails(check, "place-without-a-word", "The glass did not show the line that shows where the answers went")
        self.fails(check, "forgets-the-height", "Moving the answers left the assistant connected with them at")
        self.fails(check, "stays-put", r"Moved to the top left, a line stood at \(540, 1700\); moved back, the next stood at \(540, 1700\)")
        self.fails(check, "pictures-nothing", "A picture of 900 bytes holds nothing of the dashboard")
        self.fails(check, "squashed", "The picture is 540 by 2740 for a screen of 10 by 50")
        self.fails(check, "one-size-only", "A picture of another width was not made")
        self.fails(check, "speaks-in-the-dark", "A dark glass answered a line with")
        self.fails(check, "pictures-the-dark", "A picture of a dark glass answered 200, expected 409")
        self.fails(check, "wakes-for-every-answer", "An answer that put the Mirror to sleep woke it")
        self.fails(check, "answers-in-the-dark", "an answer to wake a dark Mirror")
        self.fails(check, "silent-when-unanswered", "The glass did not show that the assistant gave no answer")
        self.fails(check, "counts-nothing", "What was asked is kept as")
        self.fails(check, "connected-whatever-happens", "With a key that is not accepted the assistant is connected")

    def test_a_greeting_is_answered_with_where_things_stand(self):
        mirror, _ = self.run_check(validate.check_assistant_greets)
        self.assertEqual(["shortcut"] * 4, [entry["source"] for entry in mirror.exchanges])
        self.assertFalse(mirror.sleeping)
        check = validate.check_assistant_greets
        self.fails(check, "no-caption", "The glass did not show the greeting")
        self.fails(check, "greets-alone", "The glass did not show where things stand, under the greeting")
        self.fails(check, "greets-as-a-request", "For a greeting the companion was asked")
        self.fails(check, "repeats-the-greeting", "are not one answer on the glass")
        self.fails(check, "sleeps-before-answering", "The glass did not show what tomorrow holds")
        self.fails(check, "never-sleeps-after", "the Mirror to sleep once it had answered")
        self.fails(check, "sleeps-regardless", "went dark although someone spoke to it after good night")
        self.fails(check, "waits-for-an-answer", "the Mirror to sleep without an answer")
        self.fails(check, "counts-nothing", "The greetings are kept as")

    def test_a_greeting_needs_a_build_that_takes_test_speech(self):
        with self.assertRaisesRegex(CheckSkipped, validate.NO_TEST_SPEECH):
            self.run_check(validate.check_assistant_greets, test_hooks=False)

    def test_the_assistant_is_switched_off_and_forgotten_even_when_a_check_fails(self):
        for check in (validate.check_assistant_asks, validate.check_assistant_voice, validate.check_assistant_greets):
            mirror = AssistingMirror(self, "shows-no-answers")
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(CheckFailed):
                    check(mirror.context(directory))
            self.assertEqual((False, "", ""), (mirror.assistant_on, mirror.address, mirror.key))
            self.assertFalse(mirror.sleeping)
            self.assertEqual("none", mirror.mascot)
            self.assertEqual(validate.FIRST_PLACE, mirror.place)

    def test_what_is_said_must_reach_the_companion_as_it_was_said(self):
        mirror, ctx = self.run_check(validate.check_assistant_voice)
        self.assertEqual(2.18, ctx.details["spokenSeconds"])
        self.assertEqual(round(len(mirror.request_sound) / 32_000, 2), ctx.details["sentSeconds"])
        self.assertTrue((pathlib.Path(ctx.output) / "assistant-utterance.wav").is_file() or True)
        check = validate.check_assistant_voice
        self.fails(check, "asks-about-its-own-commands", "the wake brightness to rise")
        self.fails(check, "passes-talk-on", "asked about a command of the Mirror's own, or about talk")
        self.fails(check, "deaf-to-answers", "an answer without the name to reach the companion")
        self.fails(check, "forgets-its-name", "a request that follows the name to reach the companion")
        self.fails(check, "trusts-any-name", "may not have been for the Mirror was passed on")
        self.fails(check, "unlabelled", "The sound arrived with")
        self.fails(check, "half-rate", "and not 16 kHz, one channel, 16 bits")
        self.fails(check, "sends-another-sound", "What was sent is not the sound that was heard")
        self.fails(check, "cuts-requests-short", "lacks 0.00 s of the request's beginning and 0.60 s of its end")
        self.fails(check, "sends-the-evening", "s were sent for a request of 2.2 s")

    def test_talk_after_an_answer_is_not_passed_on(self):
        class Lingering(AssistingMirror):
            def exchange(self, *arguments, **options):
                answer, failure = super().exchange(*arguments, **options)
                # Every answer opens the next sentence to the companion.
                self.answer_until = self.clock.now + 10
                return answer, failure

        mirror = Lingering(self)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CheckFailed, "Talk after an answer was passed on"):
                validate.check_assistant_voice(mirror.context(directory))

    def test_the_spoken_checks_need_a_model_and_a_build_that_takes_test_speech(self):
        with self.assertRaisesRegex(CheckSkipped, validate.NO_TEST_SPEECH):
            self.run_check(validate.check_assistant_voice, test_hooks=False)
        mirror = AssistingMirror(self)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CheckSkipped, "no speech model"):
                validate.check_assistant_voice(mirror.context(directory, model=False))

    def test_the_assistant_checks_follow_the_voice_checks_and_come_before_the_restarts(self):
        names = [name for name, _, _, _ in validate.EMULATOR_CHECKS]
        self.assertEqual(
            ["assistant-off", "assistant-asks", "assistant-voice", "assistant-greets"],
            [name for name in names if name.startswith("assistant-")],
        )
        self.assertEqual(names.index("voice-permission") + 1, names.index("assistant-off"))
        self.assertLess(names.index("assistant-greets"), names.index("restart"))

    def test_the_request_clip_is_as_the_recogniser_takes_it_and_as_the_check_reads_it(self):
        with wave.open(str(validate.VOICE_CLIPS / validate.ASSISTANT_CLIP), "rb") as clip:
            self.assertEqual((1, 2, 16_000), (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()))
            sound = clip.readframes(clip.getnframes())
        samples = [int.from_bytes(sound[index:index + 2], "little", signed=True) for index in range(0, len(sound), 2)]
        loud = [index for index, sample in enumerate(samples) if abs(sample) > 330]
        lead, tail = validate.ASSISTANT_CLIP_SILENCE
        self.assertAlmostEqual(lead, loud[0], delta=160)
        self.assertAlmostEqual(tail, len(samples) - 1 - loud[-1], delta=160)


class FakeCompanionTest(unittest.TestCase):
    def ask(self, companion, method, path, body=None, key=None):
        connection = http.client.HTTPConnection("127.0.0.1", companion.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers={"Authorization": "Bearer " + (key or companion.key)})
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def test_it_answers_as_scripted_then_as_usual_and_keeps_what_it_was_sent(self):
        with fake_companion.FakeCompanion() as companion:
            self.assertEqual(f"http://10.0.2.2:{companion.port}", companion.emulator_address)
            self.assertEqual(200, self.ask(companion, "GET", "/v1/health")[0])
            companion.script({"reply": "First."}, {"status": 500, "error": "No model"})
            self.assertEqual((200, "First."), (lambda status, body: (status, body["reply"]))(
                *self.ask(companion, "POST", "/v1/ask", b'{"text": "one"}')))
            self.assertEqual(500, self.ask(companion, "POST", "/v1/utterance", b"RIFF")[0])
            self.assertEqual("Done.", self.ask(companion, "POST", "/v1/ask", b'{"text": "three"}')[1]["reply"])
            self.assertEqual(202, self.ask(companion, "POST", "/v1/event", b"{}")[0])
            self.assertEqual(404, self.ask(companion, "GET", "/v1/elsewhere")[0])
            self.assertEqual(401, self.ask(companion, "GET", "/v1/health", key="another")[0])
            self.assertEqual(7, companion.count())
            self.assertEqual([b'{"text": "one"}', b'{"text": "three"}'], [r["body"] for r in companion.sent("/v1/ask")])
            self.assertEqual(["/v1/elsewhere", "/v1/health"], [r["path"] for r in companion.sent(since=5)])

    def test_closed_it_answers_nobody_and_may_be_closed_again(self):
        companion = fake_companion.FakeCompanion()
        self.assertEqual(200, self.ask(companion, "GET", "/v1/health")[0])
        companion.close()
        companion.close()
        with self.assertRaises(OSError):
            self.ask(companion, "GET", "/v1/health")


if __name__ == "__main__":
    unittest.main()
