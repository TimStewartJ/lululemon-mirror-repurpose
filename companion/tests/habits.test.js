import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { freshConfig, loadConfig, writeConfig } from "../src/config.js";
import { createHabits, describeHabits } from "../src/habits.js";
import { startCompanion, temporaryDirectory } from "./helpers.js";

const usual = () => ({ greet: true, morningBriefing: true, reminders: true, tend: true, tendMinutes: 60, quietHours: ["22:30", "06:30"] });

test("habits change in place, so that whoever holds the settings sees the change", () => {
  const settings = usual();
  const habits = createHabits({ settings });
  const seen = [];
  habits.onChange((now) => seen.push(now));
  const result = habits.change({ greet: false, tendMinutes: 30, quietHours: null });
  assert.deepEqual(result.changed, ["greet", "tendMinutes", "quietHours"]);
  assert.equal(result.saved, false, "with no file they hold until the companion restarts");
  assert.deepEqual(settings, { greet: false, morningBriefing: true, reminders: true, tend: true, tendMinutes: 30, quietHours: null });
  assert.equal(habits.settings, settings);
  assert.deepEqual(seen, [habits.get()]);
  // What it hands out is a copy.
  habits.get().greet = true;
  assert.equal(settings.greet, false);
});

test("a change that changes nothing is not announced, and a bad one changes nothing", () => {
  const settings = usual();
  const habits = createHabits({ settings });
  let announced = 0;
  habits.onChange(() => (announced += 1));
  assert.deepEqual(habits.change({ greet: true, quietHours: ["22:30", "06:30"] }).changed, []);
  assert.throws(() => habits.change({ greet: false, tendMinutes: 2 }), /every 5 minutes at the most often/);
  assert.throws(() => habits.change({ greet: false, quietHours: ["22:30", "25:00"] }), /two times of day/);
  assert.throws(() => habits.change({ greet: false, quietHours: ["22:30", "22:30"] }), /same minute/);
  assert.throws(() => habits.change({ reminders: "no" }), /reminders is on or off/);
  assert.deepEqual(settings, usual());
  assert.equal(announced, 0);
});

test("a change is written to the config file and leaves the rest of it as it was", (t) => {
  const file = path.join(temporaryDirectory(t), "config.json");
  const written = { ...freshConfig(), mirror: { host: "192.0.2.10", port: 8787, token: "a-token-the-owner-paired" }, model: "another-model" };
  delete written.proactive.morningBriefing;
  writeConfig(file, written);
  const config = loadConfig(file);
  assert.equal(config.onDisk, true);
  assert.equal(config.path, path.resolve(file));
  const events = [];
  const habits = createHabits({ settings: config.proactive, file: config.path, log: (event, fields) => events.push({ event, ...fields }) });
  const result = habits.change({ greet: false, quietHours: ["21:00", "07:00"] });
  assert.equal(result.saved, true);
  const onDisk = JSON.parse(fs.readFileSync(file, "utf8"));
  assert.deepEqual(onDisk.proactive, { greet: false, reminders: true, tend: true, tendMinutes: 60, quietHours: ["21:00", "07:00"] });
  assert.deepEqual({ ...onDisk, proactive: written.proactive }, written, "nothing else in the file was touched");
  assert.deepEqual(events, [{ event: "habits.changed", changed: ["greet", "quietHours"], saved: true }]);
  // It is what the companion starts with the next time.
  const again = loadConfig(file).proactive;
  assert.equal(again.greet, false);
  assert.deepEqual(again.quietHours, ["21:00", "07:00"]);
  if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o077, 0, "the file still holds a secret");
});

test("a config file that cannot be written leaves the change in force and says so", (t) => {
  const file = path.join(temporaryDirectory(t), "config.json");
  writeConfig(file, freshConfig());
  const config = loadConfig(file);
  // The owner broke the file while the companion was running.
  fs.writeFileSync(file, "{ not json");
  const events = [];
  const habits = createHabits({ settings: config.proactive, file: config.path, log: (event, fields) => events.push({ event, ...fields }) });
  const result = habits.change({ reminders: false });
  assert.equal(result.saved, false);
  assert.equal(config.proactive.reminders, false);
  assert.equal(events[0].event, "habits.not_saved");
  assert.equal(fs.readFileSync(file, "utf8"), "{ not json");
});

test("the habits are told the way a person would ask about them", () => {
  assert.deepEqual(describeHabits(usual()), {
    greetsWhoWalksUp: "on", morningBriefing: "on", showsRemindersWhenDue: "on", tidiesTheDisplay: "every 60 minutes", quietHours: "22:30 to 06:30",
  });
  assert.deepEqual(describeHabits({ greet: false, reminders: false, tend: false, tendMinutes: 60, quietHours: null }), {
    greetsWhoWalksUp: "off", morningBriefing: "off", showsRemindersWhenDue: "off", tidiesTheDisplay: "off", quietHours: "none",
  });
});

test("asked to stop greeting, the companion stops, without a restart", async (t) => {
  const companion = await startCompanion(
    t,
    [{ calls: [{ tool: "say", args: { text: "Hello." } }], text: "done" }, { calls: [{ tool: "habits", args: { greet: false } }], text: "I won't greet you any more." }],
    { greet: true },
  );
  assert.equal(await companion.running.proactive.greet({ asleepSeconds: 3600 }), "greeted");
  const answer = await companion.ask("stop greeting me when I walk up");
  assert.equal(answer.body.reply, "I won't greet you any more.");
  assert.deepEqual(answer.body.acted, ["habits"]);
  assert.equal(companion.running.habits.get().greet, false);
  await companion.clock.advance(2 * 60 * 60_000);
  assert.equal(await companion.running.proactive.greet({ asleepSeconds: 3600 }), "off");
  // The test's config was never a file, so nothing was written anywhere.
  assert.equal(companion.config.onDisk, undefined);
  assert.ok(companion.events().some((event) => event.event === "habits.changed" && event.saved === false));
});
