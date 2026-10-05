import assert from "node:assert/strict";
import test from "node:test";
import { fetchState } from "../src/state.js";
import { newTurn, toolsFor } from "../src/tools.js";
import { resolveZone } from "../src/tools/settings.js";
import { startTools } from "./helpers.js";

const written = (fake) => fake.writes().map((request) => [request.method, request.path, request.body]);
const FLOWERS = "9c1f44e7" + "b".repeat(56);
const SEASONS = "546e5d02" + "a".repeat(56);
const WATER = "e03b77d1" + "c".repeat(56);

/** A turn that knows the mirror's state, as every turn of a conversation does. */
async function turnWithState({ mirror, clock }) {
  return newTurn("conversation", await fetchState(mirror, clock), clock.now());
}

test("set_clock changes how the clock reads and leaves its zone alone", async (t) => {
  const { use, fake } = await startTools(t);
  assert.deepEqual(await use("set_clock", { format: "24-hour" }), {
    clock: "24-hour", zone: "America/Los_Angeles", utcOffset: "-07:00", timeNow: "07:12", changed: true,
  });
  // The zone and the offset go back as they were read, which the mirror takes as "leave the clock alone".
  assert.deepEqual(written(fake), [
    ["PUT", "/api/v1/preferences", { timeZone: "America/Los_Angeles", utcOffsetMinutes: -420, clock24Hour: true }],
  ]);
  assert.deepEqual(await use("set_clock", { format: "24-hour" }), {
    clock: "24-hour", zone: "America/Los_Angeles", utcOffset: "-07:00", timeNow: "07:12", changed: false, note: "The clock was already like that.",
  });
  assert.equal(fake.writes().length, 1);
  assert.equal((await use("set_clock", { format: "12-hour" })).timeNow, "7:12 AM");
});

test("set_clock moves the mirror to another time zone with that zone's offset of the day", async (t) => {
  const { use, fake } = await startTools(t);
  const answer = await use("set_clock", { timeZone: "america/new_york" });
  assert.equal(answer.zone, "America/New_York");
  assert.equal(answer.utcOffset, "-04:00");
  assert.equal(answer.timeNow, "10:12 AM");
  assert.match(answer.note, /Reminders keep their moment/);
  assert.deepEqual(written(fake), [
    ["PUT", "/api/v1/preferences", { timeZone: "America/New_York", utcOffsetMinutes: -240, clock24Hour: false }],
  ]);
  // Both at once, and a zone east of Greenwich with half an hour in it.
  const india = await use("set_clock", { timeZone: "Asia/Kolkata", format: "24-hour" });
  assert.deepEqual([india.utcOffset, india.timeNow, india.clock], ["+05:30", "19:42", "24-hour"]);
  // The zone it is in already is no change.
  assert.equal((await use("set_clock", { timeZone: "Asia/Kolkata" })).changed, false);
});

test("set_clock refuses a zone that does not exist, and needs something to change", async (t) => {
  const { use, fake } = await startTools(t);
  assert.match((await use("set_clock", { timeZone: "Eastern" })).error, /no time zone called "Eastern"\. Use an IANA name/);
  assert.match((await use("set_clock", {})).error, /Give format, timeZone or both/);
  assert.match((await use("set_clock", { format: "military" })).error, /The arguments are not right\. format/);
  assert.equal(fake.writes().length, 0);
});

test("resolveZone knows a zone's proper name and its offset on the day", () => {
  const october = Date.UTC(2026, 9, 3, 14, 12);
  const january = Date.UTC(2027, 0, 15, 12, 0);
  assert.deepEqual(resolveZone("Europe/Berlin", october), { name: "Europe/Berlin", offsetMinutes: 120 });
  assert.deepEqual(resolveZone("Europe/Berlin", january), { name: "Europe/Berlin", offsetMinutes: 60 });
  assert.deepEqual(resolveZone("america/los angeles", january), { name: "America/Los_Angeles", offsetMinutes: -480 });
  assert.equal(resolveZone("UTC", october).offsetMinutes, 0);
  assert.equal(resolveZone("Mars/Olympus", october), null);
});

test("set_display_rules sets the hours the display is lit", async (t) => {
  const tools = await startTools(t);
  const { use, fake } = tools;
  assert.deepEqual(await use("set_display_rules", { awakeFrom: "7:00", awakeUntil: "23:30" }, await turnWithState(tools)), {
    awakeHours: "07:00 to 23:30", sleepsWhenNobodyIsThere: "after 5 minutes", movementSensitivity: 6, changed: true,
  });
  assert.deepEqual(written(fake), [[
    "PUT", "/api/v1/automation",
    {
      enabled: true, wakeTime: "07:00", sleepTime: "23:30", wakeBrightness: 180, ambientEnabled: false, ambientMinimum: 20,
      ambientMaximum: 220, motionEnabled: true, motionTimeoutSeconds: 300, motionSensitivity: 6,
    },
  ]]);
  // One end alone keeps the other.
  assert.equal((await use("set_display_rules", { awakeUntil: "22:00" })).awakeHours, "07:00 to 22:00");
  // No hours at all, and back to the ones that were kept.
  assert.equal((await use("set_display_rules", { alwaysAwake: true })).awakeHours, "always");
  assert.equal(fake.state.automation.enabled, false);
  assert.equal((await use("set_display_rules", { alwaysAwake: false })).awakeHours, "07:00 to 22:00");
});

test("set_display_rules says when the new hours make the display go dark at once", async (t) => {
  const tools = await startTools(t);
  // The mirror's clock reads 07:12.
  const answer = await tools.use("set_display_rules", { awakeFrom: "08:00", awakeUntil: "22:00" }, await turnWithState(tools));
  assert.equal(answer.note, "It is outside those hours now, so the display goes dark in a moment.");
  const inside = await tools.use("set_display_rules", { awakeFrom: "07:00" }, await turnWithState(tools));
  assert.equal(inside.note, undefined);
});

test("set_display_rules sets whether and when the display sleeps with nobody there", async (t) => {
  const { use, fake } = await startTools(t);
  assert.deepEqual(await use("set_display_rules", { sleepWhenNobodyIsThere: false }), {
    awakeHours: "06:30 to 23:00", sleepsWhenNobodyIsThere: "no", changed: true,
  });
  assert.equal(fake.state.automation.motionEnabled, false);
  // A time for it turns it on again: it is asked of a mirror that is to do it.
  const answer = await use("set_display_rules", { afterMinutes: 10, sensitivity: 8 });
  assert.deepEqual([answer.sleepsWhenNobodyIsThere, answer.movementSensitivity], ["after 10 minutes", 8]);
  assert.deepEqual(
    [fake.state.automation.motionEnabled, fake.state.automation.motionTimeoutSeconds, fake.state.automation.motionSensitivity],
    [true, 600, 8],
  );
  assert.equal((await use("set_display_rules", { afterMinutes: 0.75 })).sleepsWhenNobodyIsThere, "after 45 seconds");
  assert.equal((await use("set_display_rules", { afterMinutes: 0.75 })).changed, false);
});

test("set_display_rules keeps a sleep that someone asked for", async (t) => {
  const { use, fake } = await startTools(t);
  await use("set_power", { state: "asleep" });
  await use("set_display_rules", { awakeUntil: "22:00" });
  assert.equal(fake.state.automation.sleeping, true);
  assert.equal(fake.state.automation.manualOverride, true, "saving the rules ended the sleep, so it was asked for again");
});

test("set_display_rules refuses what the mirror cannot do or what makes no sense", async (t) => {
  const { use, fake } = await startTools(t);
  assert.match((await use("set_display_rules", {})).error, /Give what is to change/);
  assert.match((await use("set_display_rules", { awakeFrom: "07:00", alwaysAwake: true })).error, /not both/);
  assert.match((await use("set_display_rules", { awakeFrom: "seven" })).error, /24-hour HH:MM/);
  assert.match((await use("set_display_rules", { awakeFrom: "23:00" })).error, /no time at all/);
  assert.match((await use("set_display_rules", { followRoomLight: true })).error, /no light sensor/);
  assert.match((await use("set_display_rules", { afterMinutes: 90 })).error, /The arguments are not right\. afterMinutes/);
  fake.state.automation.motion = { available: false };
  fake.state.automation.motionEnabled = false;
  assert.match((await use("set_display_rules", { sleepWhenNobodyIsThere: true })).error, /camera is not available/);
  assert.equal(fake.writes().length, 0);
  // With a sensor, the brightness can follow the room.
  fake.state.automation.ambientLightAvailable = true;
  assert.equal((await use("set_display_rules", { followRoomLight: true })).brightnessFollowsRoomLight, true);
});

test("set_weather moves the weather to the best known place of that name and names the others", async (t) => {
  const tools = await startTools(t);
  const { use, fake } = tools;
  const answer = await use("set_weather", { place: "Portland" }, await turnWithState(tools));
  assert.deepEqual(answer, {
    weather: "on", place: "Portland, Oregon, United States", units: "celsius", changed: true,
    otherPlacesOfThatName: ["Portland, Maine, United States", "Portland, Victoria, Australia"],
    forecast: "The mirror is fetching it now; the weather in the state is still the old one. Do not quote it.",
  });
  assert.deepEqual(written(fake), [[
    "PUT", "/api/v1/weather",
    { enabled: true, locationName: "Portland, Oregon, United States", units: "metric", latitude: 45.52345, longitude: -122.67621 },
  ]]);
});

test("set_weather takes a town with its state, and says when the clock is on another zone", async (t) => {
  const tools = await startTools(t);
  const { use, fake } = tools;
  const answer = await use("set_weather", { place: "Portland, Maine" }, await turnWithState(tools));
  assert.equal(answer.place, "Portland, Maine, United States");
  assert.equal(answer.otherPlacesOfThatName, undefined);
  assert.equal(
    answer.note,
    "That place is in the time zone America/New_York; the mirror's clock stays on America/Los_Angeles. Change the clock only if asked.",
  );
  assert.ok(fake.requests().some((request) => request.path === "/api/v1/weather/locations?q=Portland%2C%20Maine"));
  assert.equal(fake.state.weatherConfig.latitude, 43.65737);
});

test("set_weather asks back when the town is not where it was said to be, or is not known", async (t) => {
  const { use, fake } = await startTools(t);
  assert.equal(
    (await use("set_weather", { place: "Springfield, Ohio" })).error,
    'No "Springfield, Ohio" was found there. Places of that name: Springfield, Missouri, United States; ' +
      "Springfield, Illinois, United States; Springfield, Oregon, United States. Nothing was changed. Ask which one is meant.",
  );
  assert.match((await use("set_weather", { place: "Xanadu" })).error, /No place called "Xanadu" was found\. Nothing was changed\. Ask for the town with its state or country\./);
  fake.state.failing.set("/api/v1/weather/locations", 500);
  assert.match((await use("set_weather", { place: "Berlin" })).error, /could not look the place up just now/);
  assert.match((await use("set_weather", {})).error, /Give place, units or on/);
  assert.equal(fake.writes().length, 0);
});

test("set_weather changes the units and keeps the place", async (t) => {
  const { use, fake } = await startTools(t);
  assert.deepEqual(await use("set_weather", { units: "fahrenheit" }), {
    weather: "on", place: "Seattle", units: "fahrenheit", changed: true,
    forecast: "The mirror is fetching it now; the weather in the state is still the old one. Do not quote it.",
  });
  assert.deepEqual(fake.state.weatherConfig, { enabled: true, locationName: "Seattle", units: "us", latitude: 47.60621, longitude: -122.33207 });
  assert.deepEqual(await use("set_weather", { units: "fahrenheit" }), {
    weather: "on", place: "Seattle", units: "fahrenheit", changed: false, note: "The weather was already like that.",
  });
  assert.equal(fake.writes().length, 1);
});

test("set_weather turns the weather off, and on again at the place it had", async (t) => {
  const { use, fake } = await startTools(t);
  assert.deepEqual(await use("set_weather", { on: false }), { weather: "off", place: "Seattle", units: "celsius", changed: true });
  assert.equal(fake.state.weatherConfig.enabled, false);
  assert.equal(fake.state.weatherConfig.latitude, null, "the mirror forgets where the place lies");
  // So the place is looked up again by its name.
  const again = await use("set_weather", { on: true });
  assert.equal(again.weather, "on");
  assert.equal(again.place, "Seattle, Washington, United States");
  assert.equal(fake.state.weatherConfig.latitude, 47.60621);
  // A mirror that never had a place has to be told one.
  fake.state.weather = false;
  assert.match((await use("set_weather", { on: true })).error, /No place is set for the weather\. Ask which town/);
  assert.equal((await use("set_weather", { place: "Tokyo" })).place, "Tokyo, Japan");
});

test("set_film_schedule sets which film plays from which time of day", async (t) => {
  const { use, fake } = await startTools(t);
  const answer = await use("set_film_schedule", {
    slots: [{ at: "19:00", film: "still water" }, { at: "6:00", film: "flowers" }],
  });
  // The mirror's clock reads 07:12, so the morning's film is the one showing.
  assert.deepEqual(answer, {
    filmSchedule: "on",
    timetable: [
      { at: "06:00", film: "luminous-flowers-spatial-180s.mp4" },
      { at: "19:00", film: "still-water-at-dusk.mp4" },
    ],
    showingNow: "luminous-flowers-spatial-180s.mp4",
  });
  assert.deepEqual(written(fake), [[
    "PUT", "/api/v1/background-videos/schedule",
    { enabled: true, slots: [{ start: "19:00", videoId: WATER }, { start: "06:00", videoId: FLOWERS }] },
  ]]);
});

test("set_film_schedule stops the timetable and keeps it, and starts it again", async (t) => {
  const tools = await startTools(t);
  const { use, fake, mirror, clock } = tools;
  await use("set_film_schedule", { slots: [{ at: "06:00", film: "9c1f44e7" }, { at: "19:00", film: SEASONS }] });
  assert.equal((await fetchState(mirror, clock)).snapshot.background.filmSchedule, "06:00 9c1f44e7, 19:00 546e5d02");
  const stopped = await use("set_film_schedule", { on: false });
  assert.equal(stopped.filmSchedule, "off");
  assert.equal(stopped.timetable.length, 2);
  // The state still shows the timetable, so that it can be added to or turned on again.
  const background = (await fetchState(mirror, clock)).snapshot.background;
  assert.equal(background.filmSchedule, undefined);
  assert.equal(background.filmScheduleSwitchedOff, "06:00 9c1f44e7, 19:00 546e5d02");
  assert.equal((await use("set_film_schedule", { on: true })).filmSchedule, "on");
  assert.deepEqual(fake.state.schedule, { enabled: true, slots: [{ start: "06:00", videoId: FLOWERS }, { start: "19:00", videoId: SEASONS }] });
  // An empty timetable clears it.
  assert.deepEqual(await use("set_film_schedule", { slots: [] }), { filmSchedule: "off", timetable: [] });
});

test("set_film_schedule returns to the timetable from a film chosen by hand", async (t) => {
  const { use, fake } = await startTools(t);
  await use("set_film_schedule", { slots: [{ at: "06:00", film: "flowers" }, { at: "19:00", film: "four seasons" }] });
  const held = await use("set_background", { video: "still-water-at-dusk" });
  assert.equal(held.note, "A film schedule is on, so this one shows until 19:00.");
  assert.deepEqual(await use("set_film_schedule", { resume: true }), {
    filmSchedule: "on",
    timetable: [
      { at: "06:00", film: "luminous-flowers-spatial-180s.mp4" },
      { at: "19:00", film: "four-seasons-spatial-120s.mp4" },
    ],
    showingNow: "luminous-flowers-spatial-180s.mp4",
  });
  assert.equal(fake.state.hold, null);
});

test("set_film_schedule refuses a timetable it cannot make sense of and changes nothing", async (t) => {
  const { use, fake } = await startTools(t);
  assert.match((await use("set_film_schedule", {})).error, /Give slots, on or resume/);
  assert.match((await use("set_film_schedule", { slots: [{ at: "dawn", film: "flowers" }] })).error, /"dawn" is no time of day/);
  assert.match(
    (await use("set_film_schedule", { slots: [{ at: "06:00", film: "the ocean" }] })).error,
    /No film matches "the ocean"\. The films are: 546e5d02 \(four-seasons-spatial-120s\.mp4\)/,
  );
  // "spatial" is in two films' names, so it names neither.
  assert.match((await use("set_film_schedule", { slots: [{ at: "06:00", film: "spatial" }] })).error, /No film matches "spatial"/);
  assert.match(
    (await use("set_film_schedule", { slots: [{ at: "06:00", film: "flowers" }, { at: "6:00", film: "still water" }] })).error,
    /Two films cannot both start at 06:00/,
  );
  assert.match((await use("set_film_schedule", { on: true })).error, /no timetable to turn on/);
  assert.match((await use("set_film_schedule", { resume: true })).error, /No film schedule is on/);
  assert.equal(fake.writes().length, 0);
});

test("set_text_color colours the writing on the glass and can give it back its own", async (t) => {
  const { use, fake } = await startTools(t);
  assert.deepEqual(await use("set_text_color", { text: "#FFD9A0" }), { text: "#ffd9a0", accent: "#c2ced3", changed: true });
  assert.equal(fake.state.layout.textColor, "#ffd9a0");
  assert.deepEqual(await use("set_text_color", { text: "fff", accent: "#9fc5e8" }), { text: "#ffffff", accent: "#9fc5e8", changed: true });
  assert.deepEqual(await use("set_text_color", { text: "default", accent: "default" }), { text: "#f5f2ec", accent: "#c2ced3", changed: true });
  assert.equal((await use("set_text_color", { text: "default" })).changed, false);
  // Dark text cannot be read on a glass that is black where nothing is drawn.
  assert.match((await use("set_text_color", { text: "#101030" })).note, /dark text can hardly be seen/);
  const writes = fake.writes().length;
  assert.match((await use("set_text_color", { text: "warm" })).error, /written as #rrggbb/);
  assert.match((await use("set_text_color", {})).error, /Give text, accent or both/);
  assert.equal(fake.writes().length, writes);
});

test("set_name gives the mirror the name it shows", async (t) => {
  const tools = await startTools(t);
  const { use, fake, mirror, clock } = tools;
  assert.deepEqual(await use("set_name", { name: "  Hallway   mirror " }), { name: "Hallway mirror", was: "Mirror", changed: true });
  assert.deepEqual(written(fake), [["POST", "/api/v1/control/name", { name: "Hallway mirror" }]]);
  assert.equal((await fetchState(mirror, clock)).snapshot.name, "Hallway mirror");
  assert.deepEqual(await use("set_name", { name: "Hallway mirror" }), { name: "Hallway mirror", changed: false, note: "That is the name already." });
  fake.state.nameRefused = true;
  assert.match((await use("set_name", { name: "Bob" })).error, /own system did not take the new name/);
  assert.match((await use("set_name", { name: "x".repeat(65) })).error, /The arguments are not right\. name/);
  assert.equal(fake.writes().length, 2);
});

test("habits tells how they are set and changes the ones asked for", async (t) => {
  const { use, habits, fake } = await startTools(t);
  const usual = {
    greetsWhoWalksUp: "on", morningBriefing: "on", showsRemindersWhenDue: "on", tidiesTheDisplay: "every 60 minutes", quietHours: "22:30 to 06:30",
  };
  assert.deepEqual(await use("habits", {}), { habits: usual });
  const seen = [];
  habits.onChange((now) => seen.push(now));
  const answer = await use("habits", { greet: false, quietHours: "21:00 - 7:00" });
  assert.deepEqual(answer.habits, { ...usual, greetsWhoWalksUp: "off", quietHours: "21:00 to 07:00" });
  assert.equal(answer.changed, true);
  // These habits live in memory only, and the answer says so.
  assert.match(answer.note, /holds until the companion is next restarted/);
  assert.equal(habits.get().greet, false);
  assert.deepEqual(habits.get().quietHours, ["21:00", "07:00"]);
  assert.equal(seen.length, 1);
  // How often to tidy is asked of a companion that is to tidy.
  await use("habits", { tidy: false });
  assert.equal((await use("habits", { tidyEveryMinutes: 30 })).habits.tidiesTheDisplay, "every 30 minutes");
  assert.equal((await use("habits", { quietHours: "none" })).habits.quietHours, "none");
  assert.deepEqual(await use("habits", { quietHours: "none", morningBriefing: true }), {
    habits: { ...usual, greetsWhoWalksUp: "off", tidiesTheDisplay: "every 30 minutes", quietHours: "none" },
    changed: false,
    note: "They were already like that.",
  });
  assert.match((await use("habits", { quietHours: "at night" })).error, /two 24-hour times/);
  assert.match((await use("habits", { quietHours: "22:00-22:00" })).error, /same minute.*Nothing was changed/);
  assert.match((await use("habits", { tidyEveryMinutes: 2 })).error, /The arguments are not right\. tidyEveryMinutes/);
  // None of this is the mirror's business.
  assert.equal(fake.requests().length, 0);
});

test("the settings are for a conversation only: a tending run cannot touch them", async (t) => {
  const { tools } = await startTools(t);
  const settings = ["set_clock", "set_display_rules", "set_weather", "set_film_schedule", "set_text_color", "set_name", "habits"];
  const allowed = (kind) => toolsFor(kind, tools).map((tool) => tool.name);
  for (const name of settings) {
    assert.ok(allowed("conversation").includes(name), name);
    assert.ok(!allowed("tend").includes(name), name);
    assert.ok(!allowed("greeting").includes(name), name);
  }
});
