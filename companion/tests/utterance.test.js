import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { BrainError } from "../src/brain.js";
import { withoutName } from "../src/assistant.js";
import { writeWav } from "../src/wav.js";
import { until } from "./fakes/clock.js";
import { speech } from "./fakes/stt.js";
import { SECRET, startCompanion } from "./helpers.js";

const sleep = { calls: [{ tool: "set_power", args: { state: "asleep" } }], text: "Good night." };

test("an utterance is heard, shown as heard, acted on and answered", async (t) => {
  const { say, mirror, brain, stt, clock } = await startCompanion(t, [sleep]);
  stt.takesMs = 1400;
  const pending = say(speech("Mirror, go to sleep."), { id: "utt-42" });
  await until(() => stt.heard === 1, "the transcription to start");
  await clock.advance(1400);
  const { status, body } = await pending;
  assert.equal(status, 200);
  assert.deepEqual(body, {
    id: "utt-42",
    heard: "go to sleep.",
    reply: "Good night.",
    details: [],
    ignored: false,
    reason: "",
    listen: false,
    acted: ["set_power"],
    ms: { stt: 1400, agent: 0, total: 1400 },
  });
  assert.equal(mirror.state.automation.sleeping, true);
  // What was understood is shown as "heard". The final reply is not sent with
  // say: the mirror shows the reply of the answer itself.
  await until(() => mirror.state.said.length === 1, "the caption of what was heard");
  assert.deepEqual(mirror.state.said, [{ text: "go to sleep.", kind: "heard", seconds: null, shown: true }]);
  assert.equal(brain.runs.length, 1);
  assert.equal(brain.runs[0].session, "conversation");
});

test("the model is told what was said and how the mirror stands, in the mirror's time", async (t) => {
  const { say, brain } = await startCompanion(t, [{ text: "Noted." }]);
  await say(speech("Mirror, remind me to take out the trash at seven tomorrow"));
  const { prompt, system, tools } = brain.runs[0];
  assert.match(prompt, /^Said to you, after your name:\n"remind me to take out the trash at seven tomorrow"\n\nThe mirror now:\n\{/);
  const snapshot = JSON.parse(prompt.slice(prompt.indexOf("{")));
  assert.equal(snapshot.now.local, "2026-10-03T07:12:00-07:00");
  assert.equal(snapshot.now.weekday, "Saturday");
  assert.equal(snapshot.now.utcOffset, "-07:00");
  assert.equal(snapshot.widgets.length, 16);
  assert.match(system, /You are the mirror/);
  assert.match(system, /never more than 200/);
  assert.match(system, /call ignore/);
  assert.match(system, /end with a question mark/);
  assert.match(system, /"palmer" is "calmer"/);
  assert.match(system, /Never say you did something that a tool refused/);
  assert.match(system, /What you remember about this household\n- Nothing yet\./);
  assert.match(system, /When you are unsure whether words were meant for you, they were/);
  assert.equal(tools.length, 14);
  assert.ok(!tools.includes("say"), "the answer is the line; there is no second way to show one");
});

test("a due time worked out by the model lands on the board at the right instant", async (t) => {
  const { say, mirror } = await startCompanion(t, [
    {
      calls: [{ tool: "board_add", args: { kind: "reminder", title: "Take out the trash", due: "2026-10-04T07:00:00-07:00" } }],
      text: (results) => `I'll remind you tomorrow at 7. (${results[0].added.due})`,
    },
  ]);
  const { body } = await say(speech("Mirror, remind me to take out the trash at seven tomorrow"));
  assert.equal(body.reply, "I'll remind you tomorrow at 7. (2026-10-04T07:00:00-07:00)");
  assert.deepEqual(body.acted, ["board_add"]);
  assert.equal(mirror.state.board.items[0].due, Date.UTC(2026, 9, 4, 14, 0, 0));
  assert.equal(mirror.widget("board").visible, true, "the board is put on the glass for the person who asked");
});

test("the name is taken off the start once, however it was written", () => {
  assert.equal(withoutName("Mirror, go to sleep."), "go to sleep.");
  assert.equal(withoutName("Hey Mirror. What's on my list?"), "What's on my list?");
  assert.equal(withoutName("ok mirror brighter"), "brighter");
  assert.equal(withoutName("Okay, Mirror, brighter"), "brighter");
  assert.equal(withoutName("Mira, good morning"), "good morning");
  assert.equal(withoutName("Mirra: good morning"), "good morning");
  assert.equal(withoutName("mirror mirror on the wall"), "mirror on the wall");
  assert.equal(withoutName("Mirrors are useful"), "Mirrors are useful");
  assert.equal(withoutName("The mirror in the hall needs cleaning"), "The mirror in the hall needs cleaning");
  assert.equal(withoutName("Mirror."), "");
});

test("with the name heard by the mirror, a transcript without it goes ahead all the same", async (t) => {
  const { say, brain } = await startCompanion(t, [{ text: "Brighter." }]);
  const { body } = await say(speech("Miro, make it brighter"));
  assert.equal(body.heard, "Miro, make it brighter");
  assert.equal(body.ignored, false);
  assert.equal(brain.runs.length, 1);
  assert.match(brain.runs[0].prompt, /^Your name was heard, and then:\n"Miro, make it brighter"/);
});

test("after a pause or in answer to a question the words are taken as they are", async (t) => {
  const { say, brain } = await startCompanion(t, [{ text: "Which one?" }, { text: "Mirror it is." }]);
  assert.equal((await say(speech("mirror the film please"), { addressed: "window" })).body.heard, "mirror the film please");
  assert.match(brain.runs[0].prompt, /^Said a moment after your name:\n"mirror the film please"/);
  assert.equal((await say(speech("Mirror"), { addressed: "follow-up", id: "utt-2" })).body.heard, "Mirror");
  assert.match(brain.runs[1].prompt, /^You asked: "Which one\?"\nHeard in answer:\n"Mirror"\nIf this is plainly not an answer to you, call ignore\./);
});

test("nothing intelligible is ignored without asking the model", async (t) => {
  const { say, call, brain, mirror, stt } = await startCompanion(t);
  for (const words of ["Mirror.", "Mirror, a", "", "[BLANK_AUDIO]", "Mirror (music)", "..."]) {
    const { status, body } = await say(speech(words));
    assert.equal(status, 200);
    assert.deepEqual([body.ignored, body.reason, body.reply, body.listen], [true, "nothing-heard", "", false], `for "${words}"`);
  }
  assert.equal(stt.heard, 6);
  // Digital silence and a recording too short to hold a word do not even reach speech-to-text.
  assert.equal((await say(writeWav(new Int16Array(16000)))).body.reason, "nothing-heard");
  assert.equal((await say(writeWav(new Int16Array(2000).fill(9)))).body.reason, "nothing-heard");
  assert.equal(stt.heard, 6);
  assert.equal(brain.runs.length, 0);
  assert.equal(mirror.state.said.length, 0);
  assert.equal((await call("GET", "/v1/activity")).body.entries.length, 8);
});

test("words not meant for the mirror are ignored by the model's decision", async (t) => {
  const { say, mirror, brain } = await startCompanion(t, [
    { calls: [{ tool: "ignore", args: { reason: "a remark about a mirror, not a request" } }, { tool: "set_power", args: { state: "asleep" } }] },
    { calls: [{ tool: "ignore", args: { reason: "still a remark about a mirror" } }] },
  ]);
  const { status, body } = await say(speech("The mirror in the hall needs cleaning before the guests arrive."));
  assert.equal(status, 200);
  assert.deepEqual(body, {
    id: "utt-1",
    heard: "The mirror in the hall needs cleaning before the guests arrive.",
    reply: "",
    details: [],
    ignored: true,
    reason: "not-addressed",
    listen: false,
    acted: ["ignore", "ignore"],
    ms: body.ms,
  });
  assert.equal(mirror.state.automation.sleeping, false, "ignore ends the turn");
  assert.match(brain.runs[1].prompt, /^Weigh those words once more/, "it is only dropped at a second look");
  assert.equal(brain.runs[1].fresh, false);
  assert.equal(brain.ended, 1, "overheard talk does not leave a conversation open");
});

test("a question sets listen, and only a question", async (t) => {
  const { ask } = await startCompanion(t, [{ text: "Which film: the flowers or the seasons?" }, { text: "The flowers are on." }]);
  const first = (await ask("change the film")).body;
  assert.deepEqual([first.reply, first.listen], ["Which film: the flowers or the seasons?", true]);
  assert.equal((await ask("the flowers")).body.listen, false);
});

test("a long or many-lined answer is made to fit the glass", async (t) => {
  const { ask } = await startCompanion(t, [
    { text: "**Here is your list:**\n- milk\n- eggs\n" + "and more ".repeat(40) },
    { calls: [{ tool: "set_power", args: { state: "awake" } }], text: "  " },
    // Typed words that get no answer are put to the model once more.
    { text: "" },
    { text: "" },
  ]);
  const long = (await ask("what is on my list?")).body.reply;
  assert.ok(long.length <= 200);
  assert.ok(!long.includes("\n") && !long.includes("*"));
  assert.match(long, /^Here is your list: - milk - eggs and more/);
  assert.equal((await ask("wake up")).body.reply, "Done.", "an act without words is still confirmed");
  assert.equal((await ask("hm")).body.reply, "I have no answer to that.");
});

test("words that were surely for the mirror cannot be passed over as talk: the model is asked once more", async (t) => {
  const passedOver = { calls: [{ tool: "ignore", args: { reason: "sounds like talk" } }], text: "" };
  const { ask, say, brain } = await startCompanion(t, [
    passedOver, { text: "Two are due tomorrow." },
    passedOver, { text: "The alarm at 6:30 and the trash at 7." },
    passedOver, passedOver,
    passedOver, passedOver,
    passedOver, { calls: [{ tool: "set_brightness", args: { change: "brighter" } }], text: "A step brighter." },
  ]);
  // Typed in the controls.
  const typed = (await ask("Which reminders are due tomorrow?")).body;
  assert.deepEqual([typed.ignored, typed.reply], [false, "Two are due tomorrow."]);
  assert.equal(brain.runs.length, 2);
  assert.match(brain.runs[0].results[0].error, /meant for you/);
  assert.match(brain.runs[1].prompt, /^Those words were addressed to you/);
  assert.equal(brain.runs[1].fresh, false, "the same conversation, which holds the words");
  // Heard by the mirror after its name, and transcribed with the name in front.
  const named = (await say(speech("Mirror, which reminders are due tomorrow?"))).body;
  assert.deepEqual([named.ignored, named.reply], [false, "The alarm at 6:30 and the trash at 7."]);
  assert.equal(brain.runs.length, 4);
  // Without the name in the transcript it may have been talk, as may what followed the name after a pause.
  const unnamed = (await say(speech("Which reminders are due tomorrow?"))).body;
  assert.deepEqual([unnamed.ignored, unnamed.reason, unnamed.reply], [true, "not-addressed", ""]);
  const later = (await say(speech("Which reminders are due tomorrow?"), { addressed: "window" })).body;
  assert.equal(later.ignored, true);
  assert.equal(brain.runs.length, 8, "each was looked at twice before it was dropped");
  // Passed over at first and taken up at the second look.
  const rescued = (await say(speech("Make it a bit brighter."), { addressed: "window" })).body;
  assert.deepEqual([rescued.ignored, rescued.reason, rescued.reply], [false, "", "A step brighter."]);
  assert.deepEqual(rescued.acted, ["ignore", "set_brightness"]);
});

test("a third request while two are in the house is answered 429", async (t) => {
  let release;
  const held = new Promise((resolve) => (release = resolve));
  const { ask, say, call, brain } = await startCompanion(t, [{ before: () => held, text: "First." }, { text: "Second." }]);
  const first = ask("first");
  await until(() => brain.runs.length === 1, "the first request to reach the model");
  assert.equal((await call("GET", "/v1/health")).body.busy, true);
  const second = say(speech("Mirror, second"));
  await until(() => brain.prepared.length + brain.runs.length >= 1 && brain.runs.length === 1);
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.deepEqual(await ask("third"), { status: 429, body: { error: "Busy" } });
  assert.equal((await say(speech("Mirror, fourth"))).status, 429);
  release();
  assert.equal((await first).body.reply, "First.");
  assert.equal((await second).body.reply, "Second.");
  assert.equal((await ask("fifth")).status, 200, "there is room again");
  assert.equal((await call("GET", "/v1/health")).body.busy, false);
});

test("a request that waited reads the mirror's state again", async (t) => {
  let release;
  const held = new Promise((resolve) => (release = resolve));
  const { ask, brain } = await startCompanion(t, [
    { before: () => held, calls: [{ tool: "arrange_widgets", args: { changes: [{ id: "weather", visible: false }] } }], text: "Hidden." },
    { text: "Yes." },
  ]);
  const first = ask("hide the weather");
  await until(() => brain.runs.length === 1);
  const second = ask("is the weather showing?");
  await new Promise((resolve) => setTimeout(resolve, 30));
  release();
  await Promise.all([first, second]);
  const snapshot = JSON.parse(brain.runs[1].prompt.slice(brain.runs[1].prompt.indexOf("{")));
  assert.equal(snapshot.widgets.find((widget) => widget.id === "weather").visible, false);
});

test("a model that does not answer is cut off at 30 seconds with an apology, as a 200", async (t) => {
  const { ask, brain, clock, call } = await startCompanion(t, [{ hangs: true }, { text: "Here again." }]);
  const pending = ask("are you there?");
  await until(() => brain.runs.length === 1);
  assert.equal(brain.runs[0].timeoutMs, 30_000);
  await clock.advance(29_000);
  await clock.advance(1_000);
  const { status, body } = await pending;
  assert.equal(status, 200);
  assert.equal(body.reply, "That took too long. Please try again.");
  assert.equal(body.ignored, false);
  assert.match(body.error, /^timeout: /);
  assert.equal(body.ms.agent, 30_000);
  assert.equal((await call("GET", "/v1/activity")).body.entries[0].error, body.error);
  // The next request starts over with a new conversation.
  assert.equal((await ask("hello?")).body.reply, "Here again.");
  assert.equal(brain.runs[1].fresh, true);
});

test("a harness that hangs for good still gets the mirror its answer in time", async (t) => {
  const { ask, brain, clock } = await startCompanion(t);
  brain.run = (run) => {
    brain.runs.push(run);
    return new Promise(() => {});
  };
  const pending = ask("are you there?");
  await until(() => brain.runs.length === 1);
  await clock.advance(31_500);
  const { status, body } = await pending;
  assert.equal(status, 200);
  assert.equal(body.reply, "That took too long. Please try again.");
  assert.ok(body.ms.total < 40_000);
});

test("a timeout after something was done says so", async (t) => {
  const { ask, brain, clock, mirror } = await startCompanion(t);
  brain.run = async (run) => {
    brain.runs.push(run);
    const tool = run.tools.find((candidate) => candidate.name === "set_power");
    const { runTool } = await import("../src/tools.js");
    await runTool(tool, { state: "asleep" }, run.turn);
    return new Promise((resolve, reject) => clock.setTimeout(() => reject(new BrainError("timeout", "slow")), run.timeoutMs));
  };
  const pending = ask("go to sleep and then think forever");
  await until(() => mirror.state.automation.sleeping);
  await clock.advance(30_000);
  const { body } = await pending;
  assert.equal(body.reply, "That took too long, but part of it is done.");
  assert.deepEqual(body.acted, ["set_power"]);
});

test("a tool call that comes after the answer went out does nothing", async (t) => {
  const { ask, brain, clock, mirror } = await startCompanion(t);
  let late;
  brain.run = (run) => {
    brain.runs.push(run);
    late = run;
    return new Promise((resolve, reject) => clock.setTimeout(() => reject(new BrainError("timeout", "slow")), run.timeoutMs));
  };
  const pending = ask("go to sleep");
  await until(() => brain.runs.length === 1);
  await clock.advance(30_000);
  await pending;
  const { runTool } = await import("../src/tools.js");
  const answer = await runTool(late.tools.find((tool) => tool.name === "set_power"), { state: "asleep" }, late.turn);
  assert.match(answer.error, /This request is over/);
  assert.equal(mirror.state.automation.sleeping, false);
});

test("a failing model, a model that is not ready and failing speech-to-text are apologies, not errors", async (t) => {
  const { ask, say, brain, stt } = await startCompanion(t, [{ fail: new Error("the model service answered 500") }]);
  const failed = await ask("hello");
  assert.equal(failed.status, 200);
  assert.equal(failed.body.reply, "Something went wrong on my side.");
  assert.equal(failed.body.error, "failed: the model service answered 500");
  assert.equal(failed.body.ignored, false);

  brain.ready = false;
  brain.detail = "GitHub Copilot is not usable: not logged in.";
  const notReady = await ask("hello");
  assert.equal(notReady.status, 200);
  assert.equal(notReady.body.reply, "I can't think right now. Please try again in a minute.");
  assert.match(notReady.body.error, /^not-ready: GitHub Copilot is not usable/);
  brain.ready = true;

  stt.fail = new Error("The speech-to-text worker stopped (exit code 1).");
  const unheard = await say(speech("Mirror, go to sleep"));
  assert.equal(unheard.status, 200);
  assert.equal(unheard.body.reply, "I could not hear that properly. Please say it again.");
  assert.equal(unheard.body.error, "The speech-to-text worker stopped (exit code 1).");
  assert.deepEqual([unheard.body.heard, unheard.body.ignored, unheard.body.acted], ["", false, []]);
});

test("an unreachable mirror is told to the model, which can say so", async (t) => {
  const { ask, mirror, brain } = await startCompanion(t, [
    { calls: [{ tool: "set_power", args: { state: "asleep" } }], text: (results) => (results[0].error ? "I can't reach the display right now." : "Good night.") },
  ]);
  await until(() => mirror.requests().length > 0);
  mirror.state.dropNext = 1000;
  const { status, body } = await ask("go to sleep");
  assert.equal(status, 200);
  assert.equal(body.reply, "I can't reach the display right now.");
  assert.match(brain.runs[0].prompt, /The mirror's state could not be read: the display cannot be reached right now/);
  assert.match(brain.runs[0].results[0].error, /I can't reach the display right now\. Nothing was changed/);
});

test("the last recordings are kept, and no more than asked", async (t) => {
  const { say, config, clock } = await startCompanion(t, []);
  for (let index = 1; index <= 22; index++) {
    await say(speech(`Mirror, request number ${index}`), { id: `utt-${index}` });
    await clock.advance(1000);
  }
  const names = fs.readdirSync(path.join(config.stateDir, "utterances")).sort();
  assert.equal(names.length, 20);
  assert.ok(names.at(-1).endsWith("-utt-22.wav"));
  assert.ok(!names.some((name) => name.endsWith("-utt-2.wav")));
});

test("neither the secret nor the mirror's token is ever logged", async (t) => {
  const { ask, say, call, lines, mirror, events } = await startCompanion(t, [sleep, { fail: new Error(`leaked ${SECRET}`) }]);
  await say(speech("Mirror, go to sleep"));
  await ask("hello");
  await call("GET", "/v1/health");
  await call("GET", "/v1/health", { auth: "Bearer wrong-secret-0123456789" });
  mirror.state.dropNext = 1000;
  await ask("are you there?");
  assert.ok(lines.length >= 8, `${lines.length} log lines`);
  for (const line of lines) {
    assert.ok(!line.includes(SECRET), line);
    assert.ok(!line.includes(mirror.token), line);
    assert.ok(!/authorization/i.test(line), line);
    JSON.parse(line);
  }
  assert.ok(lines.some((line) => line.includes("leaked [hidden]")));
  const exchange = events().find((entry) => entry.event === "exchange");
  assert.deepEqual(
    [exchange.id, exchange.source, exchange.heard, exchange.reply, exchange.acted],
    ["utt-1", "voice", "go to sleep", "Good night.", ["set_power"]],
  );
});
