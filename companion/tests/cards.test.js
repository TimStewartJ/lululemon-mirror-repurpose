import assert from "node:assert/strict";
import test from "node:test";
import { until } from "./fakes/clock.js";
import { speech } from "./fakes/stt.js";
import { startCompanion } from "./helpers.js";

const HOUR = 60 * 60 * 1000;
const WEATHER = { label: "Weather", text: "Overcast, 12° now. High 16°, rain likely by 11 AM." };

/** Sends a greeting the way Mirror Home does when it recognised it by itself. */
function shortcut(companion, name, text = name.replace("-", " ")) {
  return companion.call("POST", "/v1/ask", { body: JSON.stringify({ text, source: "shortcut", shortcut: name }) });
}

/** Puts an item on the fake mirror's board with an id the test knows. */
function post(companion, id, item) {
  return companion.mirror.state.board.put(id, item, "Kitchen agent").item;
}

test("a greeting the mirror recognised is answered with a briefing, without the model or a recording", async (t) => {
  const companion = await startCompanion(t);
  const { clock, mirror, brain, stt, call } = companion;
  post(companion, "ab12cd34", { kind: "reminder", title: "Stretch", due: clock.now() - 10 * HOUR - 12 * 60_000 });
  post(companion, "dent0001", { kind: "reminder", title: "Dentist", due: clock.now() + 8 * HOUR - 12 * 60_000 });
  post(companion, "milk0001", { kind: "todo", title: "Buy milk" });
  await until(() => mirror.requests().length > 0, "the first look at the mirror");
  const before = mirror.requests().length;
  const { status, body } = await shortcut(companion, "good-morning");
  assert.equal(status, 200);
  assert.match(body.id, /^ask-/);
  assert.deepEqual({ ...body, id: "" }, {
    id: "",
    heard: "good morning",
    reply: "Good morning",
    details: [
      WEATHER,
      { label: "Today", text: "Dentist 3:00 PM" },
      { label: "Missed", text: "Stretch, yesterday 9:00 PM" },
      { label: "To do", text: "Buy milk" },
    ],
    ignored: false,
    reason: "",
    listen: false,
    acted: [],
    ms: { stt: 0, agent: 0, total: 0 },
    seconds: 16,
  });
  assert.equal(brain.runs.length, 0, "no model");
  assert.equal(brain.prepared.length, 0, "and no session opened ahead for one");
  assert.equal(stt.heard, 0, "no speech-to-text");
  // It reads the status and the board, and writes nothing: no caption either, the answer is the card.
  assert.deepEqual(mirror.requests().slice(before).map((request) => `${request.method} ${request.path}`).sort(), [
    "GET /api/v1/board/items?limit=100",
    "GET /api/v1/status",
  ]);
  assert.equal(mirror.writes().length, 0);

  const [entry] = (await call("GET", "/v1/activity")).body.entries;
  assert.deepEqual(Object.keys(entry), ["at", "source", "heard", "reply", "acted", "ignored", "reason", "ms", "details"]);
  assert.deepEqual([entry.source, entry.heard, entry.reply, entry.details.length], ["shortcut", "good morning", "Good morning", 4]);
  assert.equal((await call("GET", "/v1/health")).body.last, null, "health is as it was: it shows what went to the model");
});

test("each greeting has its own headline and rows", async (t) => {
  const companion = await startCompanion(t);
  post(companion, "dent0001", { kind: "reminder", title: "Dentist", due: companion.clock.now() + 8 * HOUR });
  const answers = {};
  for (const name of ["good-morning", "good-afternoon", "good-evening", "good-night", "home"]) {
    const { body } = await shortcut(companion, name);
    answers[name] = [body.reply, body.details.map((row) => row.label), body.seconds];
  }
  assert.deepEqual(answers, {
    "good-morning": ["Good morning", ["Weather", "Today"], 12],
    "good-afternoon": ["Good afternoon", ["Weather", "Later"], 12],
    "good-evening": ["Good evening", ["Weather", "Tonight"], 12],
    "good-night": ["Good night", ["Tomorrow", "Tomorrow"], 10],
    home: ["Welcome home", ["Weather", "Later"], 12],
  });
  assert.equal(companion.brain.runs.length, 0);
});

test("a shortcut this companion does not know, or none, is taken as typed words", async (t) => {
  const companion = await startCompanion(t, [{ text: "Happy birthday." }, { text: "Good morning." }, { text: "Hello." }]);
  const ask = (body) => companion.call("POST", "/v1/ask", { body: JSON.stringify(body) });
  const unknown = (await ask({ text: "happy birthday", source: "shortcut", shortcut: "happy-birthday" })).body;
  assert.deepEqual([unknown.reply, unknown.details], ["Happy birthday.", []]);
  const none = (await ask({ text: "good morning", source: "shortcut" })).body;
  assert.equal(none.reply, "Good morning.");
  // The shortcut's name alone does not make a shortcut of words typed in the controls.
  const typed = (await ask({ text: "good morning", source: "controls", shortcut: "good-morning" })).body;
  assert.equal(typed.reply, "Hello.");
  assert.equal(companion.brain.runs.length, 3);
  assert.match(companion.brain.runs[0].prompt, /^Typed to you in the phone controls, so certainly meant for you:\n"happy birthday"/);
  const entries = (await companion.call("GET", "/v1/activity")).body.entries;
  assert.deepEqual(entries.map((entry) => entry.source), ["controls", "controls", "controls"]);
});

test("a shortcut does not wait behind people's requests and is never turned away as one too many", async (t) => {
  let release;
  const held = new Promise((resolve) => (release = resolve));
  const companion = await startCompanion(t, [{ before: () => held, text: "First." }, { text: "Second." }]);
  const { ask, say, brain } = companion;
  const first = ask("first");
  await until(() => brain.runs.length === 1, "the first request to reach the model");
  const second = say(speech("Mirror, second"));
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal((await ask("third")).status, 429);
  const answer = await shortcut(companion, "home");
  assert.equal(answer.status, 200);
  assert.equal(answer.body.reply, "Welcome home");
  assert.equal((await ask("fourth")).status, 429, "and it took nobody's place");
  release();
  assert.equal((await first).body.reply, "First.");
  assert.equal((await second).body.reply, "Second.");
});

test("a shortcut leaves an open conversation as it is", async (t) => {
  const companion = await startCompanion(t, [
    { text: "Which film: the flowers or the seasons?" },
    { calls: [{ tool: "set_background", args: { video: "luminous-flowers-spatial-180s" } }], text: "The flowers are on." },
  ]);
  const { say, brain, clock } = companion;
  assert.equal((await say(speech("Mirror, change the film"))).body.listen, true);
  await clock.advance(60_000);
  assert.equal((await shortcut(companion, "good-evening")).body.listen, false);
  await clock.advance(59_000);
  assert.equal(brain.ended, 0, "the shortcut neither closed the conversation nor made it last longer");
  const answer = (await say(speech("the flowers"), { addressed: "follow-up", id: "utt-2" })).body;
  assert.equal(answer.reply, "The flowers are on.");
  assert.equal(brain.runs[1].fresh, false);
  assert.match(brain.runs[1].prompt, /^You asked: "Which film: the flowers or the seasons\?"/);
  await clock.advance(120_000);
  assert.equal(brain.ended, 1);
});

test("when the mirror cannot be read, a shortcut is answered with the plain greeting and the reason", async (t) => {
  const companion = await startCompanion(t);
  const { mirror, call } = companion;
  await until(() => mirror.requests().length > 0);
  mirror.state.failing.set("/api/v1/status", 500);
  const refused = await shortcut(companion, "good-night");
  assert.equal(refused.status, 200);
  assert.deepEqual([refused.body.reply, refused.body.details, refused.body.seconds], ["Good night", [], undefined]);
  assert.match(refused.body.error, /Mirror Home could not complete the request/);
  mirror.state.dropNext = 1000;
  const unreachable = (await shortcut(companion, "home")).body;
  assert.deepEqual([unreachable.reply, unreachable.details, unreachable.ignored], ["Welcome home", [], false]);
  assert.match(unreachable.error, /cannot be reached/);
  assert.match((await call("GET", "/v1/activity")).body.entries[0].error, /cannot be reached/);
});

test("a board that cannot be read leaves a shortcut its weather", async (t) => {
  const companion = await startCompanion(t);
  companion.mirror.state.failing.set("/api/v1/board/items", 500);
  const { body } = await shortcut(companion, "good-morning");
  assert.deepEqual(body.details, [WEATHER]);
  assert.equal(body.error, undefined);
});

test("after a briefing that named missed items, \"dismiss those\" is told which they were", async (t) => {
  const companion = await startCompanion(t, [
    { text: "It is 7:12." },
    {
      calls: [
        { tool: "board_update", args: { id: "ab12cd34", done: true } },
        { tool: "board_update", args: { id: "pill0001", done: true } },
      ],
      text: "Both dismissed.",
    },
    { text: "Nothing is left." },
  ]);
  const { ask, brain, mirror, clock } = companion;
  post(companion, "ab12cd34", { kind: "reminder", title: "Stretch", due: clock.now() - 10 * HOUR });
  post(companion, "pill0001", { kind: "todo", title: "Take the \"blue\" pills", due: clock.now() - HOUR });
  post(companion, "milk0001", { kind: "todo", title: "Buy milk" });
  await shortcut(companion, "good-morning");

  // Any request in the three minutes after it carries the briefing, not only the next one.
  await ask("what time is it?");
  await clock.advance(60_000);
  assert.equal((await ask("dismiss those")).body.reply, "Both dismissed.");
  for (const run of brain.runs.slice(0, 2)) {
    assert.match(
      run.prompt,
      /\n\nA moment ago the glass showed this briefing: Good morning \/ Weather: Overcast, 12° now\. High 16°, rain likely by 11 AM\. \/ Missed: Take the "blue" pills, 1 hour ago · Stretch, yesterday 9:12 PM \/ To do: Buy milk\nThe missed items were: "Take the "blue" pills" \(id pill0001\), "Stretch" \(id ab12cd34\)\. If the person now dismisses or clears "those", "them" or "that", or says they did it or got it, mark these done with board_update\.\n\nThe mirror now:\n\{/,
    );
  }
  assert.match(brain.runs[0].system, /To dismiss, clear or tick off a reminder or a to-do means the same: mark it done with board_update\./);
  assert.deepEqual(mirror.state.board.items.map((item) => [item.id, item.done]), [["ab12cd34", true], ["pill0001", true], ["milk0001", false]]);
  // Once they were dealt with, the briefing is out of date and is not told again.
  await ask("anything else?");
  assert.ok(!brain.runs[2].prompt.includes("A moment ago"));
});

test("a briefing is forgotten after three minutes, and one without missed items asks for nothing", async (t) => {
  const companion = await startCompanion(t, [{ text: "It is 7:12." }, { text: "It is 7:15." }]);
  const { ask, brain, clock } = companion;
  post(companion, "milk0001", { kind: "todo", title: "Buy milk" });
  await shortcut(companion, "home");
  await ask("what time is it?");
  assert.match(brain.runs[0].prompt, /\n\nA moment ago the glass showed this briefing: Welcome home \/ Weather: Overcast, 12° now\. \/ To do: Buy milk\n\nThe mirror now:/);
  await clock.advance(180_000);
  await ask("what time is it?");
  assert.ok(!brain.runs[1].prompt.includes("A moment ago"));
});

test("every answer has details, empty when it is one line", async (t) => {
  const overheard = { calls: [{ tool: "ignore", args: { reason: "talk" } }] };
  const { ask, say } = await startCompanion(t, [{ text: "It is 7:12." }, overheard, overheard]);
  const plain = (await ask("what time is it?")).body;
  assert.deepEqual([plain.details, "seconds" in plain], [[], false]);
  assert.deepEqual((await say(speech("so I told her we should go"))).body.details, []);
  assert.deepEqual((await say(speech("Mirror."), { id: "utt-2" })).body.details, []);
});

test("the model answers a greeting in other words with the briefing tool, and the card is the answer", async (t) => {
  const companion = await startCompanion(t, [
    { calls: [{ tool: "briefing", args: { kind: "catch-up" } }, { tool: "set_power", args: { state: "asleep" } }], text: "This is not shown." },
    { calls: [{ tool: "board_update", args: { id: "ab12cd34", done: true } }], text: "Dismissed." },
  ]);
  const { ask, say, brain, mirror, clock, call } = companion;
  post(companion, "ab12cd34", { kind: "reminder", title: "Stretch", due: clock.now() - 2 * HOUR });
  const { body } = await say(speech("Mirror, what did I miss?"));
  assert.deepEqual({ ...body, ms: null }, {
    id: "utt-1",
    heard: "what did I miss?",
    reply: "Here's where things stand",
    details: [
      { label: "Now", text: "Overcast, 12° now. High 16°, rain likely by 11 AM." },
      { label: "Missed", text: "Stretch, 2 hours ago" },
    ],
    ignored: false,
    reason: "",
    listen: false,
    acted: ["briefing"],
    ms: null,
    seconds: 12,
  });
  assert.equal(mirror.state.automation.sleeping, false, "the briefing ends the turn");
  assert.equal(brain.runs.length, 1, "a card is an answer: the model is not asked again");
  assert.deepEqual(brain.runs[0].results, [
    { shown: { headline: "Here's where things stand", rows: body.details } },
  ]);
  assert.match(brain.runs[0].system, /call briefing and nothing else/);
  assert.deepEqual((await call("GET", "/v1/activity")).body.entries[0].details, body.details);
  // It is the last briefing, as one from a shortcut is.
  await ask("got it");
  assert.match(brain.runs[1].prompt, /The missed item was: "Stretch" \(id ab12cd34\)\. .* mark it done with board_update\./);
  assert.equal(mirror.state.board.items[0].done, true);
});

test("the briefing tool reads the mirror afresh and takes every kind", async (t) => {
  const kinds = ["morning", "afternoon", "evening", "night", "home", "catch-up"];
  const companion = await startCompanion(t, [
    { calls: [{ tool: "board_add", args: { kind: "todo", title: "Buy milk" } }, { tool: "briefing", args: { kind: "morning" } }] },
    ...kinds.map((kind) => ({ calls: [{ tool: "briefing", args: { kind } }] })),
  ]);
  const added = (await companion.ask("add milk and good morning")).body;
  assert.deepEqual(added.acted, ["board_add", "briefing"]);
  assert.deepEqual(added.details.at(-1), { label: "To do", text: "Buy milk" }, "what the turn itself added is in the card");
  const headlines = [];
  for (const kind of kinds) headlines.push((await companion.ask(`a greeting for ${kind}`)).body.reply);
  assert.deepEqual(headlines, ["Good morning", "Good afternoon", "Good evening", "Good night", "Welcome home", "Here's where things stand"]);
});

test("a list is answered as a card with the present tool", async (t) => {
  const rows = [
    { label: "7:00 AM", text: "Take out the trash" },
    { label: "To do", text: "Buy milk · Call the plumber" },
    { text: "**Water** the plants\n(the ferns too)" },
  ];
  const companion = await startCompanion(t, [{ calls: [{ tool: "present", args: { headline: "Four things on your list", rows, seconds: 12 } }], text: "ignored" }]);
  const { body } = await companion.ask("what's on my list?");
  assert.deepEqual({ ...body, id: "", ms: null }, {
    id: "",
    heard: "what's on my list?",
    reply: "Four things on your list",
    details: [
      { label: "7:00 AM", text: "Take out the trash" },
      { label: "To do", text: "Buy milk · Call the plumber" },
      { label: "", text: "Water the plants (the ferns too)" },
    ],
    ignored: false,
    reason: "",
    listen: false,
    acted: ["present"],
    ms: null,
    seconds: 12,
  });
  assert.equal(companion.brain.runs.length, 1);
  assert.deepEqual(companion.brain.runs[0].results, [{ shown: true }]);
  assert.equal(companion.mirror.state.said.length, 0, "the card travels in the answer, not through say");
  assert.match(companion.brain.runs[0].system, /you show as a card with present/);
  assert.deepEqual((await companion.call("GET", "/v1/activity")).body.entries[0].details, body.details);
});

test("a card that breaks the limits is handed back once, and the turn goes on", async (t) => {
  const long = "Call the plumber about the dripping tap in the upstairs bathroom before the weekend, and the boiler too";
  const companion = await startCompanion(t, [
    {
      calls: [
        { tool: "present", args: { headline: "Your list", rows: [{ label: "To do", text: long }] } },
        { tool: "present", args: { headline: "Your list", rows: [{ label: "To do", text: "Call the plumber about the tap and the boiler" }] } },
      ],
    },
  ]);
  const { body } = await companion.ask("what's on my list?");
  assert.deepEqual(body.details, [{ label: "To do", text: "Call the plumber about the tap and the boiler" }]);
  assert.deepEqual(body.acted, ["present", "present"]);
  const [refusal, accepted] = companion.brain.runs[0].results;
  assert.equal(
    refusal.error,
    "Nothing was shown. The text of row 1 has 103 characters and may have 90: shorten it or split it over two rows. " +
      "Call present once more with that put right.",
  );
  assert.deepEqual(accepted, { shown: true });
  // Which limit was broken is logged, to see where the model's cards go wrong.
  const logged = companion.events().filter((entry) => entry.event === "card.limits");
  assert.deepEqual(logged.map((entry) => [entry.refused, entry.problems.length]), [[true, 1]]);
});

test("a turn may change something and then answer with a card, and a card's headline may ask", async (t) => {
  const companion = await startCompanion(t, [
    {
      calls: [
        { tool: "board_add", args: { kind: "todo", title: "Buy milk" } },
        { tool: "present", args: { headline: "Milk is on your list. Anything else?", rows: [{ label: "To do", text: "Buy milk" }] } },
      ],
    },
  ]);
  const { body } = await companion.ask("add milk and show my list");
  assert.deepEqual([body.reply, body.listen, body.acted], ["Milk is on your list. Anything else?", true, ["board_add", "present"]]);
  assert.equal(companion.mirror.state.board.items[0].title, "Buy milk");
});
