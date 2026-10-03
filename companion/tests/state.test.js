import assert from "node:assert/strict";
import test from "node:test";
import { MirrorUnreachable } from "../src/mirror.js";
import { buildState, fetchState } from "../src/state.js";
import { startTools } from "./helpers.js";

test("the snapshot tells the time in the mirror's zone, whatever the server's", async (t) => {
  const { mirror, clock } = await startTools(t);
  const { snapshot, offsetMinutes, now } = await fetchState(mirror, clock);
  assert.equal(offsetMinutes, -420);
  assert.equal(now, clock.now());
  assert.deepEqual(snapshot.now, {
    local: "2026-10-03T07:12:00-07:00",
    weekday: "Saturday",
    zone: "America/Los_Angeles",
    utcOffset: "-07:00",
    clock: "12-hour",
  });
});

test("the snapshot has the display, the widgets, the background, the board, the weather and voice", async (t) => {
  const { mirror, clock, fake, use } = await startTools(t);
  await use("board_add", { kind: "todo", title: "Buy milk" });
  const { snapshot, boardVersion, sleeping, items } = await fetchState(mirror, clock);
  assert.deepEqual(snapshot.display, {
    power: "awake",
    wakeBrightness: 180,
    awakeHours: "06:30 to 23:00",
    sleepsWhenNobodyIsThere: "after 300 seconds",
  });
  assert.match(snapshot.layoutUnits, /thousandths of the screen.*0,0 is the top left/);
  assert.equal(snapshot.widgets.length, 16);
  assert.deepEqual(snapshot.widgets[0], { id: "clock", visible: true, x: 50, y: 52, w: 560, h: 150, align: "start" });
  assert.equal(snapshot.background.mode, "video");
  assert.deepEqual(snapshot.background.films, [
    { id: "546e5d02", name: "four-seasons-spatial-120s.mp4", showing: true },
    { id: "9c1f44e7", name: "luminous-flowers-spatial-180s.mp4" },
    { id: "e03b77d1", name: "still-water-at-dusk.mp4" },
  ]);
  assert.equal(snapshot.board.onTheGlass, true);
  assert.equal(snapshot.board.items.length, 1);
  assert.deepEqual(Object.keys(snapshot.board.items[0]), ["id", "kind", "title", "state"]);
  assert.equal(snapshot.weather.place, "Seattle");
  assert.deepEqual(snapshot.weather.now, { temp: 12, feelsLike: 11, condition: "Overcast" });
  assert.deepEqual(snapshot.weather.today, { high: 16, low: 9, rainChance: 70, condition: "Light rain", sunset: "18:45" });
  assert.equal(snapshot.weather.tomorrow.condition, "Mostly clear");
  assert.equal(snapshot.weather.nextHours.length, 6);
  assert.equal(snapshot.weather.nextHours[0].at, "08:00");
  assert.equal(snapshot.voice, "listening");
  assert.equal(boardVersion, fake.state.board.version);
  assert.equal(sleeping, false);
  assert.equal(items[0].title, "Buy milk");
});

test("the snapshot is small enough to read in one go", async (t) => {
  const { mirror, clock, use } = await startTools(t);
  for (const title of ["Buy milk", "Call the plumber", "Water the plants"]) await use("board_add", { kind: "todo", title });
  await use("board_add", { kind: "reminder", title: "Take out the trash", due: "2026-10-04T07:00:00-07:00" });
  const { snapshot } = await fetchState(mirror, clock);
  const size = Buffer.byteLength(JSON.stringify(snapshot));
  assert.ok(size < 3072, `the snapshot is ${size} bytes`);
});

test("a dark display says why, and a due time is on the mirror's wall clock", async (t) => {
  const { mirror, clock, fake, use } = await startTools(t);
  await use("board_add", { kind: "reminder", title: "Trash", due: "2026-10-04T14:00:00Z" });
  Object.assign(fake.state.automation, { sleeping: true, sleepReason: "schedule" });
  const { snapshot, sleeping } = await fetchState(mirror, clock);
  assert.equal(sleeping, true);
  assert.equal(snapshot.display.power, "asleep");
  assert.equal(snapshot.display.asleepBecause, "schedule");
  assert.equal(snapshot.board.items[0].due, "2026-10-04T07:00:00-07:00");
});

test("a coming change of the offset is announced", () => {
  const now = Date.UTC(2026, 9, 25, 14, 0, 0);
  const status = {
    timeZone: "Europe/Berlin",
    utcOffsetMinutes: 120,
    nextUtcOffsetChange: { at: Date.UTC(2026, 9, 26, 1, 0, 0), utcOffsetMinutes: 60 },
    automation: {},
  };
  const { snapshot } = buildState({ status, layout: null, board: null, films: null, now });
  assert.equal(snapshot.now.utcOffset, "+02:00");
  assert.equal(snapshot.now.offsetChange, "From 2026-10-26T02:00:00+01:00 the offset is +01:00.");
  assert.equal(snapshot.widgets, "The layout could not be read.");
  assert.deepEqual(snapshot.board, { note: "The board could not be read." });
  assert.deepEqual(snapshot.weather, { note: "No weather is set up on the mirror." });
});

test("a part that cannot be read is left out, but no status means no state", async (t) => {
  const { mirror, clock, fake } = await startTools(t);
  fake.state.failing.set("/api/v1/background-videos", 500);
  const partial = await fetchState(mirror, clock);
  assert.deepEqual(partial.snapshot.background, { mode: "video" });
  fake.state.failing.set("/api/v1/status", 500);
  await assert.rejects(fetchState(mirror, clock), MirrorUnreachable);
});
