import assert from "node:assert/strict";
import test from "node:test";
import { writeWav } from "../src/wav.js";
import { until } from "./fakes/clock.js";
import { speech } from "./fakes/stt.js";
import { startCompanion } from "./helpers.js";

test("every route needs the secret", async (t) => {
  const { call, brain } = await startCompanion(t);
  const routes = [
    ["GET", "/v1/health"],
    ["GET", "/v1/activity"],
    ["POST", "/v1/event"],
    ["POST", "/v1/ask"],
    ["POST", "/v1/utterance"],
    ["GET", "/v1/nothing"],
  ];
  for (const [method, route] of routes) {
    for (const auth of [null, "Bearer wrong-secret-0123456789", "test-secret-0123456789", "Bearer test-secret-012345678"]) {
      const answer = await call(method, route, { auth, body: method === "POST" ? "{}" : undefined });
      assert.equal(answer.status, 401, `${method} ${route} with ${auth}`);
      assert.deepEqual(answer.body, { error: "Unauthorized" });
    }
  }
  assert.equal(brain.runs.length, 0);
});

test("an unknown route is 404 and a wrong method is 405", async (t) => {
  const { call } = await startCompanion(t);
  assert.deepEqual(await call("GET", "/v1/nothing"), { status: 404, body: { error: "There is no such route." } });
  assert.equal((await call("GET", "/")).status, 404);
  assert.equal((await call("POST", "/v1/health", { body: "{}" })).status, 405);
  assert.equal((await call("GET", "/v1/ask")).status, 405);
  assert.equal((await call("GET", "/v1/utterance")).status, 405);
});

test("health reports every part from what is known, without asking anyone", async (t) => {
  const { call, mirror, brain, stt, clock } = await startCompanion(t);
  await until(() => mirror.requests().length > 0, "the first look at the mirror");
  const seen = mirror.requests().length;
  await clock.advance(5000);
  const { status, body } = await call("GET", "/v1/health");
  assert.equal(status, 200);
  assert.deepEqual(body, {
    ok: true,
    name: "mirror-companion",
    version: "0.1.0",
    provider: "copilot-cli",
    model: "gpt-6-luna",
    brain: { ready: true, detail: "" },
    stt: { ready: true, model: "small.en", device: "cuda", detail: "" },
    mirror: { reachable: true, version: "2.3.0", detail: "" },
    busy: false,
    uptimeSeconds: 5,
    last: null,
  });
  assert.equal(mirror.requests().length, seen, "health does not call the mirror");
  assert.equal(brain.runs.length, 0);

  brain.ready = false;
  brain.detail = "The model gpt-6-luna of copilot-cli is not usable.";
  stt.device = "cpu";
  const worse = (await call("GET", "/v1/health")).body;
  assert.equal(worse.ok, false);
  assert.deepEqual(worse.brain, { ready: false, detail: "The model gpt-6-luna of copilot-cli is not usable." });
  assert.equal(worse.stt.device, "cpu");
});

test("health shows the last thing heard", async (t) => {
  const { call, ask } = await startCompanion(t, [{ text: "It is 7:12." }]);
  await ask("what time is it?");
  const { last } = (await call("GET", "/v1/health")).body;
  assert.deepEqual(Object.keys(last), ["at", "source", "heard", "reply", "ms"]);
  assert.deepEqual([last.source, last.heard, last.reply], ["controls", "what time is it?", "It is 7:12."]);
});

test("ask checks its text", async (t) => {
  const { call, brain } = await startCompanion(t);
  const post = (body) => call("POST", "/v1/ask", { body: typeof body === "string" ? body : JSON.stringify(body) });
  assert.deepEqual(await post({ text: "" }), { status: 400, body: { error: "text must be 1 to 500 characters." } });
  assert.equal((await post({ text: "   " })).status, 400);
  assert.equal((await post({ text: "x".repeat(501) })).status, 400);
  assert.equal((await post({})).status, 400);
  assert.equal((await post({ text: 5 })).status, 400);
  assert.equal((await post({ text: "hello", source: 5 })).status, 400);
  assert.deepEqual(await post("not json"), { status: 400, body: { error: "The request body must be JSON." } });
  assert.equal((await post("[1]")).status, 400);
  assert.equal((await post(JSON.stringify({ text: "x", pad: "y".repeat(70_000) }))).status, 413);
  assert.equal(brain.runs.length, 0);
  assert.equal((await post({ text: "x".repeat(500) })).status, 200);
});

test("ask answers like an utterance, with no hearing time and no caption of what was heard", async (t) => {
  const { ask, mirror } = await startCompanion(t, [{ calls: [{ tool: "set_power", args: { state: "asleep" } }], text: "Good night." }]);
  const { status, body } = await ask("go to sleep");
  assert.equal(status, 200);
  assert.match(body.id, /^ask-/);
  assert.deepEqual({ ...body, id: "", ms: null }, {
    id: "", heard: "go to sleep", reply: "Good night.", details: [], ignored: false, reason: "", listen: false, acted: ["set_power"], ms: null,
  });
  assert.equal(body.ms.stt, 0);
  assert.equal(mirror.state.said.length, 0);
  assert.equal(mirror.state.automation.sleeping, true);
});

test("an utterance checks its headers and its recording", async (t) => {
  const { call, say, brain } = await startCompanion(t);
  const wav = speech("go to sleep");
  const post = (headers, body = wav) => call("POST", "/v1/utterance", { body, headers });
  assert.match((await post({ "X-Mirror-Addressed": "loudly" })).body.error, /X-Mirror-Addressed must be name, window or follow-up/);
  assert.match((await post({ "X-Mirror-Utterance": "no spaces allowed" })).body.error, /X-Mirror-Utterance must be 1 to 64 letters, digits or dashes/);
  assert.equal((await post({ "X-Mirror-Utterance": "x".repeat(65) })).status, 400);
  assert.deepEqual(await say(Buffer.from("this is not a recording, it is a sentence of text")), {
    status: 400,
    body: { error: "The body is not a WAV file." },
  });
  const stereo = writeWav(new Int16Array(16000).fill(5));
  stereo.writeUInt16LE(2, 22);
  assert.match((await say(stereo)).body.error, /must be 16 kHz, mono, 16-bit PCM/);
  assert.equal(brain.runs.length, 0);
});

test("a recording over 1,500,000 bytes is refused with 413, and one just under is taken", async (t) => {
  const { say } = await startCompanion(t, [{ text: "Done." }]);
  const big = writeWav(new Int16Array(750_000).fill(1));
  assert.equal(big.length, 1_500_044);
  const refused = await say(big);
  assert.equal(refused.status, 413);
  assert.match(refused.body.error, /larger than 1,500,000 bytes/);
  const samples = new Int16Array(749_978).fill(1);
  const words = "go on";
  samples[0] = words.length;
  for (let index = 0; index < words.length; index++) samples[index + 1] = words.charCodeAt(index);
  const fits = writeWav(samples);
  assert.equal(fits.length, 1_500_000);
  assert.equal((await say(fits)).status, 200);
});

test("an utterance without the optional headers is taken as addressed by name", async (t) => {
  const { call, brain } = await startCompanion(t, [{ text: "Good morning." }]);
  const { status, body } = await call("POST", "/v1/utterance", { body: speech("Mirror, good morning") });
  assert.equal(status, 200);
  assert.match(body.id, /^u-/);
  assert.equal(body.heard, "good morning");
  assert.match(brain.runs[0].prompt, /Said to you, after your name:\n"good morning"/);
});

test("events are accepted at once, unknown ones too", async (t) => {
  const { call, brain } = await startCompanion(t);
  const post = (body) => call("POST", "/v1/event", { body: JSON.stringify(body) });
  assert.deepEqual(await post({ type: "started", at: 1790990000000 }), { status: 202, body: { accepted: true } });
  assert.deepEqual(await post({ type: "something-new", at: 1790990000000, extra: 1 }), { status: 202, body: { accepted: true } });
  assert.deepEqual(await post({ type: "presence", at: 1790990000000, asleepSeconds: 5 }), { status: 202, body: { accepted: true } });
  assert.equal((await post({ at: 1790990000000 })).status, 400);
  assert.equal((await post({ type: "" })).status, 400);
  assert.equal(brain.runs.length, 0);
});

test("activity lists exchanges newest first, as many as asked for", async (t) => {
  const { call, ask, say } = await startCompanion(t, [
    { text: "It is 7:12." },
    { calls: [{ tool: "ignore", args: { reason: "talk" } }] },
    { calls: [{ tool: "ignore", args: { reason: "talk" } }] },
    { calls: [{ tool: "set_power", args: { state: "asleep" } }], text: "Good night." },
  ]);
  await ask("what time is it?");
  await say(speech("so I told her we should go"));
  await ask("go to sleep", "test");
  const { status, body } = await call("GET", "/v1/activity");
  assert.equal(status, 200);
  assert.deepEqual(
    body.entries.map((entry) => [entry.source, entry.heard, entry.reply, entry.acted, entry.ignored, entry.reason]),
    [
      ["test", "go to sleep", "Good night.", ["set_power"], false, ""],
      ["voice", "so I told her we should go", "", ["ignore", "ignore"], true, "not-addressed"],
      ["controls", "what time is it?", "It is 7:12.", [], false, ""],
    ],
  );
  assert.deepEqual(Object.keys(body.entries[0]), ["at", "source", "heard", "reply", "acted", "ignored", "reason", "ms"]);
  assert.equal((await call("GET", "/v1/activity?limit=1")).body.entries.length, 1);
  assert.equal((await call("GET", "/v1/activity?limit=0")).status, 400);
  assert.equal((await call("GET", "/v1/activity?limit=many")).status, 400);
});
