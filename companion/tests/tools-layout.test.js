import assert from "node:assert/strict";
import test from "node:test";
import { newTurn } from "../src/tools.js";
import { startTools } from "./helpers.js";

test("arrange_widgets moves one widget and leaves the rest of the layout exactly as it was", async (t) => {
  const { use, fake } = await startTools(t);
  const before = structuredClone(fake.state.layout);
  const answer = await use("arrange_widgets", { changes: [{ id: "clock", y: 820 }] });
  assert.deepEqual(answer.changed, ["clock"]);
  assert.equal(answer.widgets.length, 16);
  assert.deepEqual(answer.widgets[0], { id: "clock", visible: true, x: 50, y: 820, w: 560, h: 150, align: "start" });

  // What was sent is what was read, with one number changed: same fields, same order.
  const put = fake.requests().find((request) => request.method === "PUT");
  const expected = structuredClone(before);
  expected.widgets[0].y = 820;
  assert.equal(JSON.stringify(put.body), JSON.stringify(expected));
  assert.deepEqual(fake.state.layout, expected);
});

test("arrange_widgets asks the mirror to validate before it writes", async (t) => {
  const { use, fake } = await startTools(t);
  await use("arrange_widgets", { changes: [{ id: "weather", visible: false }] });
  const order = fake.requests().map((request) => `${request.method} ${request.path}`);
  assert.deepEqual(order, [
    "GET /api/v1/dashboard/layout",
    "POST /api/v1/dashboard/layout/validate",
    "PUT /api/v1/dashboard/layout",
  ]);
  assert.equal(fake.widget("weather").visible, false);
});

test("arrange_widgets applies several changes together", async (t) => {
  const { use, fake } = await startTools(t);
  const answer = await use("arrange_widgets", {
    changes: [
      { id: "clock", w: 700, h: 190 },
      { id: "date", y: 250 },
      { id: "forecast", visible: false },
      { id: "weather", visible: false },
      { id: "board", visible: true, size: "large", text: "To do" },
    ],
  });
  assert.deepEqual(answer.changed, ["clock", "date", "forecast", "weather", "board"]);
  assert.equal(fake.widget("clock").w, 700);
  assert.equal(fake.widget("date").y, 250);
  assert.equal(fake.widget("board").text, "To do");
  assert.deepEqual(answer.notes, ['"clock" is now above "date".', '"date" is now below "clock" and above "board".', '"board" is now below "date".']);
});

test("arrange_widgets refuses an unknown id by name and writes nothing", async (t) => {
  const { use, fake } = await startTools(t);
  const answer = await use("arrange_widgets", { changes: [{ id: "weather", visible: false }, { id: "klock", y: 800 }] });
  assert.match(answer.error, /^Nothing was changed\. No widget has the id "klock"\. The ids are: clock, date/);
  assert.equal(fake.writes().length, 0);
  assert.equal(fake.widget("weather").visible, true);
});

test("arrange_widgets keeps a widget on the screen and says where it put it", async (t) => {
  const { use, fake } = await startTools(t);
  const answer = await use("arrange_widgets", { changes: [{ id: "clock", y: 950 }] });
  assert.equal(fake.widget("clock").y, 850);
  assert.equal(answer.notes[0], '"clock" did not fit there and is at y 850, the lowest for h 150.');
});

test("arrange_widgets refuses a number that is off the screen and writes nothing", async (t) => {
  const { use, fake } = await startTools(t);
  const answer = await use("arrange_widgets", { changes: [{ id: "clock", y: -50 }] });
  assert.match(answer.error, /^Nothing was changed\. "clock": y must be a whole number from 0 to 1000, not -50\./);
  assert.equal(fake.writes().length, 0);
});

test("arrange_widgets takes width and height for w and h, and refuses fractions", async (t) => {
  const { use, fake } = await startTools(t);
  await use("arrange_widgets", { changes: [{ id: "clock", width: 500, height: 140 }] });
  assert.deepEqual([fake.widget("clock").w, fake.widget("clock").h], [500, 140]);
  assert.match((await use("arrange_widgets", { changes: [{ id: "clock", x: 10.5 }] })).error, /arguments are not right/);
  assert.match((await use("arrange_widgets", { changes: [] })).error, /arguments are not right/);
});

test("arrange_widgets says so when there was nothing to change", async (t) => {
  const { use, fake } = await startTools(t);
  const answer = await use("arrange_widgets", { changes: [{ id: "clock", visible: true }] });
  assert.deepEqual(answer.changed, []);
  assert.match(answer.note, /already like that/);
  assert.equal(fake.writes().length, 0);
});

test("arrange_widgets refuses to lay one widget over another unless that is wanted", async (t) => {
  const { use, fake } = await startTools(t);
  const refused = await use("arrange_widgets", { changes: [{ id: "clock", h: 200 }] });
  assert.match(refused.error, /^Nothing was changed\. "clock" \(x 50 to 610, y 52 to 252\) overlaps "date" \(x 54 to 594, y 208 to 254\)\. Move or resize those widgets in the same call/);
  assert.equal(fake.writes().length, 0);
  const wanted = await use("arrange_widgets", { changes: [{ id: "clock", h: 200 }], overlapOk: true });
  assert.equal(fake.widget("clock").h, 200);
  assert.equal(wanted.notes[0], '"clock" (x 50 to 610, y 52 to 252) overlaps "date" (x 54 to 594, y 208 to 254) now.');
});

test("arrange_widgets puts the mirror's bare refusal into words", async (t) => {
  const { use, fake } = await startTools(t);
  // A layout the mirror will not take although the companion's own checks pass:
  // the stored layout carries something the mirror's rules reject.
  fake.state.layout.textColor = "white";
  const answer = await use("arrange_widgets", { changes: [{ id: "clock", y: 800 }] });
  assert.match(answer.error, /The mirror refused: the mirror does not accept this layout and does not say why\. Nothing was changed/);
  assert.equal(fake.writes().length, 0);
});

test("arrange_widgets updates the state the turn carries", async (t) => {
  const { use } = await startTools(t);
  const turn = newTurn("conversation");
  await use("get_state", {}, turn);
  await use("arrange_widgets", { changes: [{ id: "clock", y: 800 }] }, turn);
  assert.equal(turn.state.snapshot.widgets[0].y, 800);
  assert.equal(turn.changes, 1);
});
