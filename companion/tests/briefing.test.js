import assert from "node:assert/strict";
import test from "node:test";
import { buildAwayCard, buildBriefing, createBriefingMemory, reminderCard } from "../src/briefing.js";
import { buildState } from "../src/state.js";
import { fakeClock } from "./fakes/clock.js";

const HOUR = 60 * 60 * 1000;
const at = (local) => Date.parse(local);
/** Saturday 3 October 2026 on a mirror in Los Angeles, seven hours behind UTC. */
const day = (time) => at(`2026-10-03T${time}:00-07:00`);
const dayBefore = (time) => at(`2026-10-02T${time}:00-07:00`);
const dayAfter = (time) => at(`2026-10-04T${time}:00-07:00`);
const midnight = at("2026-10-03T00:00:00-07:00");

let nextId = 0;
const item = (kind, title, due = null, more = {}) => ({ id: `item${String(++nextId).padStart(4, "0")}`, kind, title, due, done: false, priority: "normal", ...more });
const reminder = (title, due, more) => item("reminder", title, due, more);
const todo = (title, due, more) => item("todo", title, due, more);

const daily = (index, high, low, rain, condition, start = midnight) => ({
  time: start + index * 24 * HOUR, high, low, precipitationProbability: rain, condition,
  sunrise: start + index * 24 * HOUR + 7 * HOUR, sunset: start + index * 24 * HOUR + 18.75 * HOUR,
});
/** A clear day, in Fahrenheit, with no hourly forecast. */
const FAIR = {
  state: "ready",
  stale: false,
  data: {
    units: { temperature: "°F" },
    current: { temperature: 62.3, condition: "Clear" },
    daily: [daily(0, 91.2, 58.4, 5, "Clear"), daily(1, 85, 57.6, 10, "Mostly clear")],
    hourly: [],
  },
};
/** A wet day: rain grows likely from three in the afternoon. */
const WET = {
  state: "ready",
  stale: false,
  data: {
    units: { temperature: "°F" },
    current: { temperature: 54, condition: "Light rain" },
    daily: [daily(0, 58, 50, 70, "Light rain"), daily(1, 61, 49, 40, "Overcast")],
    hourly: [8, 9, 10, 11, 12, 13, 14, 15, 16, 17].map((hour) => ({ time: midnight + hour * HOUR, precipitationProbability: hour >= 15 ? 80 : 20 })),
  },
};

/** The mirror's state as fetchState would hand it over, from fixed facts. */
function stateAt(now, { items = [], weather = FAIR, clock24Hour = false, offset = -420, board = true } = {}) {
  return buildState({
    status: { utcOffsetMinutes: offset, timeZone: "America/Los_Angeles", clock24Hour, weather, automation: {} },
    layout: null,
    board: board ? { items, version: 1 } : null,
    films: null,
    now,
  });
}

const rows = (briefing) => briefing.details.map((row) => `${row.label}: ${row.text}`);

test("a morning briefing has the weather, what is due today, what was missed and what is to do", () => {
  const stretch = reminder("Stretch", dayBefore("21:00"));
  const state = stateAt(day("07:12"), {
    items: [
      stretch,
      reminder("Start dishwasher", day("21:00")),
      reminder("Dentist", day("15:00")),
      todo("Buy milk"),
      todo("Call the plumber"),
      reminder("Take out the trash", dayAfter("07:00")),
      item("note", "Welcome home"),
    ],
  });
  assert.deepEqual(buildBriefing(state, "good-morning"), {
    reply: "Good morning",
    details: [
      { label: "Weather", text: "Clear, 62° now. High 91°, no rain." },
      { label: "Today", text: "Dentist 3:00 PM · Start dishwasher 9:00 PM" },
      { label: "Missed", text: "Stretch, yesterday 9:00 PM" },
      { label: "To do", text: "Buy milk · Call the plumber" },
    ],
    seconds: 16,
    missed: [stretch.id],
  });
});

test("rain worth knowing about is told with its hour, or with its chance when the hours are not known", () => {
  const withHours = buildBriefing(stateAt(day("07:12"), { weather: WET }), "good-morning");
  assert.equal(withHours.details[0].text, "Light rain, 54° now. High 58°, rain likely by 3 PM.");
  const noHours = structuredClone(WET);
  noHours.data.hourly = [];
  assert.equal(buildBriefing(stateAt(day("07:12"), { weather: noHours }), "good-morning").details[0].text, "Light rain, 54° now. High 58°, 70% chance of rain.");
  // Under 30 % is not worth a word, and neither is rain that the hours show to be over.
  const light = structuredClone(noHours);
  light.data.daily[0].precipitationProbability = 29;
  assert.match(buildBriefing(stateAt(day("07:12"), { weather: light }), "good-morning").details[0].text, /High 58°, no rain\.$/);
  const over = structuredClone(WET);
  over.data.hourly = [18, 19, 20, 21, 22, 23].map((hour) => ({ time: midnight + hour * HOUR, precipitationProbability: 10 }));
  assert.match(buildBriefing(stateAt(day("17:30"), { weather: over }), "good-afternoon").details[0].text, /no rain\.$/);
  const snow = structuredClone(noHours);
  snow.data.daily[0].condition = "Light snow";
  assert.match(buildBriefing(stateAt(day("07:12"), { weather: snow }), "good-morning").details[0].text, /70% chance of snow\.$/);
});

test("times and hours are written the way the mirror's clock shows them", () => {
  const items = [reminder("Dentist", day("15:00")), reminder("Stretch", dayBefore("21:05"))];
  const twelve = rows(buildBriefing(stateAt(day("07:12"), { items, weather: WET }), "good-morning"));
  assert.deepEqual(twelve.slice(0, 3), [
    "Weather: Light rain, 54° now. High 58°, rain likely by 3 PM.",
    "Today: Dentist 3:00 PM",
    "Missed: Stretch, yesterday 9:05 PM",
  ]);
  const twentyFour = rows(buildBriefing(stateAt(day("07:12"), { items, weather: WET, clock24Hour: true }), "good-morning"));
  assert.deepEqual(twentyFour.slice(0, 3), [
    "Weather: Light rain, 54° now. High 58°, rain likely by 15:00.",
    "Today: Dentist 15:00",
    "Missed: Stretch, yesterday 21:05",
  ]);
  const early = buildBriefing(stateAt(day("07:12"), { items: [reminder("Tea", day("09:30")), reminder("Lunch", day("12:00"))], clock24Hour: true }), "home");
  assert.equal(early.details[1].text, "Tea 09:30 · Lunch 12:00");
  const noon = buildBriefing(stateAt(day("07:12"), { items: [reminder("Lunch", day("12:00")), reminder("Bed", dayAfter("00:15"))] }), "catch-up");
  assert.equal(noon.details[1].text, "Lunch 12:00 PM · Bed tomorrow 12:15 AM");
});

test("weather that is stale, missing or not set up is left out, not told as news", () => {
  const items = [todo("Buy milk"), reminder("Stretch", dayBefore("21:00"))];
  const stale = { ...FAIR, stale: true };
  const none = { state: "loading", stale: false, data: null };
  const unconfigured = { state: "unconfigured", stale: false, data: null };
  for (const weather of [stale, none, unconfigured, null]) {
    for (const kind of ["good-morning", "good-afternoon", "good-evening", "good-night", "home", "catch-up"]) {
      const briefing = buildBriefing(stateAt(day("07:12"), { items, weather }), kind);
      assert.ok(!briefing.details.some((row) => /°|rain/.test(row.text)), `${kind}: ${JSON.stringify(briefing.details)}`);
      assert.ok(briefing.details.length >= 1);
    }
  }
  // Weather with holes says what it knows and no more.
  const bare = { state: "ready", stale: false, data: { current: { temperature: 61.6 }, daily: [], hourly: [] } };
  assert.deepEqual(rows(buildBriefing(stateAt(day("07:12"), { weather: bare }), "good-morning")), ["Weather: 62° now.", "Today: Nothing on your list."]);
  const noCurrent = { state: "ready", stale: false, data: { daily: [daily(0, 70, 50, 0, "Clear")] } };
  assert.equal(buildBriefing(stateAt(day("07:12"), { weather: noCurrent }), "good-morning").details[0].text, "High 70°, no rain.");
  const freezing = { state: "ready", stale: false, data: { current: { temperature: -0.4, condition: "Fog" }, daily: [daily(0, -0.2, -7.5, 0, "Fog")] } };
  assert.equal(buildBriefing(stateAt(day("07:12"), { weather: freezing }), "good-morning").details[0].text, "Fog, 0° now. High 0°, no rain.");
});

test("a morning with nothing on the board still has its weather and says so", () => {
  assert.deepEqual(buildBriefing(stateAt(day("07:12")), "good-morning"), {
    reply: "Good morning",
    details: [
      { label: "Weather", text: "Clear, 62° now. High 91°, no rain." },
      { label: "Today", text: "Nothing on your list." },
    ],
    seconds: 12,
    missed: [],
  });
  // Finished items and notes are nothing to do.
  const finished = [todo("Buy milk", null, { done: true }), reminder("Dentist", day("15:00"), { done: true }), item("note", "Welcome home")];
  assert.equal(buildBriefing(stateAt(day("07:12"), { items: finished }), "good-morning").details[1].text, "Nothing on your list.");
  // Something due on a later day is not "nothing", and is not today's either.
  const later = buildBriefing(stateAt(day("07:12"), { items: [reminder("Trash", dayAfter("07:00"))] }), "good-morning");
  assert.deepEqual(rows(later), ["Weather: Clear, 62° now. High 91°, no rain."]);
});

test("a board that could not be read is not spoken of", () => {
  const briefing = buildBriefing(stateAt(day("07:12"), { board: false }), "good-morning");
  assert.deepEqual(rows(briefing), ["Weather: Clear, 62° now. High 91°, no rain."]);
  assert.deepEqual(buildBriefing(stateAt(day("22:00"), { board: false, weather: null }), "good-night").details, []);
});

test("a list names three items and counts the rest", () => {
  const five = ["One", "Two", "Three", "Four", "Five"].map((title) => todo(title));
  const four = [1, 2, 3, 4].map((hour) => reminder(`Call ${hour}`, day(`${String(12 + hour)}:00`)));
  const briefing = buildBriefing(stateAt(day("07:12"), { items: [...five, ...four] }), "good-morning");
  assert.deepEqual(rows(briefing).slice(1), [
    "Today: Call 1 1:00 PM · Call 2 2:00 PM · Call 3 3:00 PM +1 more",
    "To do: One · Two · Three +2 more",
  ]);
  assert.deepEqual(rows(buildBriefing(stateAt(day("07:12"), { items: five.slice(0, 4) }), "good-morning"))[1], "To do: One · Two · Three +1 more");
  assert.deepEqual(rows(buildBriefing(stateAt(day("07:12"), { items: five.slice(0, 3) }), "good-morning"))[1], "To do: One · Two · Three");
});

test("long titles are shortened, and fewer are named, so that a row never passes 90 characters", () => {
  const long = [
    todo("Write the long overdue letter to the landlord about the heating in the back room"),
    todo("Sort through the boxes in the attic and decide what goes to the charity shop"),
    todo("Book the car in for its yearly inspection before the end of the month"),
    todo("Milk"),
  ];
  const briefing = buildBriefing(stateAt(day("07:12"), { items: long }), "good-morning");
  const text = briefing.details[1].text;
  assert.equal(text, "Write the long overdue letter to the... · Sort through the boxes in the attic... +2 more");
  assert.ok(text.length <= 90);
  for (const kind of ["good-morning", "good-afternoon", "good-evening", "good-night", "home", "catch-up"]) {
    const timed = long.map((entry, index) => ({ ...entry, kind: "reminder", due: index < 2 ? dayBefore("21:00") : dayAfter("09:00") }));
    for (const row of buildBriefing(stateAt(day("19:12"), { items: [...long, ...timed], weather: WET }), kind).details) {
      assert.ok(row.text.length >= 1 && row.text.length <= 90, `${kind} ${row.label}: ${row.text.length}`);
      assert.ok(row.label.length <= 14);
    }
  }
});

test("what was missed is told by how long ago, and after six hours by its day and time", () => {
  const missedAt = (due) => buildBriefing(stateAt(day("12:00"), { items: [reminder("Call mum", due)], weather: null }), "home").details[0].text;
  assert.equal(missedAt(day("12:00")), "Call mum, just now");
  assert.equal(missedAt(day("11:59")), "Call mum, 1 minute ago");
  assert.equal(missedAt(day("11:15")), "Call mum, 45 minutes ago");
  assert.equal(missedAt(day("11:00")), "Call mum, 1 hour ago");
  assert.equal(missedAt(day("10:00")), "Call mum, 2 hours ago");
  assert.equal(missedAt(day("06:01")), "Call mum, 6 hours ago");
  assert.equal(missedAt(day("06:00")), "Call mum, today 6:00 AM");
  assert.equal(missedAt(dayBefore("21:00")), "Call mum, yesterday 9:00 PM");
  assert.equal(missedAt(at("2026-09-30T08:30:00-07:00")), "Call mum, Wednesday 8:30 AM");
  assert.equal(missedAt(at("2026-09-12T08:30:00-07:00")), "Call mum, Sep 12 8:30 AM");
});

test("an item stays missed until it is done or gone, and the latest miss comes first", () => {
  const stretch = reminder("Stretch", dayBefore("21:00"));
  const bins = todo("Put the bins out", day("06:30"));
  const done = reminder("Take the pills", day("07:00"), { done: true });
  const state = stateAt(day("07:12"), { items: [stretch, bins, done, item("note", "Welcome home")], weather: null });
  for (const kind of ["good-morning", "good-afternoon", "good-evening", "home", "catch-up"]) {
    const briefing = buildBriefing(state, kind);
    assert.deepEqual(rows(briefing), ["Missed: Put the bins out, 42 minutes ago · Stretch, yesterday 9:00 PM"], kind);
    assert.deepEqual(briefing.missed, [bins.id, stretch.id]);
  }
  const night = buildBriefing(state, "good-night");
  assert.deepEqual(rows(night), ["Still open: Put the bins out · Stretch"]);
  assert.deepEqual(night.missed, [bins.id, stretch.id]);
  // Marked done, it is dismissed.
  stretch.done = true;
  assert.deepEqual(buildBriefing(stateAt(day("07:12"), { items: [stretch, bins], weather: null }), "home").missed, [bins.id]);
});

test("an afternoon briefing looks to the rest of the day", () => {
  const items = [reminder("Dentist", day("15:00")), reminder("Trash", dayAfter("07:00")), todo("Buy milk"), reminder("Stretch", day("09:00"))];
  assert.deepEqual(rows(buildBriefing(stateAt(day("13:05"), { items, weather: WET }), "good-afternoon")), [
    "Weather: Light rain, 54° now. Low 49° tonight, rain likely by 3 PM.",
    "Later: Dentist 3:00 PM",
    "Missed: Stretch, 4 hours ago",
    "To do: Buy milk",
  ]);
  assert.equal(buildBriefing(stateAt(day("13:05"), { items }), "good-afternoon").details[0].text, "Clear, 62° now. Low 58° tonight, no rain.");
  assert.equal(buildBriefing(stateAt(day("13:05")), "good-afternoon").reply, "Good afternoon");
  // An afternoon with nothing to tell is the greeting alone.
  assert.deepEqual(rows(buildBriefing(stateAt(day("13:05"), { weather: null }), "good-afternoon")), []);
});

test("an evening briefing has tonight and a word about tomorrow", () => {
  const items = [reminder("Start dishwasher", day("21:00")), reminder("Trash", dayAfter("07:00")), reminder("Dentist", dayAfter("15:00")), todo("Buy milk")];
  assert.deepEqual(buildBriefing(stateAt(day("18:40"), { items }), "good-evening"), {
    reply: "Good evening",
    details: [
      { label: "Weather", text: "Clear, 62° now, low 58° tonight. Tomorrow mostly clear, high 85°." },
      { label: "Tonight", text: "Start dishwasher 9:00 PM" },
      { label: "Tomorrow", text: "Trash 7:00 AM · Dentist 3:00 PM" },
    ],
    seconds: 14,
    missed: [],
  });
});

test("a good night looks to tomorrow, and is short because the display then goes dark", () => {
  const stretch = reminder("Stretch", day("21:00"));
  const items = [reminder("Dentist", dayAfter("15:00")), reminder("Trash", dayAfter("07:00")), stretch, todo("Buy milk"), reminder("Next week", at("2026-10-09T09:00:00-07:00"))];
  assert.deepEqual(buildBriefing(stateAt(day("22:10"), { items }), "good-night"), {
    reply: "Good night",
    details: [
      { label: "Tomorrow", text: "Mostly clear, high 85°, low 58°, no rain." },
      { label: "First up", text: "Trash 7:00 AM · Dentist 3:00 PM" },
      { label: "Still open", text: "Stretch · Buy milk" },
    ],
    seconds: 10,
    missed: [stretch.id],
  });
  assert.deepEqual(buildBriefing(stateAt(day("22:10")), "good-night"), {
    reply: "Good night",
    details: [
      { label: "Tomorrow", text: "Mostly clear, high 85°, low 58°, no rain." },
      { label: "Tomorrow", text: "Nothing planned." },
    ],
    seconds: 10,
    missed: [],
  });
  assert.deepEqual(rows(buildBriefing(stateAt(day("22:10"), { weather: WET }), "good-night"))[0], "Tomorrow: Overcast, high 61°, low 49°, 40% chance of rain.");
  // With something still open there is no "nothing planned", though nothing is due tomorrow.
  assert.deepEqual(rows(buildBriefing(stateAt(day("22:10"), { items: [todo("Buy milk")], weather: null }), "good-night")), ["Still open: Buy milk"]);
  assert.equal(buildBriefing(stateAt(day("22:10"), { weather: null }), "good-night").seconds, 10);
});

test("coming home gets the weather of the moment and what is left of the day", () => {
  const items = [reminder("Start dishwasher", day("21:00")), todo("Buy milk"), reminder("Trash", dayAfter("07:00"))];
  assert.deepEqual(buildBriefing(stateAt(day("17:45"), { items }), "home"), {
    reply: "Welcome home",
    details: [
      { label: "Weather", text: "Clear, 62° now." },
      { label: "Later", text: "Start dishwasher 9:00 PM" },
      { label: "To do", text: "Buy milk" },
    ],
    seconds: 14,
    missed: [],
  });
});

test("a catch-up fits its weather to the hour and names the day of what is not due today", () => {
  const items = [reminder("Start dishwasher", day("21:00")), reminder("Trash", dayAfter("07:00")), reminder("Later on", at("2026-10-06T09:00:00-07:00"))];
  const weatherAt = (time) => buildBriefing(stateAt(day(time), { items }), "catch-up").details[0];
  assert.deepEqual(weatherAt("09:00"), { label: "Now", text: "Clear, 62° now. High 91°, no rain." });
  assert.deepEqual(weatherAt("14:00"), { label: "Now", text: "Clear, 62° now. Low 58° tonight, no rain." });
  assert.deepEqual(weatherAt("19:00"), { label: "Now", text: "Clear, 62° now, low 58° tonight. Tomorrow mostly clear, high 85°." });
  assert.deepEqual(weatherAt("01:30"), { label: "Now", text: "Clear, 62° now. High 91°, no rain." });
  const briefing = buildBriefing(stateAt(day("19:00"), { items }), "catch-up");
  assert.equal(briefing.reply, "Here's where things stand");
  assert.deepEqual(briefing.details[1], { label: "Next", text: "Start dishwasher 9:00 PM · Trash tomorrow 7:00 AM" });
  assert.deepEqual(rows(buildBriefing(stateAt(day("19:00"), { weather: null }), "catch-up")), ["Next: Nothing on your list."]);
});

test("today and tomorrow are the mirror's own days, wherever the companion runs", () => {
  // 23:30 on Saturday in Berlin is 21:30 UTC: by UTC's date both items would be "today".
  const now = at("2026-10-03T23:30:00+02:00");
  const items = [reminder("Lock the door", at("2026-10-03T23:45:00+02:00")), reminder("Feed the cat", at("2026-10-04T00:15:00+02:00"))];
  const weather = structuredClone(FAIR);
  const berlinMidnight = at("2026-10-03T00:00:00+02:00");
  weather.data.daily = [daily(0, 14, 6, 0, "Clear", berlinMidnight), daily(1, 11, 4, 0, "Overcast", berlinMidnight)];
  const state = stateAt(now, { items, weather, offset: 120, clock24Hour: true });
  assert.deepEqual(rows(buildBriefing(state, "good-evening")), [
    "Weather: Clear, 62° now, low 4° tonight. Tomorrow overcast, high 11°.",
    "Tonight: Lock the door 23:45",
    "Tomorrow: Feed the cat 00:15",
  ]);
  assert.deepEqual(rows(buildBriefing(state, "good-night")).slice(0, 2), ["Tomorrow: Overcast, high 11°, low 4°, no rain.", "First up: Feed the cat 00:15"]);
});

test("a briefing is shown 8 seconds and 2 more for each row, at most 20", () => {
  const items = [reminder("Dentist", day("15:00")), reminder("Stretch", dayBefore("21:00")), todo("Buy milk")];
  assert.equal(buildBriefing(stateAt(day("07:12"), { weather: null, board: false }), "good-morning").seconds, 8);
  assert.equal(buildBriefing(stateAt(day("07:12"), { items: items.slice(0, 1), weather: null }), "good-morning").seconds, 10);
  assert.equal(buildBriefing(stateAt(day("07:12"), { items }), "good-morning").seconds, 16);
  assert.equal(buildBriefing(stateAt(day("07:12"), { items }), "good-night").seconds, 10);
});

test("building a briefing leaves the state as it was", () => {
  const state = stateAt(day("07:12"), { items: [reminder("Dentist", day("15:00")), reminder("Stretch", dayBefore("21:00")), todo("Buy milk")], weather: WET });
  const before = JSON.stringify(state);
  for (const kind of ["good-morning", "good-afternoon", "good-evening", "good-night", "home", "catch-up"]) buildBriefing(state, kind);
  buildAwayCard(state, state.items.map((entry) => entry.id));
  assert.equal(JSON.stringify(state), before);
});

test("the card for someone who was away names what fell due meanwhile, and what is due soon", () => {
  const plants = reminder("Water the plants", day("09:30"));
  const pills = reminder("Take the pills", day("08:00"));
  const older = reminder("Stretch", dayBefore("21:00"));
  const dentist = reminder("Dentist", day("12:30"));
  const far = reminder("Start dishwasher", day("21:00"));
  const state = stateAt(day("10:00"), { items: [plants, pills, older, dentist, far] });
  assert.deepEqual(buildAwayCard(state, [plants.id, pills.id]), {
    reply: "While you were away",
    details: [
      { label: "Missed", text: "Water the plants, 30 minutes ago · Take the pills, 2 hours ago" },
      { label: "Next", text: "Dentist 12:30 PM" },
    ],
    seconds: 12,
    missed: [plants.id, pills.id],
  });
  // Nothing is due within three hours: one row.
  assert.deepEqual(buildAwayCard(stateAt(day("10:00"), { items: [plants, far] }), new Set([plants.id])).details, [
    { label: "Missed", text: "Water the plants, 30 minutes ago" },
  ]);
  // What was done or removed meanwhile, or moved to a later time, needs no telling.
  const moved = { ...plants, due: day("11:00") };
  assert.equal(buildAwayCard(stateAt(day("10:00"), { items: [{ ...pills, done: true }, moved] }), [plants.id, pills.id, "gone1234"]), null);
});

test("a reminder's card is its title, begun with a capital, and the time it was due", () => {
  assert.deepEqual(reminderCard("start the dishwasher", day("21:00"), { offsetMinutes: -420, clock24Hour: false }), {
    text: "Start the dishwasher",
    details: [{ label: "Reminder", text: "Now, 9:00 PM" }],
  });
  assert.deepEqual(reminderCard("iPad charger  to Sam", day("21:00"), { offsetMinutes: -420, clock24Hour: true }), {
    text: "IPad charger to Sam",
    details: [{ label: "Reminder", text: "Now, 21:00" }],
  });
});

test("the last briefing is remembered for three minutes, with the titles of what was missed", async () => {
  const clock = fakeClock();
  const memory = createBriefingMemory(clock);
  assert.equal(memory.recall(), null);
  const stretch = reminder("Stretch", dayBefore("21:00"));
  const state = stateAt(day("07:12"), { items: [stretch, todo("Buy milk")], weather: null });
  memory.note(buildBriefing(state, "good-morning"), state);
  await clock.advance(179_999);
  assert.deepEqual(memory.recall(), {
    at: clock.now() - 179_999,
    reply: "Good morning",
    details: [
      { label: "Missed", text: "Stretch, yesterday 9:00 PM" },
      { label: "To do", text: "Buy milk" },
    ],
    missed: [{ id: stretch.id, title: "Stretch" }],
  });
  await clock.advance(1);
  assert.equal(memory.recall(), null);
  memory.note(buildBriefing(state, "good-morning"), state);
  memory.forget();
  assert.equal(memory.recall(), null);
});
