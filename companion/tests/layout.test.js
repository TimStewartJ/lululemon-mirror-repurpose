import assert from "node:assert/strict";
import test from "node:test";
import { applyChanges, describeWidget } from "../src/layout.js";
import { defaultLayout, normalizeLayout } from "./fakes/mirror-layout.js";

const widget = (layout, id) => layout.widgets.find((candidate) => candidate.id === id);

test("only the named fields of the named widget change", () => {
  const before = defaultLayout();
  const { layout, changed, problems } = applyChanges(before, [{ id: "clock", y: 800 }]);
  assert.deepEqual(problems, []);
  assert.deepEqual(changed, ["clock"]);
  assert.equal(widget(layout, "clock").y, 800);
  // Everything else is as it was, in the same order, down to the last field.
  const restored = structuredClone(layout);
  widget(restored, "clock").y = 52;
  assert.equal(JSON.stringify(restored), JSON.stringify(before));
  assert.equal(widget(before, "clock").y, 52, "the layout handed in is not altered");
});

test("width and height are other names for w and h", () => {
  const { layout } = applyChanges(defaultLayout(), [{ id: "clock", width: 700, height: 200 }], { overlapOk: true });
  assert.equal(widget(layout, "clock").w, 700);
  assert.equal(widget(layout, "clock").h, 200);
  assert.equal(widget(layout, "clock").width, undefined);
});

test("an unknown id is refused by name, with the ids there are", () => {
  const { problems, changed } = applyChanges(defaultLayout(), [{ id: "clok", y: 800 }]);
  assert.equal(changed.length, 0);
  assert.match(problems[0], /No widget has the id "clok"\. The ids are: clock, date, name, weather/);
});

test("a widget that would reach past an edge is put at the edge, and that is said", () => {
  const right = applyChanges(defaultLayout(), [{ id: "weather", x: 900 }]);
  assert.deepEqual(right.problems, []);
  assert.equal(widget(right.layout, "weather").x, 650);
  assert.equal(right.notes[0], '"weather" did not fit there and is at x 650, the furthest right for w 350.');
  const bottom = applyChanges(defaultLayout(), [{ id: "clock", y: 1000 }]);
  assert.equal(widget(bottom.layout, "clock").y, 850);
  assert.equal(bottom.notes[0], '"clock" did not fit there and is at y 850, the lowest for h 150.');
  // A widget made wider than the room to its right moves left to fit.
  const wider = applyChanges(defaultLayout(), [{ id: "weather", w: 500 }], { overlapOk: true });
  assert.deepEqual([widget(wider.layout, "weather").x, widget(wider.layout, "weather").w], [500, 500]);
  normalizeLayout(bottom.layout);
});

test("a number outside the screen is refused with the range", () => {
  const small = applyChanges(defaultLayout(), [{ id: "clock", w: 10 }]);
  assert.match(small.problems[0], /w must be a whole number from 24 to 1000, not 10/);
  const fraction = applyChanges(defaultLayout(), [{ id: "clock", x: 10.5 }]);
  assert.match(fraction.problems[0], /x must be a whole number/);
  assert.match(applyChanges(defaultLayout(), [{ id: "clock", y: -20 }]).problems[0], /y must be a whole number from 0 to 1000, not -20/);
  assert.match(applyChanges(defaultLayout(), [{ id: "clock", h: 1200 }]).problems[0], /h must be a whole number from 24 to 1000/);
});

test("one bad change stops them all", () => {
  const before = defaultLayout();
  const { layout, problems } = applyChanges(before, [
    { id: "weather", visible: false },
    { id: "clock", y: 1990 },
  ]);
  assert.equal(problems.length, 1);
  assert.equal(layout, before);
});

test("settings that a widget does not have are explained", () => {
  assert.match(applyChanges(defaultLayout(), [{ id: "clock", size: "large" }]).problems[0], /has no size setting.*w and h/);
  assert.match(applyChanges(defaultLayout(), [{ id: "board", size: "huge" }]).problems[0], /size must be one of small, medium, large/);
  assert.match(applyChanges(defaultLayout(), [{ id: "clock", text: "Hello" }]).problems[0], /shows no text of its own/);
  assert.match(applyChanges(defaultLayout(), [{ id: "board", text: "Two\nlines" }]).problems[0], /heading is one line/);
  assert.match(applyChanges(defaultLayout(), [{ id: "clock", opacity: 5 }]).problems[0], /opacity must be a whole number from 10 to 100/);
});

test("a board's heading and size and a note's words can be set", () => {
  const { layout, problems } = applyChanges(
    defaultLayout(),
    [
      { id: "board", text: "To do", size: "large", visible: true },
      { id: "note", text: "Keys are by the door", visible: true },
    ],
    { overlapOk: true },
  );
  assert.deepEqual(problems, []);
  assert.deepEqual([widget(layout, "board").text, widget(layout, "board").size, widget(layout, "board").show], ["To do", "large", "all"]);
  assert.equal(widget(layout, "note").source, "text");
  normalizeLayout(layout);
});

test("a locked widget can be shown or hidden but not moved", () => {
  const locked = defaultLayout();
  widget(locked, "clock").locked = true;
  assert.match(applyChanges(locked, [{ id: "clock", y: 700 }]).problems[0], /"clock" is locked in the layout editor/);
  const hidden = applyChanges(locked, [{ id: "clock", visible: false, y: 52 }]);
  assert.deepEqual(hidden.problems, []);
  assert.equal(widget(hidden.layout, "clock").visible, false);
});

test("a change that changes nothing is not reported as one", () => {
  assert.deepEqual(applyChanges(defaultLayout(), [{ id: "clock", visible: true, x: 50 }]).changed, []);
});

test("a change that would lay one widget over another is refused with both boxes", () => {
  const before = defaultLayout();
  const { layout, problems } = applyChanges(before, [{ id: "clock", y: 150 }]);
  assert.equal(layout, before);
  assert.deepEqual(problems, [
    '"clock" (x 50 to 610, y 150 to 300) overlaps "date" (x 54 to 594, y 208 to 254). ' +
      "Move or resize those widgets in the same call so that nothing overlaps, choose another place or size, " +
      "or pass overlapOk: true if lying over one another is what the person wants.",
  ]);
  const several = applyChanges(defaultLayout(), [{ id: "clock", x: 440, y: 100 }]);
  assert.match(several.problems[0], /^"clock" \(x 440 to 1000, y 100 to 250\) overlaps "date" \(.*\), "weather" \(.*\) and "forecast" \(x 560 to 950, y 185 to 280\)\./);
});

test("moving the other widget out of the way in the same call is accepted", () => {
  const { problems, changed, notes } = applyChanges(defaultLayout(), [
    { id: "clock", h: 200 },
    { id: "date", y: 262 },
    { id: "forecast", y: 262 },
  ]);
  assert.deepEqual(problems, []);
  assert.deepEqual(changed, ["clock", "date", "forecast"]);
  assert.deepEqual(notes, ['"clock" is now above "date".', '"date" is now below "clock".', '"forecast" is now below "clock".']);
});

test("where a moved widget ended up is said in words", () => {
  assert.deepEqual(applyChanges(defaultLayout(), [{ id: "clock", y: 850 }]).notes, ['"clock" is now below "forecast".']);
  assert.deepEqual(applyChanges(defaultLayout(), [{ id: "weather", x: 100, y: 400 }]).notes, ['"weather" is now below "forecast".']);
  assert.deepEqual(applyChanges(defaultLayout(), [{ id: "weather", visible: false }]).notes, []);
  const between = applyChanges(defaultLayout(), [
    { id: "clock", y: 800 },
    { id: "weather", x: 50, y: 500 },
  ]);
  assert.equal(between.notes[1], '"weather" is now below "forecast" and above "clock".');
});

test("an overlap that is wanted is allowed and noted", () => {
  const { problems, notes, layout } = applyChanges(defaultLayout(), [{ id: "clock", y: 150 }], { overlapOk: true });
  assert.deepEqual(problems, []);
  assert.equal(widget(layout, "clock").y, 150);
  assert.equal(notes[0], '"clock" (x 50 to 610, y 150 to 300) overlaps "date" (x 54 to 594, y 208 to 254) now.');
});

test("what does not count as an overlap", () => {
  assert.deepEqual(applyChanges(defaultLayout(), [{ id: "clock", y: 700 }]).problems, []);
  // The default clock and weather boxes touch by a sliver; that is not worth a refusal.
  assert.deepEqual(applyChanges(defaultLayout(), [{ id: "clock", y: 60 }]).problems, []);
  // Hidden widgets are not in the way: the bottom of the default layout is full of them.
  assert.deepEqual(applyChanges(defaultLayout(), [{ id: "clock", y: 850 }]).problems, []);
  // Two widgets that already overlapped may be changed without fixing that.
  const stacked = defaultLayout();
  widget(stacked, "date").y = 150;
  assert.deepEqual(applyChanges(stacked, [{ id: "clock", x: 60 }]).problems, []);
  // A photo may be a frame behind everything else.
  const framed = applyChanges(defaultLayout(), [{ id: "photo", visible: true, x: 0, y: 0, w: 1000, h: 1000 }]);
  assert.deepEqual(framed.problems, []);
  // A widget shown again is checked like one that moved.
  const hidden = defaultLayout();
  widget(hidden, "date").visible = false;
  widget(hidden, "clock").h = 200;
  assert.match(applyChanges(hidden, [{ id: "date", visible: true }]).problems[0], /"date" \(.*\) overlaps "clock"/);
});

test("everything the checks allow, the mirror's own rules allow too", () => {
  const changes = [
    { id: "clock", x: 0, y: 850, w: 1000, h: 150 },
    { id: "date", x: 976, y: 976, w: 24, h: 24, opacity: 10, align: "center" },
    { id: "weather", visible: false },
  ];
  const { layout, problems } = applyChanges(defaultLayout(), changes, { overlapOk: true });
  assert.deepEqual(problems, []);
  assert.deepEqual(normalizeLayout(layout), layout);
});

test("a widget is described with its place, and no more than is needed", () => {
  const layout = defaultLayout();
  assert.deepEqual(describeWidget(widget(layout, "clock")), { id: "clock", visible: true, x: 50, y: 52, w: 560, h: 150, align: "start" });
  assert.deepEqual(describeWidget(widget(layout, "wifi")), { id: "wifi", visible: false, x: 50, y: 905, w: 170, h: 40 });
  assert.deepEqual(describeWidget({ ...widget(layout, "clock"), id: "clock-2", locked: true }), {
    id: "clock-2", type: "clock", visible: true, x: 50, y: 52, w: 560, h: 150, align: "start", locked: true,
  });
  assert.equal(describeWidget(widget(layout, "board")).size, "medium");
});
