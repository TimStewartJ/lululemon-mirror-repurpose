import assert from "node:assert/strict";
import test from "node:test";
import { fetchState } from "../src/state.js";
import { newTurn, toolsFor } from "../src/tools.js";
import { describeMoments } from "../src/tools/moments.js";
import { startTools } from "./helpers.js";

const posted = (fake) => fake.writes().filter((request) => request.path === "/api/v1/moments").map((request) => request.body);

/** A turn that knows the mirror's state, as every turn of a conversation does. */
async function turnWithState({ mirror, clock }) {
  return newTurn("conversation", await fetchState(mirror, clock), clock.now());
}

test("show_moment puts words on the glass for about a minute, and leaves the place to the glass", async (t) => {
  const tools = await startTools(t);
  const { use, fake, clock } = tools;
  const answer = await use("show_moment", { kind: "text", text: " Good luck today. ", title: "For Sam" }, await turnWithState(tools));
  assert.deepEqual(answer, { showing: "m1", kind: "text", leavesIn: "45 s" });
  // Neither a size nor a place was asked for, so none is sent: the glass knows where something is drawn.
  assert.deepEqual(posted(fake), [{ kind: "text", title: "For Sam", seconds: 45, text: "Good luck today." }]);
  assert.equal(fake.requests().length, 7, "the six of the state, and one request to show it");
  // The next turn's state says that it is there, and it is gone when its time is up.
  assert.deepEqual((await fetchState(tools.mirror, clock)).snapshot.moments, [{ id: "m1", kind: "text", shows: "For Sam", leavesIn: "45 s" }]);
  await clock.advance(45_000);
  assert.equal((await fetchState(tools.mirror, clock)).snapshot.moments, undefined);
});

test("a countdown runs to its instant and stays a little longer", async (t) => {
  const tools = await startTools(t);
  const { use, fake, clock } = tools;
  const answer = await use("show_moment", { kind: "countdown", countdownSeconds: 600, title: "Tea", id: "tea", seconds: 60 }, await turnWithState(tools));
  assert.deepEqual(answer, { showing: "tea", kind: "countdown", leavesIn: "620 s", runsOutIn: "600 s" });
  // It stays until it has run out and a little longer: seconds that would end it sooner are not passed on.
  assert.deepEqual(posted(fake)[0], { kind: "countdown", id: "tea", title: "Tea", endsAt: clock.now() + 600_000 });
  // To a time of day, as the model writes one.
  const until = await use("show_moment", { kind: "countdown", countdownTo: "2026-10-03T08:00:00-07:00", title: "Leave" }, await turnWithState(tools));
  assert.equal(until.runsOutIn, "2880 s");
  assert.match((await use("show_moment", { kind: "countdown" })).error, /needs countdownSeconds or countdownTo/);
  assert.match((await use("show_moment", { kind: "countdown", countdownSeconds: 60, countdownTo: "2026-10-03T08:00:00-07:00" })).error, /one of them/);
  assert.match((await use("show_moment", { kind: "countdown", countdownTo: "eight" })).error, /ISO time with its UTC offset/);
  assert.match((await use("show_moment", { kind: "countdown", countdownTo: "2026-10-03T07:00:00-07:00" })).error, /has passed already/);
  assert.match((await use("show_moment", { kind: "countdown", countdownTo: "2026-10-04T07:00:00-07:00" })).error, /six hours at most.*reminder/);
  assert.equal(posted(fake).length, 2);
});

test("the same id replaces a moment, and a place is passed on when one is asked for", async (t) => {
  const { use, fake } = await startTools(t);
  await use("show_moment", { kind: "text", id: "score", text: "2 : 1", size: "large", height: "top" });
  const second = await use("show_moment", { kind: "text", id: "score", text: "3 : 1", size: "large", height: "top" });
  assert.deepEqual(second, { showing: "score", kind: "text", leavesIn: "45 s", replaced: true });
  assert.deepEqual(posted(fake)[1], { kind: "text", id: "score", seconds: 45, size: "large", height: "top", text: "3 : 1" });
  assert.equal(fake.state.moments.length, 1);
  await use("show_moment", { kind: "text", text: "Over here", side: "right", seconds: 20 });
  assert.deepEqual(posted(fake)[2], { kind: "text", seconds: 20, side: "right", text: "Over here" });
});

test("a list, a chart and a drawing go to the mirror as they were described", async (t) => {
  const { use, fake } = await startTools(t);
  await use("show_moment", { kind: "list", title: "Pour-over", rows: [{ label: "1", text: "Rinse the filter" }, { text: "Bloom" }] });
  await use("show_moment", { kind: "chart", chart: "line", values: [{ label: "9", value: 75 }, { label: "10", value: 72 }] });
  await use("show_moment", { kind: "chart", values: [{ label: "Mon", value: 4200 }, { label: "Tue", value: 8100 }] });
  await use("show_moment", { kind: "drawing", motion: "pulse", color: "FF8FA3", shapes: [{ shape: "circle", x: 50, y: 50, r: 30, colour: "ignored" }] });
  const [list, line, bars, drawing] = posted(fake);
  assert.deepEqual(list, { kind: "list", title: "Pour-over", seconds: 90, rows: [{ label: "1", text: "Rinse the filter" }, { label: "", text: "Bloom" }] });
  assert.deepEqual([line.chart, line.seconds, bars.chart], ["line", 60, "bars"]);
  assert.deepEqual(drawing, { kind: "drawing", motion: "pulse", seconds: 45, color: "#ff8fa3", shapes: [{ shape: "circle", x: 50, y: 50, r: 30 }] });
  assert.equal(fake.state.moments.length, 4);
});

test("what cannot be shown is said in words the model can act on", async (t) => {
  const { use, fake } = await startTools(t);
  assert.match((await use("show_moment", { kind: "text" })).error, /A text needs text/);
  assert.match((await use("show_moment", { kind: "list" })).error, /A list needs rows/);
  assert.match((await use("show_moment", { kind: "chart", values: [] })).error, /A chart needs values/);
  assert.match((await use("show_moment", { kind: "drawing" })).error, /A drawing needs shapes/);
  assert.match((await use("show_moment", { kind: "text", text: "Hi", color: "warm" })).error, /written as #rrggbb/);
  assert.match((await use("show_moment", { kind: "video" })).error, /The arguments are not right\. kind/);
  assert.match((await use("show_moment", { kind: "text", text: "Hi", id: "Not An Id" })).error, /The arguments are not right\. id/);
  assert.equal(fake.requests().length, 0, "none of these reached the mirror");
  // What the mirror itself refuses comes back with its reason.
  assert.match(
    (await use("show_moment", { kind: "chart", values: [{ label: "a", value: 1 }] })).error,
    /The mirror did not take it: A chart needs at least two values\. Nothing was shown\./,
  );
  assert.match((await use("show_moment", { kind: "drawing", shapes: [{ shape: "circle", x: 50, y: 50 }] })).error, /r must be a number/);
  assert.equal(fake.state.moments.length, 0);
});

test("a dark display is told, and a Mirror Home without moments says so", async (t) => {
  const { use, fake } = await startTools(t);
  fake.state.automation.sleeping = true;
  assert.equal((await use("show_moment", { kind: "text", text: "Hello" })).note, "The display is dark, so nobody sees it yet.");
  fake.state.moments = null;
  assert.match((await use("show_moment", { kind: "text", text: "Hello" })).error, /cannot show moments yet.*Answer in words/);
  assert.deepEqual(await use("end_moment", { id: "all" }), { removed: 0, note: "No moment was on the glass." });
});

test("end_moment takes one moment down, or all of them", async (t) => {
  const { use, fake } = await startTools(t);
  await use("show_moment", { kind: "countdown", countdownSeconds: 300, id: "tea" });
  await use("show_moment", { kind: "text", text: "Hello", id: "hello" });
  await use("show_moment", { kind: "text", text: "Again" });
  assert.equal((await use("end_moment", { id: "nope" })).error, 'No moment is called "nope". Showing now: tea, hello, m3.');
  assert.deepEqual(await use("end_moment", { id: "tea" }), { removed: 1 });
  assert.deepEqual(fake.state.moments.map((moment) => moment.id), ["hello", "m3"]);
  assert.deepEqual(await use("end_moment", { id: "All" }), { removed: 2 });
  assert.deepEqual(await use("end_moment", { id: "all" }), { removed: 0, note: "No moment was on the glass." });
  assert.equal((await use("end_moment", { id: "tea" })).error, "No moment is on the glass.");
});

test("the state tells of each moment what the model needs to speak of it", () => {
  const now = 1_000_000;
  const document = {
    moments: [
      { id: "tea", kind: "countdown", title: "Tea", endsAt: now + 90_000, until: now + 110_000 },
      { id: "m2", kind: "text", text: "A sentence that is a good deal longer than forty characters", until: now + 30_400 },
      { id: "gone", kind: "text", text: "Over", until: now },
      { id: "art", kind: "drawing", until: now + 5_000 },
    ],
  };
  assert.deepEqual(describeMoments(document, now), [
    { id: "tea", kind: "countdown", shows: "Tea", runsOutIn: "90 s", leavesIn: "110 s" },
    { id: "m2", kind: "text", shows: "A sentence that is a good deal longer...", leavesIn: "30 s" },
    { id: "art", kind: "drawing", leavesIn: "5 s" },
  ]);
  assert.equal(describeMoments(null, now), null);
});

test("moments are for a conversation: a tending run and a greeting have neither tool", async (t) => {
  const { tools } = await startTools(t);
  for (const name of ["show_moment", "end_moment"]) {
    assert.ok(toolsFor("conversation", tools).some((tool) => tool.name === name));
    assert.ok(!toolsFor("tend", tools).some((tool) => tool.name === name));
    assert.ok(!toolsFor("greeting", tools).some((tool) => tool.name === name));
  }
});
