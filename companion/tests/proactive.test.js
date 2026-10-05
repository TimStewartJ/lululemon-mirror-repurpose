import assert from "node:assert/strict";
import test from "node:test";
import { createActivity } from "../src/activity.js";
import { createHabits } from "../src/habits.js";
import { createMemory } from "../src/memory.js";
import { createMirror } from "../src/mirror.js";
import { createProactive } from "../src/proactive.js";
import { createQueue } from "../src/queue.js";
import { createTools } from "../src/tools.js";
import { scriptedBrain } from "./fakes/brain.js";
import { fakeClock, until } from "./fakes/clock.js";
import { startFakeMirror } from "./fakes/mirror.js";
import { startCompanion, temporaryDirectory } from "./helpers.js";

const MINUTE = 60_000;
const greetingLine = { calls: [{ tool: "say", args: { text: "Good morning. Rain from eleven." } }], text: "done" };

/** The proactive part by itself, with a fake mirror and a scripted brain. */
async function startProactive(t, script = [], settings = {}) {
  const clock = fakeClock();
  const fake = await startFakeMirror({ now: clock.now });
  const mirror = createMirror({ host: fake.host, port: fake.port, token: fake.token });
  const folder = temporaryDirectory(t);
  const memory = createMemory(folder);
  const activity = createActivity(folder);
  const queue = createQueue();
  const events = [];
  const log = (event, fields) => events.push({ event, ...fields });
  const brain = scriptedBrain(clock, script);
  const habits = createHabits({ settings: { greet: true, reminders: true, tend: true, tendMinutes: 60, quietHours: ["22:30", "06:30"], ...settings } });
  const proactive = createProactive({
    settings: habits.settings,
    brain,
    mirror,
    tools: createTools({ mirror, memory, clock, log }),
    memory,
    activity,
    queue,
    log,
    clock,
  });
  habits.onChange(() => proactive.settingsChanged());
  t.after(async () => {
    proactive.stop();
    mirror.close();
    await fake.close();
  });
  /** Puts an item on the fake mirror's board directly, as another program would. */
  const post = (item) => fake.state.board.create(item, "Kitchen agent");
  return { clock, fake, brain, proactive, habits, activity, queue, memory, events, post };
}

test("someone walking up after a long dark is greeted with one notice", async (t) => {
  const { proactive, fake, brain, activity } = await startProactive(t, [greetingLine]);
  assert.equal(await proactive.greet({ asleepSeconds: 3600 }), "greeted");
  assert.deepEqual(fake.state.said, [{ text: "Good morning. Rain from eleven.", kind: "notice", seconds: null, shown: true }]);
  const run = brain.runs[0];
  assert.equal(run.session, "proactive");
  assert.deepEqual(run.tools, ["say"], "a greeting can speak and change nothing");
  assert.match(run.prompt, /^Someone just walked up\. The display had been dark for 60 minutes\.\n\nThe mirror now:\n\{/);
  assert.match(run.system, /stay silent/);
  assert.ok(run.timeoutMs <= 15_000);
  assert.deepEqual(
    activity.recent().map((entry) => [entry.source, entry.reply, entry.acted]),
    [["presence", "Good morning. Rain from eleven.", ["say"]]],
  );
});

test("a greeting may stay silent, and that is recorded too", async (t) => {
  const { proactive, fake, activity } = await startProactive(t, [{ text: "done" }]);
  assert.equal(await proactive.greet({ asleepSeconds: 3600 }), "silent");
  assert.equal(fake.state.said.length, 0);
  assert.deepEqual(activity.recent().map((entry) => [entry.source, entry.reply, entry.acted]), [["presence", "", []]]);
});

test("there is at most one greeting in 45 minutes", async (t) => {
  const { proactive, clock, brain } = await startProactive(t, [greetingLine, greetingLine, greetingLine]);
  assert.equal(await proactive.greet({ asleepSeconds: 3600 }), "greeted");
  await clock.advance(44 * MINUTE);
  assert.equal(await proactive.greet({ asleepSeconds: 1200 }), "greeted-recently");
  await clock.advance(1 * MINUTE);
  assert.equal(await proactive.greet({ asleepSeconds: 1200 }), "greeted");
  assert.equal(brain.runs.length, 2);
});

test("a short absence gets no greeting", async (t) => {
  const { proactive, brain } = await startProactive(t, [greetingLine]);
  assert.equal(await proactive.greet({ asleepSeconds: 599 }), "asleep-too-short");
  assert.equal(await proactive.greet({}), "asleep-too-short");
  assert.equal(await proactive.greet({ asleepSeconds: 600 }), "greeted");
  assert.equal(brain.runs.length, 1);
});

test("there is no greeting in quiet hours, by the mirror's clock", async (t) => {
  const { proactive, clock, brain, fake } = await startProactive(t, [greetingLine]);
  // 07:12 on the mirror; quiet hours begin at 22:30.
  await clock.advance((15 * 60 + 17) * MINUTE);
  assert.equal(await proactive.greet({ asleepSeconds: 3600 }), "greeted");
  await clock.advance(60 * MINUTE);
  assert.equal(await proactive.greet({ asleepSeconds: 3600 }), "quiet-hours");
  await clock.advance(7 * 60 * MINUTE);
  assert.equal(await proactive.greet({ asleepSeconds: 3600 }), "quiet-hours", "06:29 is still quiet");
  assert.equal(brain.runs.length, 1);
  assert.equal(fake.state.said.length, 1);
});

test("quiet hours can be switched off, and so can greeting", async (t) => {
  const always = await startProactive(t, [greetingLine], { quietHours: null });
  await always.clock.advance(17 * 60 * MINUTE);
  assert.equal(await always.proactive.greet({ asleepSeconds: 3600 }), "greeted");
  const never = await startProactive(t, [greetingLine], { greet: false });
  assert.equal(await never.proactive.greet({ asleepSeconds: 3600 }), "off");
  assert.equal(never.brain.runs.length, 0);
});

test("a greeting that would come later than 15 seconds is dropped", async (t) => {
  const { proactive, clock, fake, brain, activity } = await startProactive(t, [
    { before: () => clock.advance(16_000), ...greetingLine },
    { hangs: true },
  ]);
  assert.equal(await proactive.greet({ asleepSeconds: 3600 }), "silent");
  assert.match(brain.runs[0].results[0].error, /Too late/);
  assert.equal(fake.state.said.length, 0);

  await clock.advance(46 * MINUTE);
  const slow = proactive.greet({ asleepSeconds: 3600 });
  await until(() => brain.runs.length === 2);
  await clock.advance(15_000);
  assert.match(await slow, /^failed: timeout/);
  assert.match(activity.recent(1)[0].error, /^timeout/);
});

test("a greeting steps aside for a person", async (t) => {
  const { proactive, queue, brain } = await startProactive(t, [{ hangs: true }, greetingLine]);
  const leave = queue.enter();
  assert.equal(await proactive.greet({ asleepSeconds: 3600 }), "busy");
  leave();
  assert.equal(brain.runs.length, 0);
  // One that is under way is stopped when a person arrives, and counts as the greeting of that hour.
  const greeting = proactive.greet({ asleepSeconds: 3600 });
  await until(() => brain.runs.length === 1);
  const person = queue.enter();
  assert.match(await greeting, /^failed: aborted/);
  person();
});

test("a reminder that falls due is shown once, as a small card for 20 seconds", async (t) => {
  const { proactive, clock, fake, post, activity, brain } = await startProactive(t);
  post({ kind: "reminder", title: "Take out the trash", due: clock.now() + 10 * MINUTE });
  await proactive.checkReminders();
  assert.equal(fake.state.said.length, 0, "not yet due");
  await clock.advance(10 * MINUTE);
  await proactive.checkReminders();
  assert.deepEqual(fake.state.said, [
    { text: "Take out the trash", kind: "notice", seconds: 20, shown: true, details: [{ label: "Reminder", text: "Now, 7:22 AM" }] },
  ]);
  for (let round = 0; round < 3; round++) {
    await clock.advance(30_000);
    await proactive.checkReminders();
  }
  assert.equal(fake.state.said.length, 1, "once per item");
  assert.deepEqual(
    activity.recent().map((entry) => [entry.source, entry.reply, entry.details, entry.acted]),
    [["reminder", "Take out the trash", [{ label: "Reminder", text: "Now, 7:22 AM" }], ["say"]]],
  );
  assert.equal(brain.runs.length, 0, "no model is involved");
});

test("the board is read only when it has changed", async (t) => {
  const { proactive, clock, fake, post } = await startProactive(t);
  const reads = () => fake.requests().filter((request) => request.path.startsWith("/api/v1/board/items")).length;
  post({ kind: "todo", title: "Call the plumber", due: clock.now() + 2 * MINUTE });
  await proactive.checkReminders();
  await clock.advance(MINUTE);
  await proactive.checkReminders();
  assert.equal(reads(), 1);
  await clock.advance(MINUTE);
  await proactive.checkReminders();
  assert.equal(reads(), 1, "a known due time needs no new read");
  assert.equal(fake.state.said[0].text, "Call the plumber", "a to-do with a due time is announced as well");
  post({ kind: "note", title: "Welcome home" });
  await proactive.checkReminders();
  assert.equal(reads(), 2);
});

test("a reminder that was done, removed or moved is not announced at its old time", async (t) => {
  const { proactive, clock, fake, post } = await startProactive(t);
  const done = post({ kind: "reminder", title: "Done already", due: clock.now() + 5 * MINUTE });
  const gone = post({ kind: "reminder", title: "Removed", due: clock.now() + 5 * MINUTE });
  const moved = post({ kind: "reminder", title: "Moved", due: clock.now() + 5 * MINUTE });
  await proactive.checkReminders();
  fake.state.board.patch(done.id, { done: true });
  fake.state.board.delete(gone.id);
  fake.state.board.patch(moved.id, { due: clock.now() + 20 * MINUTE });
  await clock.advance(5 * MINUTE);
  await proactive.checkReminders();
  assert.equal(fake.state.said.length, 0);
  await clock.advance(15 * MINUTE);
  await proactive.checkReminders();
  assert.deepEqual(fake.state.said.map((caption) => caption.text), ["Moved"]);
});

test("what was overdue before the companion looked is not announced late", async (t) => {
  const { proactive, clock, fake, post } = await startProactive(t);
  post({ kind: "reminder", title: "Long ago", due: clock.now() + MINUTE });
  await clock.advance(30 * MINUTE);
  await proactive.checkReminders();
  await proactive.checkReminders();
  assert.equal(fake.state.said.length, 0);
});

test("a reminder in quiet hours shows no caption, then or later", async (t) => {
  const { proactive, clock, fake, post, events } = await startProactive(t);
  await clock.advance(15 * 60 * MINUTE);
  post({ kind: "reminder", title: "Lock the door", due: clock.now() + 30 * MINUTE });
  await proactive.checkReminders();
  await clock.advance(30 * MINUTE);
  await proactive.checkReminders();
  assert.equal(fake.state.said.length, 0, "22:42 is in quiet hours");
  assert.ok(events.some((entry) => entry.event === "reminder.quiet" && entry.title === "Lock the door"));
  await clock.advance(9 * 60 * MINUTE);
  await proactive.checkReminders();
  assert.equal(fake.state.said.length, 0);
});

test("a reminder on a dark display is recorded as not shown, and not repeated", async (t) => {
  const { proactive, clock, fake, post, activity } = await startProactive(t);
  post({ kind: "reminder", title: "Water the plants", due: clock.now() + MINUTE });
  await proactive.checkReminders();
  fake.state.automation.sleeping = true;
  await clock.advance(MINUTE);
  await proactive.checkReminders();
  assert.deepEqual(fake.state.said.map((caption) => caption.shown), [false]);
  assert.equal(activity.recent(1)[0].reason, "sleeping");
  fake.state.automation.sleeping = false;
  await clock.advance(MINUTE);
  await proactive.checkReminders();
  assert.equal(fake.state.said.length, 1);
});

test("reminders can be switched off", async (t) => {
  const { proactive, clock, fake, post } = await startProactive(t, [], { reminders: false });
  post({ kind: "reminder", title: "Take out the trash", due: clock.now() + MINUTE });
  await proactive.checkReminders();
  await clock.advance(MINUTE);
  await proactive.checkReminders();
  assert.equal(fake.state.said.length, 0);
  assert.equal(fake.requests().filter((request) => request.path.startsWith("/api/v1/board")).length, 0);
});

test("tending is told what people asked for in the last 12 hours, and to leave it alone", async (t) => {
  const { proactive, clock, brain, activity } = await startProactive(t, [{ text: "Nothing to do." }]);
  const hour = 60 * MINUTE;
  activity.add({ at: clock.now() - 13 * hour, source: "voice", heard: "change the background to the flowers one", acted: ["set_background"] });
  activity.add({ at: clock.now() - 3 * hour, source: "voice", heard: "move the clock to the bottom", acted: ["arrange_widgets"] });
  activity.add({ at: clock.now() - 2 * hour, source: "voice", heard: "and so she left", ignored: true, reason: "not-addressed", acted: ["ignore"] });
  activity.add({ at: clock.now() - 1 * hour, source: "controls", heard: "what is on my list?", acted: [] });
  activity.add({ at: clock.now() - 30 * MINUTE, source: "reminder", reply: "Reminder: trash", acted: ["say"] });
  assert.equal(await proactive.tend(), "nothing");
  const { prompt, system, tools, session } = brain.runs[0];
  assert.equal(session, "proactive");
  assert.match(
    prompt,
    /^What people asked for in the last 12 hours:\n- 04:12 "move the clock to the bottom" \(arrange_widgets\)\n- 06:12 "what is on my list\?" \(no tool\)\n\nThe mirror now:\n\{/,
  );
  assert.ok(!prompt.includes("flowers one"), "what was asked 13 hours ago is no longer protected");
  assert.ok(!prompt.includes("she left"), "overheard talk is not a request");
  assert.match(system, /Leave alone whatever a person asked for in the last 12 hours/);
  assert.match(system, /Change one thing at most/);
  assert.deepEqual(tools, ["get_state", "set_background", "board_remove"], "the layout was a person's wish, so its tool is withheld");
  assert.equal(activity.recent(1)[0].source, "reminder", "a run that changed nothing is not listed");
});

test("tending is not handed the tools for what people asked for, and does not run when that is all of them", async (t) => {
  const { proactive, clock, brain, activity } = await startProactive(t, [{ text: "Nothing to do." }, { text: "Nothing to do." }]);
  activity.add({ at: clock.now() - 13 * 60 * MINUTE, source: "voice", heard: "hide the weather", acted: ["arrange_widgets"] });
  assert.equal(await proactive.tend(), "nothing");
  assert.deepEqual(brain.runs[0].tools, ["get_state", "set_background", "arrange_widgets", "board_remove"], "after 12 hours everything may be tended again");
  activity.add({ at: clock.now() - 60 * MINUTE, source: "voice", heard: "the calm film please", acted: ["set_background"] });
  activity.add({ at: clock.now() - 50 * MINUTE, source: "controls", heard: "add milk to my list", acted: ["board_add"] });
  assert.equal(await proactive.tend(), "nothing");
  assert.deepEqual(brain.runs[1].tools, ["get_state", "arrange_widgets"]);
  activity.add({ at: clock.now() - 40 * MINUTE, source: "voice", heard: "clock to the bottom", acted: ["arrange_widgets"] });
  assert.equal(await proactive.tend(), "all-recently-asked-for");
  assert.equal(brain.runs.length, 2, "the model is not asked when there is nothing it may change");
});

test("tending may change one thing, and what it did is listed", async (t) => {
  const { proactive, fake, activity, post } = await startProactive(t, [
    {
      calls: [
        { tool: "board_remove", args: { all: "done" } },
        { tool: "set_background", args: { video: "next" } },
      ],
      text: "I cleared the finished items.",
    },
  ]);
  const item = post({ kind: "todo", title: "Old chore" });
  fake.state.board.patch(item.id, { done: true });
  assert.equal(await proactive.tend(), "changed");
  assert.equal(fake.state.board.items.length, 0);
  assert.equal(fake.state.activeFilm.slice(0, 8), "546e5d02", "the second change was refused");
  assert.deepEqual(
    activity.recent().map((entry) => [entry.source, entry.reply, entry.acted]),
    [["tend", "I cleared the finished items.", ["board_remove", "set_background"]]],
  );
});

test("tending does not run on a dark display, in quiet hours, when switched off, or beside a person", async (t) => {
  const { proactive, clock, fake, brain, queue } = await startProactive(t);
  fake.state.automation.sleeping = true;
  assert.equal(await proactive.tend(), "asleep");
  fake.state.automation.sleeping = false;
  const leave = queue.enter();
  assert.equal(await proactive.tend(), "busy");
  leave();
  await clock.advance(16 * 60 * MINUTE);
  assert.equal(await proactive.tend(), "quiet-hours");
  assert.equal(brain.runs.length, 0);
  const off = await startProactive(t, [], { tend: false });
  assert.equal(await off.proactive.tend(), "off");
});

test("once started, it looks every 30 seconds and tends every hour", async (t) => {
  const { proactive, clock, fake, brain, post } = await startProactive(t, [{ text: "Nothing to do." }]);
  post({ kind: "reminder", title: "Tea is ready", due: clock.now() + 4 * MINUTE });
  proactive.start();
  await until(() => fake.requests().some((request) => request.path === "/api/v1/status"), "the first look");
  for (let step = 0; step < 9; step++) {
    await clock.advance(30_000);
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  assert.deepEqual(fake.state.said.map((caption) => caption.text), ["Tea is ready"]);
  assert.equal(brain.runs.length, 0);
  await clock.advance(56 * MINUTE);
  await until(() => brain.runs.length === 1, "the hourly tending run");
  proactive.stop();
});

test("the tidying follows its settings when they are changed while running", async (t) => {
  const settings = { tend: false };
  const { proactive, clock, brain, habits } = await startProactive(t, [{ text: "Nothing to do." }, { text: "Nothing to do." }], settings);
  proactive.start();
  await clock.advance(3 * 60 * MINUTE);
  assert.equal(brain.runs.length, 0, "switched off, it does not tidy");
  // Switched on and told to come round every 20 minutes, as the habits tool does it.
  habits.change({ tend: true, tendMinutes: 20 });
  await clock.advance(19 * MINUTE);
  assert.equal(brain.runs.length, 0);
  await clock.advance(MINUTE);
  await until(() => brain.runs.length === 1, "the first tending run after 20 minutes");
  // Asked for less often while a round is pending, the pending one is dropped for the new time.
  await new Promise((resolve) => setTimeout(resolve, 20));
  habits.change({ tendMinutes: 120 });
  await clock.advance(60 * MINUTE);
  assert.equal(brain.runs.length, 1);
  await clock.advance(60 * MINUTE);
  await until(() => brain.runs.length === 2, "the tending run after two hours");
  habits.change({ tend: false });
  await clock.advance(6 * 60 * MINUTE);
  assert.equal(brain.runs.length, 2);
  proactive.stop();
});

test("a presence event sent to the companion leads to a greeting", async (t) => {
  // 07:12 is morning; the helper has the morning briefing switched off, so the model greets.
  const { call, brain, mirror, events } = await startCompanion(t, [greetingLine], { greet: true });
  const answer = await call("POST", "/v1/event", { body: JSON.stringify({ type: "presence", at: 1790990000000, asleepSeconds: 1800 }) });
  assert.equal(answer.status, 202);
  await until(() => events().some((entry) => entry.event === "greeting"), "the greeting to finish");
  assert.equal(events().find((entry) => entry.event === "greeting").outcome, "greeted");
  assert.equal(mirror.state.said[0].kind, "notice");
  assert.equal(brain.runs[0].session, "proactive");
  const listed = (await call("GET", "/v1/activity")).body.entries[0];
  assert.deepEqual([listed.source, listed.reply], ["presence", "Good morning. Rain from eleven."]);
});

const MORNING_CARD = {
  text: "Good morning",
  kind: "notice",
  seconds: 12,
  shown: true,
  details: [
    { label: "Weather", text: "Overcast, 12° now. High 16°, rain likely by 11 AM." },
    { label: "Today", text: "Nothing on your list." },
  ],
};

test("the first person of the morning gets the briefing as a card, without the model", async (t) => {
  const { proactive, fake, brain, activity, clock } = await startProactive(t, [greetingLine], { morningBriefing: true });
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "briefed");
  assert.deepEqual(fake.state.said, [MORNING_CARD]);
  assert.equal(brain.runs.length, 0);
  assert.equal(fake.writes().length, 1, "the card is shown and nothing is changed");
  assert.deepEqual(
    activity.recent().map((entry) => [entry.source, entry.heard, entry.reply, entry.details, entry.acted]),
    [["presence", "", "Good morning", MORNING_CARD.details, ["say"]]],
  );
  // It is the greeting of that hour, and there is one briefing a day.
  await clock.advance(44 * MINUTE);
  assert.equal(await proactive.presence({ asleepSeconds: 1200 }), "greeted-recently");
  await clock.advance(MINUTE);
  assert.equal(await proactive.presence({ asleepSeconds: 1200 }), "greeted", "later that morning the model greets, as at any other hour");
  assert.equal(fake.state.said.length, 2);
  assert.equal(fake.state.said[1].details, undefined);
});

test("the morning briefing comes once on each of the mirror's days, between 5 and 11", async (t) => {
  const { proactive, fake, brain, clock } = await startProactive(t, [], { morningBriefing: true, quietHours: null });
  // 07:12 on Saturday.
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "briefed");
  await clock.advance((3 * 60 + 47) * MINUTE);
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "silent", "10:59, but today's was shown: the model is asked");
  await clock.advance(18 * 60 * MINUTE);
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "silent", "04:59 on Sunday is not morning yet");
  assert.equal(fake.state.said.length, 1);
  assert.equal(brain.runs.length, 2);
  await clock.advance(MINUTE);
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "briefed", "05:00");
  await clock.advance(24 * 60 * MINUTE + 6 * 60 * MINUTE);
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "silent", "11:00 on Monday is past the morning");
  assert.deepEqual(fake.state.said.map((caption) => caption.text), ["Good morning", "Good morning"]);
});

test("the morning briefing waits for a long dark, a quiet hour to end, and a display that shows it", async (t) => {
  const { proactive, fake, brain, clock, queue } = await startProactive(t, [], { morningBriefing: true, greet: false });
  assert.equal(await proactive.presence({ asleepSeconds: 599 }), "off", "after a short dark there is no card, and greeting is off");
  assert.equal(await proactive.presence({}), "off");
  const leave = queue.enter();
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "busy", "it steps aside for a person");
  leave();
  fake.state.automation.sleeping = true;
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "not-shown");
  fake.state.automation.sleeping = false;
  // Tried again at the next presence, since nobody has seen it yet. Greeting by the model is off; the card is its own switch.
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "briefed");
  assert.deepEqual(fake.state.said.map((caption) => caption.shown), [false, true]);
  // The next morning at 06:00 it is still a quiet hour; at 06:30 it is not.
  await clock.advance((22 * 60 + 48) * MINUTE);
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "quiet-hours");
  await clock.advance(30 * MINUTE);
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "briefed");
  assert.equal(fake.state.said.length, 3);
  assert.equal(brain.runs.length, 0);
});

test("with the morning briefing switched off, the model greets in the morning too", async (t) => {
  const { proactive, fake, brain } = await startProactive(t, [greetingLine], { morningBriefing: false });
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "greeted");
  assert.deepEqual(fake.state.said, [{ text: "Good morning. Rain from eleven.", kind: "notice", seconds: null, shown: true }]);
  assert.equal(brain.runs.length, 1);
});

test("once the mirror's clock is known, a presence at another hour costs no look at the mirror", async (t) => {
  const { proactive, fake, clock } = await startProactive(t, [], { morningBriefing: true, greet: false });
  await clock.advance(4 * 60 * MINUTE);
  await proactive.checkReminders();
  const before = fake.requests().length;
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "off", "11:12");
  assert.equal(fake.requests().length, before);
});

test("someone who comes back is told which reminders fell due while the display was dark", async (t) => {
  const { proactive, clock, fake, post, activity, brain } = await startProactive(t, [greetingLine]);
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "greeted");
  post({ kind: "reminder", title: "Water the plants", due: clock.now() + MINUTE });
  post({ kind: "reminder", title: "Dentist", due: clock.now() + 108 * MINUTE });
  post({ kind: "reminder", title: "Start dishwasher", due: clock.now() + 14 * 60 * MINUTE });
  await proactive.checkReminders();
  fake.state.automation.sleeping = true;
  await clock.advance(MINUTE);
  await proactive.checkReminders();
  assert.deepEqual(fake.state.said.map((caption) => [caption.text, caption.shown]), [["Good morning. Rain from eleven.", true], ["Water the plants", false]]);

  await clock.advance(30 * MINUTE);
  fake.state.automation.sleeping = false;
  // 31 minutes after the greeting: the 45 minutes between greetings do not hold this back.
  assert.equal(await proactive.presence({ asleepSeconds: 59 }), "asleep-too-short", "someone who only stepped aside was not away");
  assert.equal(await proactive.presence({ asleepSeconds: 60 }), "caught-up");
  assert.deepEqual(fake.state.said[2], {
    text: "While you were away",
    kind: "notice",
    seconds: 12,
    shown: true,
    details: [
      { label: "Missed", text: "Water the plants, 30 minutes ago" },
      { label: "Next", text: "Dentist 9:00 AM" },
    ],
  });
  assert.equal(brain.runs.length, 1, "no model is asked for it");
  const [entry] = activity.recent(1);
  assert.deepEqual([entry.source, entry.reply, entry.details, entry.acted], ["presence", "While you were away", fake.state.said[2].details, ["say"]]);
  // Told once: the next presence is an ordinary one.
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "greeted-recently");
  assert.equal(fake.state.said.length, 3);
});

test("a reminder that fell due in a quiet hour is in the morning's briefing, and is then told", async (t) => {
  const { proactive, clock, fake, post } = await startProactive(t, [], { morningBriefing: true });
  // 05:42 on Sunday, a quiet hour.
  await clock.advance((22 * 60 + 30) * MINUTE);
  post({ kind: "reminder", title: "Take the pills", due: clock.now() + 18 * MINUTE });
  await proactive.checkReminders();
  await clock.advance(18 * MINUTE);
  await proactive.checkReminders();
  assert.equal(fake.state.said.length, 0, "06:00 is quiet");
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "quiet-hours");
  await clock.advance(45 * MINUTE);
  assert.equal(await proactive.presence({ asleepSeconds: 3600 }), "briefed");
  assert.deepEqual(fake.state.said[0].details[1], { label: "Missed", text: "Take the pills, 45 minutes ago" });
  // The briefing named it, so there is no second card about it.
  await clock.advance(5 * 60 * MINUTE);
  assert.equal(await proactive.presence({ asleepSeconds: 120 }), "asleep-too-short");
  assert.equal(fake.state.said.length, 1);
});

test("a reminder whose card could not be shown, or that was found late, is told at the next presence", async (t) => {
  const { proactive, clock, fake, post, activity } = await startProactive(t, [], { greet: false });
  post({ kind: "reminder", title: "Feed the cat", due: clock.now() + MINUTE });
  await proactive.checkReminders();
  fake.state.failing.set("/api/v1/assistant/say", 500);
  await clock.advance(MINUTE);
  await proactive.checkReminders();
  assert.match(activity.recent(1)[0].error, /Mirror Home could not complete the request/);
  // The mirror was out of reach when the second fell due, and is found again ten minutes later.
  post({ kind: "reminder", title: "Call mum", due: clock.now() + MINUTE });
  await proactive.checkReminders();
  await clock.advance(11 * MINUTE);
  await proactive.checkReminders();
  assert.equal(fake.state.said.length, 0);
  assert.equal(await proactive.presence({ asleepSeconds: 600 }), "not-shown", "the card fails like the reminder did, and is kept for later");
  fake.state.failing.clear();
  assert.equal(await proactive.presence({ asleepSeconds: 600 }), "caught-up");
  assert.deepEqual(fake.state.said.map((caption) => [caption.text, caption.details]), [
    ["While you were away", [{ label: "Missed", text: "Call mum, 10 minutes ago · Feed the cat, 11 minutes ago" }]],
  ]);
});

test("what was done or removed before anyone came back is not told", async (t) => {
  const { proactive, clock, fake, post, brain } = await startProactive(t, [], { greet: false });
  const done = post({ kind: "reminder", title: "Water the plants", due: clock.now() + MINUTE });
  const gone = post({ kind: "reminder", title: "Feed the cat", due: clock.now() + MINUTE });
  await proactive.checkReminders();
  fake.state.automation.sleeping = true;
  await clock.advance(MINUTE);
  await proactive.checkReminders();
  fake.state.automation.sleeping = false;
  fake.state.board.patch(done.id, { done: true });
  fake.state.board.delete(gone.id);
  assert.equal(await proactive.presence({ asleepSeconds: 600 }), "off", "nothing is left to tell, and greeting is off");
  assert.equal(fake.state.said.filter((caption) => caption.shown).length, 0);
  assert.equal(brain.runs.length, 0);
});

test("a presence event in the morning shows the briefing, and \"dismiss those\" then knows what it named", async (t) => {
  const { call, ask, brain, mirror, events, clock } = await startCompanion(
    t,
    [{ calls: [{ tool: "board_update", args: { id: "ab12cd34", done: true } }], text: "Dismissed." }],
    { morningBriefing: true },
  );
  mirror.state.board.put("ab12cd34", { kind: "reminder", title: "Stretch", due: clock.now() - 10 * 60 * MINUTE }, "Kitchen agent");
  const answer = await call("POST", "/v1/event", { body: JSON.stringify({ type: "presence", at: 1790990000000, asleepSeconds: 1800 }) });
  assert.equal(answer.status, 202);
  await until(() => events().some((entry) => entry.event === "greeting"), "the presence to be dealt with");
  assert.equal(events().find((entry) => entry.event === "greeting").outcome, "briefed");
  assert.deepEqual(mirror.state.said.map((caption) => [caption.text, caption.kind, caption.details.map((row) => row.label)]), [
    ["Good morning", "notice", ["Weather", "Missed"]],
  ]);
  const listed = (await call("GET", "/v1/activity")).body.entries[0];
  assert.deepEqual([listed.source, listed.reply, listed.details[1]], ["presence", "Good morning", { label: "Missed", text: "Stretch, yesterday 9:12 PM" }]);
  assert.equal(brain.runs.length, 0);
  assert.equal((await ask("dismiss those")).body.reply, "Dismissed.");
  assert.match(brain.runs[0].prompt, /A moment ago the glass showed this briefing: Good morning .*\nThe missed item was: "Stretch" \(id ab12cd34\)\./);
  assert.equal(mirror.state.board.items[0].done, true);
});
