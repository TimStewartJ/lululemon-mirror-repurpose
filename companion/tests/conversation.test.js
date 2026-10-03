import assert from "node:assert/strict";
import test from "node:test";
import { until } from "./fakes/clock.js";
import { speech } from "./fakes/stt.js";
import { startCompanion } from "./helpers.js";

test("a request within 120 seconds goes into the same conversation", async (t) => {
  const { ask, brain, clock } = await startCompanion(t, [{ text: "The clock is showing." }, { text: "It is bigger now." }]);
  await ask("show the clock");
  await clock.advance(119_000);
  await ask("make it bigger");
  assert.deepEqual(brain.runs.map((run) => run.fresh), [true, false]);
  assert.equal(brain.ended, 0, "the conversation was not closed in between");
});

test("after 120 seconds the conversation is closed and the next request starts a new one", async (t) => {
  const { ask, brain, clock } = await startCompanion(t, [{ text: "The clock is showing." }, { text: "Bigger than what?" }]);
  await ask("show the clock");
  await clock.advance(119_999);
  assert.equal(brain.ended, 0);
  await clock.advance(1);
  assert.equal(brain.ended, 1, "closed, so the owner's session list is not littered");
  await ask("make it bigger");
  assert.deepEqual(brain.runs.map((run) => run.fresh), [true, true]);
});

test("each turn moves the 120 seconds on", async (t) => {
  const { ask, brain, clock } = await startCompanion(t, [{ text: "One." }, { text: "Two." }, { text: "Three." }]);
  await ask("first");
  await clock.advance(100_000);
  await ask("second");
  await clock.advance(100_000);
  assert.equal(brain.ended, 0, "200 seconds after the first turn, but only 100 after the second");
  await ask("third");
  assert.deepEqual(brain.runs.map((run) => run.fresh), [true, false, false]);
  await clock.advance(120_000);
  assert.equal(brain.ended, 1);
});

test("overheard talk does not keep a conversation open", async (t) => {
  const { ask, say, brain, clock } = await startCompanion(t, [
    { text: "The clock is showing." },
    { calls: [{ tool: "ignore", args: { reason: "talk" } }] },
    { calls: [{ tool: "ignore", args: { reason: "talk, also at a second look" } }] },
    { text: "Bigger than what?" },
  ]);
  await ask("show the clock");
  await clock.advance(100_000);
  await say(speech("and then she said no"), { addressed: "window" });
  assert.equal(brain.runs[1].fresh, false, "it is heard within the conversation");
  await clock.advance(20_000);
  assert.equal(brain.ended, 1, "but the 120 seconds still count from the last real turn");
  await ask("make it bigger");
  assert.equal(brain.runs[3].fresh, true);
});

test("an answer to a question arrives in the conversation that asked", async (t) => {
  const { say, brain } = await startCompanion(t, [
    { text: "Which film: the flowers or the seasons?" },
    { calls: [{ tool: "set_background", args: { video: "luminous-flowers-spatial-180s" } }], text: "The flowers are on." },
  ]);
  const first = (await say(speech("Mirror, change the film"))).body;
  assert.equal(first.listen, true);
  const second = (await say(speech("the flowers"), { addressed: "follow-up", id: "utt-2" })).body;
  assert.equal(second.reply, "The flowers are on.");
  assert.equal(brain.runs[1].fresh, false);
  assert.match(brain.runs[1].prompt, /You asked: "Which film: the flowers or the seasons\?"/);
});

test("a session is asked for while the words are still being transcribed", async (t) => {
  const { say, brain, stt, clock } = await startCompanion(t, [{ text: "Good morning." }, { text: "Still here." }]);
  stt.takesMs = 1000;
  const pending = say(speech("Mirror, good morning"));
  await until(() => stt.heard === 1);
  assert.deepEqual(brain.prepared, [true], "asked for at once, as a new conversation");
  assert.equal(brain.runs.length, 0);
  await clock.advance(1000);
  await pending;
  const again = say(speech("Mirror, are you there"), { id: "utt-2" });
  await until(() => stt.heard === 2);
  assert.deepEqual(brain.prepared, [true, false], "within the conversation the open session serves");
  await clock.advance(1000);
  await again;
});

test("a session opened ahead for words that turn out to be nothing is closed again", async (t) => {
  const { say, brain } = await startCompanion(t);
  await say(speech("Mirror."));
  assert.deepEqual(brain.prepared, [true]);
  assert.equal(brain.runs.length, 0);
  assert.equal(brain.ended, 1);
});

test("what the mirror is asked to remember is in the instructions of the next conversation", async (t) => {
  const { ask, brain, clock } = await startCompanion(t, [
    { calls: [{ tool: "remember", args: { note: "Sam likes the flowers film in the evening" } }], text: "I'll remember that." },
    { text: "Good evening, Sam." },
    { calls: [{ tool: "forget", args: { containing: "flowers film" } }], text: "Forgotten." },
    { text: "Hello." },
  ]);
  assert.deepEqual((await ask("remember that I like the flowers film in the evening")).body.acted, ["remember"]);
  await clock.advance(121_000);
  await ask("good evening");
  assert.match(brain.runs[1].system, /What you remember about this household\n- Sam likes the flowers film in the evening$/);
  await ask("forget what I said about the flowers film");
  await clock.advance(121_000);
  await ask("hello");
  assert.match(brain.runs[3].system, /- Nothing yet\.$/);
});

test("typed words are marked as certainly meant for the mirror", async (t) => {
  const { ask, brain } = await startCompanion(t, [{ text: "Hello." }]);
  await ask("hello mirror");
  assert.match(brain.runs[0].prompt, /^Typed to you in the phone controls, so certainly meant for you:\n"hello mirror"/);
});
