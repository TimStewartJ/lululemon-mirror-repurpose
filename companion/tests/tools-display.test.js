import assert from "node:assert/strict";
import test from "node:test";
import { newTurn, runTool, toolsFor } from "../src/tools.js";
import { startTools } from "./helpers.js";

test("get_state returns the snapshot and refreshes the turn's state", async (t) => {
  const { use, clock } = await startTools(t);
  const turn = newTurn("conversation");
  const snapshot = await use("get_state", {}, turn);
  assert.equal(snapshot.now.local, "2026-10-03T07:12:00-07:00");
  assert.equal(turn.state.offsetMinutes, -420);
  assert.equal(turn.stateAt, clock.now());
  assert.deepEqual(turn.acted, ["get_state"]);
});

test("look returns the picture, and says so when the display is dark", async (t) => {
  const { use, fake } = await startTools(t);
  const seen = await use("look");
  assert.equal(seen.image.mimeType, "image/jpeg");
  assert.equal(Buffer.from(seen.image.data, "base64")[0], 0xff);
  fake.state.automation.sleeping = true;
  assert.match((await use("look")).error, /The display is dark.*set_power/);
});

test("set_power puts the display to sleep and wakes it", async (t) => {
  const { use, fake } = await startTools(t);
  assert.deepEqual(await use("set_power", { state: "asleep" }), { power: "asleep" });
  assert.equal(fake.state.automation.sleeping, true);
  assert.deepEqual(await use("set_power", { state: "awake" }), { power: "awake" });
  assert.equal(fake.state.automation.sleeping, false);
  // These routes do not read a body on the mirror, so none is sent.
  assert.deepEqual(fake.writes().map((request) => request.body), [null, null]);
  assert.match((await use("set_power", { state: "off" })).error, /The arguments are not right\. state/);
});

test("set_brightness saves the level for every wake and applies it now", async (t) => {
  const { use, fake } = await startTools(t);
  assert.deepEqual(await use("set_brightness", { level: 90 }), { wakeBrightness: 90, was: 180, changed: true });
  assert.equal(fake.state.automation.wakeBrightness, 90);
  assert.equal(fake.state.brightness, 90);
  // The rest of the schedule is sent back as it was read.
  const put = fake.writes().find((request) => request.method === "PUT");
  assert.deepEqual(put.body, {
    enabled: true, wakeTime: "06:30", sleepTime: "23:00", wakeBrightness: 90, ambientEnabled: false,
    ambientMinimum: 20, ambientMaximum: 220, motionEnabled: true, motionTimeoutSeconds: 300, motionSensitivity: 6,
  });
});

test("brighter and dimmer move one step and stop at the ends", async (t) => {
  const { use, fake } = await startTools(t);
  assert.equal((await use("set_brightness", { change: "brighter" })).wakeBrightness, 220);
  assert.equal((await use("set_brightness", { change: "brighter" })).wakeBrightness, 255);
  const top = await use("set_brightness", { change: "brighter" });
  assert.equal(top.changed, false);
  assert.match(top.note, /already 255\. It is at its brightest/);
  fake.state.automation.wakeBrightness = 30;
  assert.equal((await use("set_brightness", { change: "dimmer" })).wakeBrightness, 15);
  assert.match((await use("set_brightness", { change: "dimmer" })).note, /at its dimmest/);
});

test("set_brightness refuses what makes no sense", async (t) => {
  const { use, fake } = await startTools(t);
  assert.match((await use("set_brightness", {})).error, /either level or change/);
  assert.match((await use("set_brightness", { level: 90, change: "dimmer" })).error, /either level or change/);
  assert.match((await use("set_brightness", { level: 5 })).error, /arguments are not right\. level/);
  Object.assign(fake.state.automation, { ambientEnabled: true, ambientLightAvailable: true });
  assert.match((await use("set_brightness", { change: "brighter" })).error, /room's light sets the brightness/);
  assert.equal(fake.writes().length, 0);
});

test("set_brightness keeps a sleep or wake that someone asked for", async (t) => {
  const { use, fake } = await startTools(t);
  await use("set_power", { state: "asleep" });
  await use("set_brightness", { level: 100 });
  assert.equal(fake.state.automation.sleeping, true);
  assert.equal(fake.state.automation.manualOverride, true);
  assert.equal(fake.state.automation.wakeBrightness, 100);
  assert.equal(fake.state.brightness, 180, "a dark display is not lit to set its level");
});

test("set_background finds a film by id, by the start of its id, and by name", async (t) => {
  const { use, fake } = await startTools(t);
  fake.state.layout.background.mode = "solid";
  assert.deepEqual(await use("set_background", { video: "9c1f44e7" }), {
    showing: { id: "9c1f44e7", name: "luminous-flowers-spatial-180s.mp4" },
  });
  assert.equal(fake.state.activeFilm.slice(0, 8), "9c1f44e7");
  assert.equal(fake.state.layout.background.mode, "video", "activating a film turns the background to film");
  assert.equal((await use("set_background", { video: "still-water-at-dusk" })).showing.id, "e03b77d1");
  assert.equal((await use("set_background", { video: "Four-Seasons-Spatial-120s.MP4" })).showing.id, "546e5d02");
  assert.equal((await use("set_background", { video: fake.state.films[1].id })).showing.id, "9c1f44e7");
});

test("set_background next goes round the films in order", async (t) => {
  const { use } = await startTools(t);
  assert.equal((await use("set_background", { video: "next" })).showing.id, "9c1f44e7");
  assert.equal((await use("set_background", { video: "next" })).showing.id, "e03b77d1");
  assert.equal((await use("set_background", { video: "next" })).showing.id, "546e5d02");
});

test("set_background refuses an unknown film and lists the ones there are", async (t) => {
  const { use, fake } = await startTools(t);
  const answer = await use("set_background", { video: "the ocean one" });
  assert.match(answer.error, /No film matches "the ocean one"\. The films are: 546e5d02 \(four-seasons-spatial-120s\.mp4\), 9c1f44e7/);
  assert.equal(fake.writes().length, 0);
  fake.state.films.length = 1;
  assert.match((await use("set_background", { video: "next" })).error, /only one film/);
  fake.state.films.length = 0;
  assert.match((await use("set_background", { video: "next" })).error, /no film on the mirror/);
  assert.match((await use("set_background", { mode: "film" })).error, /no film on the mirror/);
  assert.match((await use("set_background", {})).error, /Give video or mode/);
});

test("set_background changes the mode and nothing else in the layout", async (t) => {
  const { use, fake } = await startTools(t);
  const before = structuredClone(fake.state.layout);
  assert.deepEqual(await use("set_background", { mode: "black" }), { background: "black" });
  assert.deepEqual(fake.state.layout, { ...before, background: { ...before.background, mode: "solid", primary: "#000000" } });
  assert.deepEqual(await use("set_background", { mode: "photo" }), { background: "photo" });
  assert.equal(fake.state.layout.background.mode, "photo");
  assert.equal(fake.state.layout.background.photo, "garden.jpg");
  assert.deepEqual(await use("set_background", { mode: "film" }), { background: "film" });
  assert.deepEqual(fake.state.layout.widgets, before.widgets);
  fake.state.photos = [];
  fake.state.layout.background.photo = "";
  assert.match((await use("set_background", { mode: "photo" })).error, /no photo on the mirror/);
});

test("an unreachable mirror is something the model can explain", async (t) => {
  const { use, fake } = await startTools(t);
  fake.state.dropNext = 2;
  const answer = await use("set_power", { state: "asleep" });
  assert.equal(answer.error, "I can't reach the display right now. Nothing was changed. Tell the person so.");
});

test("a tool call after the turn is over does nothing", async (t) => {
  const { use, fake } = await startTools(t);
  const turn = newTurn("conversation");
  turn.closed = true;
  assert.match((await use("set_power", { state: "asleep" }, turn)).error, /This request is over/);
  assert.equal(fake.writes().length, 0);
  assert.deepEqual(turn.acted, []);
});

test("a greeting may only speak and a tending run may only tidy, once", async (t) => {
  const { tools, fake } = await startTools(t);
  assert.deepEqual(toolsFor("greeting", tools).map((tool) => tool.name), ["say"]);
  assert.deepEqual(toolsFor("tend", tools).map((tool) => tool.name), ["get_state", "set_background", "arrange_widgets", "board_remove"]);
  // In a conversation the answer is the line on the glass; a say tool beside it showed the line twice.
  assert.equal(toolsFor("conversation", tools).length, 15);
  assert.ok(!toolsFor("conversation", tools).some((tool) => tool.name === "say"));

  const turn = newTurn("tend");
  const background = tools.find((tool) => tool.name === "set_background");
  assert.equal((await runTool(background, { video: "next" }, turn)).showing.id, "9c1f44e7");
  assert.match((await runTool(background, { video: "next" }, turn)).error, /may change one thing/);
  assert.equal(fake.state.activeFilm.slice(0, 8), "9c1f44e7");
  // Looking is not changing, and a change that failed does not count.
  const reading = newTurn("tend");
  await runTool(tools.find((tool) => tool.name === "get_state"), {}, reading);
  await runTool(background, { video: "nothing like it" }, reading);
  assert.equal((await runTool(background, { video: "next" }, reading)).showing.id, "e03b77d1");
});
