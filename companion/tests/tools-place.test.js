import assert from "node:assert/strict";
import test from "node:test";
import { toolsFor } from "../src/tools.js";
import { startTools } from "./helpers.js";

const sent = (fake) => fake.writes().map((request) => [request.method, request.path, request.body]);

test("set_answer_place moves the answers to the height and side asked for", async (t) => {
  const { use, fake } = await startTools(t);
  assert.deepEqual(await use("set_answer_place", { height: "top", side: "left" }), {
    answersAt: "top left", was: "bottom center", changed: true,
  });
  assert.deepEqual(fake.state.place, { height: "top", side: "left" });
  assert.deepEqual(sent(fake), [["PUT", "/api/v1/assistant", { place: { height: "top", side: "left" } }]]);
});

test("what is left out stays as it is", async (t) => {
  const { use, fake } = await startTools(t);
  assert.equal((await use("set_answer_place", { height: "upper" })).answersAt, "upper center");
  assert.equal((await use("set_answer_place", { side: "right" })).answersAt, "upper right");
  assert.deepEqual(fake.state.place, { height: "upper", side: "right" });
  // The mirror is told the whole place each time, so that nothing rests on what it had.
  assert.deepEqual(sent(fake).at(-1), ["PUT", "/api/v1/assistant", { place: { height: "upper", side: "right" } }]);
});

test("up and down go one step, and stop at the ends", async (t) => {
  const { use, fake } = await startTools(t);
  const seen = [];
  for (let step = 0; step < 4; step += 1) seen.push((await use("set_answer_place", { height: "up" })).answersAt);
  assert.deepEqual(seen, ["lower center", "middle center", "upper center", "top center"]);
  const writes = fake.writes().length;
  assert.deepEqual(await use("set_answer_place", { height: "up" }), {
    answersAt: "top center", changed: false, note: "Your answers are as high as they go.",
  });
  assert.equal((await use("set_answer_place", { height: "down" })).answersAt, "upper center");
  fake.state.place = { height: "bottom", side: "left" };
  assert.match((await use("set_answer_place", { height: "down" })).note, /as low as they go/);
  assert.equal(fake.writes().length, writes + 1);
  // At an end, a side that was asked for along with the step is still taken.
  assert.deepEqual(await use("set_answer_place", { height: "down", side: "right" }), {
    answersAt: "bottom right", was: "bottom left", changed: true,
  });
});

test("asking for the place they have changes nothing and says so", async (t) => {
  const { use, fake } = await startTools(t);
  assert.deepEqual(await use("set_answer_place", { height: "bottom", side: "center" }), {
    answersAt: "bottom center", changed: false, note: "Your answers were already there.",
  });
  assert.equal(fake.writes().length, 0);
});

test("what is no place is refused before the mirror is asked", async (t) => {
  const { use, fake } = await startTools(t);
  assert.match((await use("set_answer_place", {})).error, /Give height, side or both/);
  assert.match((await use("set_answer_place", { height: "ceiling" })).error, /The arguments are not right\. height/);
  assert.match((await use("set_answer_place", { side: "middle" })).error, /The arguments are not right\. side/);
  assert.equal(fake.requests().length, 0);
});

test("a mirror with fewer places is asked only for the ones it has", async (t) => {
  const { use, fake } = await startTools(t);
  fake.state.places = { heights: ["top", "bottom"], sides: ["center"] };
  assert.equal(
    (await use("set_answer_place", { height: "middle" })).error,
    "This mirror has no such place. Its heights are top, bottom; its sides are center.",
  );
  // One step up from the bottom is its top.
  assert.equal((await use("set_answer_place", { height: "up" })).answersAt, "top center");
  assert.equal(fake.writes().length, 1);
});

test("a Mirror Home whose answers cannot be moved says so", async (t) => {
  const { use, fake } = await startTools(t);
  fake.state.place = null;
  assert.match((await use("set_answer_place", { height: "top" })).error, /cannot move its answers yet/);
  assert.equal(fake.writes().length, 0);
});

test("only a conversation may move the answers", async (t) => {
  const { tools } = await startTools(t);
  const allowed = (kind) => toolsFor(kind, tools).some((tool) => tool.name === "set_answer_place");
  assert.deepEqual([allowed("conversation"), allowed("greeting"), allowed("tend")], [true, false, false]);
});
