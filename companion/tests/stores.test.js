import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { createActivity } from "../src/activity.js";
import { createMemory } from "../src/memory.js";
import { createQueue } from "../src/queue.js";
import { createRecordings } from "../src/recordings.js";
import { temporaryDirectory } from "./helpers.js";

test("memory keeps one line per note and survives a new start", (t) => {
  const folder = temporaryDirectory(t);
  const memory = createMemory(folder);
  assert.deepEqual(memory.lines(), []);
  assert.deepEqual(memory.remember("  Sam likes the flowers film\nin the evening "), { remembered: "Sam likes the flowers film in the evening" });
  assert.deepEqual(memory.remember("The trash goes out on Thursday"), { remembered: "The trash goes out on Thursday" });
  // Saying the same thing again does not add it again.
  memory.remember("the trash goes out on thursday");
  assert.deepEqual(createMemory(folder).lines(), ["Sam likes the flowers film in the evening", "The trash goes out on Thursday"]);
});

test("memory refuses what it cannot keep, and says why", (t) => {
  const memory = createMemory(temporaryDirectory(t));
  assert.match(memory.remember("   ").error, /empty/);
  assert.match(memory.remember("x".repeat(201)).error, /up to 200 characters/);
  for (let index = 0; index < 60; index++) memory.remember(`fact number ${index}`);
  assert.match(memory.remember("one too many").error, /full at 60 notes/);
});

test("forgetting removes the lines that contain the words", (t) => {
  const memory = createMemory(temporaryDirectory(t));
  memory.remember("Sam likes the flowers film");
  memory.remember("The trash goes out on Thursday");
  assert.deepEqual(memory.forget("TRASH"), { forgotten: ["The trash goes out on Thursday"] });
  assert.deepEqual(memory.lines(), ["Sam likes the flowers film"]);
  assert.match(memory.forget("bicycle").error, /No note contains "bicycle"/);
  assert.match(memory.forget("a").error, /at least three characters/);
});

test("activity lists the newest first and survives a new start", (t) => {
  const folder = temporaryDirectory(t);
  const activity = createActivity(folder);
  activity.add({ at: 1000, source: "voice", heard: "go to sleep", reply: "Good night.", acted: ["set_power"], ms: 2100 });
  activity.add({ at: 2000, source: "reminder", reply: "Reminder: trash", acted: ["say"] });
  activity.add({ at: 3000, source: "voice", heard: "so anyway", ignored: true, reason: "not-addressed", error: "none really" });
  assert.deepEqual(activity.recent(2).map((entry) => entry.at), [3000, 2000]);
  assert.deepEqual(activity.recent()[2], {
    at: 1000, source: "voice", heard: "go to sleep", reply: "Good night.", acted: ["set_power"], ignored: false, reason: "", ms: 2100,
  });
  assert.equal(activity.lastExchange().at, 3000);
  assert.deepEqual(activity.since(2000).map((entry) => entry.at), [2000, 3000]);
  const again = createActivity(folder);
  assert.deepEqual(again.recent().map((entry) => entry.at), [3000, 2000, 1000]);
  assert.equal(again.recent()[0].error, "none really");
});

test("an entry keeps the rows of a card, and one without a card has no such field", (t) => {
  const folder = temporaryDirectory(t);
  const activity = createActivity(folder);
  const details = [{ label: "Weather", text: "Clear, 62\u00B0 now." }, { label: "", text: "Buy milk \u00B7 Call the plumber" }];
  activity.add({ at: 1000, source: "shortcut", heard: "good morning", reply: "Good morning", details });
  activity.add({ at: 2000, source: "controls", heard: "what time is it?", reply: "It is 7:12.", details: [] });
  const [plain, card] = createActivity(folder).recent();
  assert.deepEqual(card, { at: 1000, source: "shortcut", heard: "good morning", reply: "Good morning", acted: [], ignored: false, reason: "", ms: 0, details });
  assert.ok(!("details" in plain));
  // A greeting answered by code is not what the health report calls the last thing asked of the model.
  assert.equal(activity.lastExchange().at, 2000);
  activity.add({ at: 3000, source: "shortcut", heard: "good night", reply: "Good night" });
  assert.equal(activity.lastExchange().at, 2000);
});

test("activity keeps the last 200 and its file does not grow without end", (t) => {
  const folder = temporaryDirectory(t);
  const activity = createActivity(folder);
  for (let at = 1; at <= 450; at++) activity.add({ at, source: "tend" });
  assert.equal(activity.recent(500).length, 200);
  assert.equal(activity.recent(1)[0].at, 450);
  assert.equal(activity.lastExchange(), null);
  const lines = fs.readFileSync(path.join(folder, "activity.jsonl"), "utf8").trim().split("\n");
  assert.ok(lines.length <= 400, `${lines.length} lines`);
  assert.equal(createActivity(folder).recent(1)[0].at, 450);
});

test("recordings keep the newest few", (t) => {
  const folder = temporaryDirectory(t);
  const recordings = createRecordings(folder, 2);
  recordings.save("a", Buffer.from("one"), Date.UTC(2026, 9, 3, 14, 0, 0));
  recordings.save("b", Buffer.from("two"), Date.UTC(2026, 9, 3, 14, 0, 1));
  recordings.save("c", Buffer.from("three"), Date.UTC(2026, 9, 3, 14, 0, 2));
  const names = fs.readdirSync(recordings.folder).sort();
  assert.equal(names.length, 2);
  assert.match(names[0], /^20261003T140001000Z-b\.wav$/);
  assert.match(names[1], /-c\.wav$/);
});

test("keeping no recordings stores none and removes what is there", (t) => {
  const folder = temporaryDirectory(t);
  createRecordings(folder, 5).save("a", Buffer.from("one"), 1000);
  const none = createRecordings(folder, 0);
  none.save("b", Buffer.from("two"), 2000);
  assert.deepEqual(fs.readdirSync(none.folder), []);
  // With nothing ever stored there is no folder, and that is fine too.
  createRecordings(temporaryDirectory(t), 0).save("c", Buffer.from("three"), 3000);
});

test("people take turns, two at most, and a third is turned away", async () => {
  const queue = createQueue();
  const order = [];
  const first = queue.enter();
  const second = queue.enter();
  assert.equal(typeof first, "function");
  assert.equal(queue.enter(), null);
  let finishFirst;
  const one = queue.turn(async () => {
    order.push("first starts");
    await new Promise((resolve) => (finishFirst = resolve));
    order.push("first ends");
  });
  const two = queue.turn(async () => order.push("second runs"));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(order, ["first starts"]);
  assert.equal(queue.running, true);
  finishFirst();
  await Promise.all([one, two]);
  assert.deepEqual(order, ["first starts", "first ends", "second runs"]);
  first();
  first();
  assert.equal(typeof queue.enter(), "function", "leaving makes room, and leaving twice counts once");
  assert.equal(queue.enter(), null);
  second();
});

test("a run of the companion's own steps aside for people", async () => {
  const queue = createQueue();
  const leave = queue.enter();
  assert.deepEqual(await queue.proactive(async () => "ran"), { skipped: true });
  leave();
  assert.deepEqual(await queue.proactive(async () => "ran"), { skipped: false, value: "ran" });

  // One that is under way is told to stop when a person arrives.
  let signalSeen;
  const running = queue.proactive(
    (signal) =>
      new Promise((resolve) => {
        signalSeen = signal;
        signal.addEventListener("abort", () => resolve("stopped"));
      }),
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(await queue.proactive(async () => "second"), { skipped: true }, "two of them never overlap");
  assert.equal(signalSeen.aborted, false);
  const person = queue.enter();
  assert.equal(signalSeen.aborted, true);
  assert.deepEqual(await running, { skipped: false, value: "stopped" });
  person();
  assert.equal(queue.busy, false);
});

test("a failing turn does not block the next", async () => {
  const queue = createQueue();
  await assert.rejects(queue.turn(async () => Promise.reject(new Error("broke"))), /broke/);
  assert.equal(await queue.turn(async () => "fine"), "fine");
  assert.equal(queue.running, false);
});
