import assert from "node:assert/strict";
import test from "node:test";
import { newTurn } from "../src/tools.js";
import { startTools } from "./helpers.js";

/** A turn that knows the mirror's clock and zone, as every real turn does. */
async function turnWithState(use) {
  const turn = newTurn("conversation");
  await use("get_state", {}, turn);
  turn.acted.length = 0;
  return turn;
}

test("board_add puts a reminder on the board at the right instant", async (t) => {
  const { use, fake } = await startTools(t);
  fake.widget("board").visible = true;
  const turn = await turnWithState(use);
  const answer = await use("board_add", { kind: "reminder", title: "  Take out\nthe trash ", due: "2026-10-04T07:00:00-07:00" }, turn);
  assert.equal(answer.added.title, "Take out the trash");
  assert.equal(answer.added.due, "2026-10-04T07:00:00-07:00");
  assert.equal(answer.added.state, "open");
  assert.match(answer.added.id, /^[a-z0-9]{8}$/);
  assert.equal(answer.boardNowShown, undefined);
  const stored = fake.state.board.items[0];
  assert.equal(stored.due, Date.UTC(2026, 9, 4, 14, 0, 0));
  assert.equal(stored.kind, "reminder");
  assert.equal(stored.source, "Mirror companion");
});

test("board_add lets a to-do stay a week, and leaves other lifetimes to the mirror", async (t) => {
  const { use, fake, clock } = await startTools(t);
  const turn = await turnWithState(use);
  await use("board_add", { kind: "todo", title: "Buy milk" }, turn);
  await use("board_add", { kind: "todo", title: "Call the plumber", due: "2026-10-03T18:00:00-07:00" }, turn);
  await use("board_add", { kind: "note", title: "Dinner is in the oven" }, turn);
  await use("board_add", { kind: "todo", title: "Water the plants", ttlSeconds: 3600 }, turn);
  const day = 24 * 60 * 60 * 1000;
  assert.deepEqual(
    fake.state.board.items.map((item) => item.expiresAt - clock.now()),
    [7 * day, Date.UTC(2026, 9, 4, 1, 0, 0) + day - clock.now(), day, 3600_000],
  );
});

test("board_add reads a due time in any offset as the same instant", async (t) => {
  const { use, fake } = await startTools(t);
  const turn = await turnWithState(use);
  await use("board_add", { kind: "todo", title: "One", due: "2026-10-04T14:00:00Z" }, turn);
  await use("board_add", { kind: "todo", title: "Two", due: "2026-10-04T16:00+02:00" }, turn);
  assert.deepEqual(fake.state.board.items.map((item) => item.due), [Date.UTC(2026, 9, 4, 14), Date.UTC(2026, 9, 4, 14)]);
});

test("board_add refuses a due time without an offset and shows how to write it", async (t) => {
  const { use, fake } = await startTools(t);
  const turn = await turnWithState(use);
  const answer = await use("board_add", { kind: "reminder", title: "Trash", due: "2026-10-04T07:00:00" }, turn);
  assert.equal(
    answer.error,
    "due needs its offset from UTC. The mirror's offset is -07:00. Write it like 2026-10-03T08:12:00-07:00.",
  );
  assert.match((await use("board_add", { kind: "reminder", title: "Trash", due: "tomorrow at 7" }, turn)).error, /not a time the mirror can read\. Write it like/);
  assert.match((await use("board_add", { kind: "reminder", title: "Trash", due: "2026-02-30T07:00:00Z" }, turn)).error, /not a real date and time/);
  assert.equal(fake.writes().length, 0);
});

test("board_add refuses a time that has passed, by the mirror's clock", async (t) => {
  const { use, fake } = await startTools(t);
  const turn = await turnWithState(use);
  const answer = await use("board_add", { kind: "reminder", title: "Trash", due: "2026-10-03T07:00:00-07:00" }, turn);
  assert.match(answer.error, /already passed: the mirror's clock reads 2026-10-03T07:12:00-07:00/);
  assert.equal(fake.writes().length, 0);
});

test("board_add knows which kinds take a due time", async (t) => {
  const { use, fake } = await startTools(t);
  assert.match((await use("board_add", { kind: "reminder", title: "Trash" })).error, /A reminder needs due.*ask when/);
  assert.match((await use("board_add", { kind: "note", title: "Hi", due: "2026-10-04T07:00:00-07:00" })).error, /A note has no due time/);
  assert.match((await use("board_add", { kind: "task", title: "Hi" })).error, /arguments are not right\. kind/);
  assert.match((await use("board_add", { kind: "note", title: "x".repeat(121) })).error, /arguments are not right\. title/);
  assert.equal(fake.writes().length, 0);
});

test("board_add shows a hidden board for a person, and only then", async (t) => {
  const { use, fake } = await startTools(t);
  assert.equal(fake.widget("board").visible, false);
  const before = structuredClone(fake.state.layout);
  const answer = await use("board_add", { kind: "todo", title: "Buy milk", priority: "high", body: "Two litres", ttlSeconds: 604800 });
  assert.equal(answer.boardNowShown, true);
  assert.equal(fake.widget("board").visible, true);
  // Showing the board changed that one field of the layout.
  const expected = structuredClone(before);
  expected.widgets.find((widget) => widget.id === "board").visible = true;
  assert.deepEqual(fake.state.layout, expected);
  const stored = fake.state.board.items[0];
  assert.deepEqual([stored.priority, stored.body, stored.autoExpiry], ["high", "Two litres", false]);

  const second = await use("board_add", { kind: "todo", title: "Buy eggs" });
  assert.equal(second.boardNowShown, undefined);
});

test("board_add makes a board that lists only another kind list everything again", async (t) => {
  const { use, fake } = await startTools(t);
  Object.assign(fake.widget("board"), { visible: true, show: "reminder" });
  const turn = await turnWithState(use);
  const reminder = await use("board_add", { kind: "reminder", title: "Trash", due: "2026-10-04T07:00:00-07:00" }, turn);
  assert.equal(reminder.boardNowShown, undefined, "a reminder is listed as things are");
  assert.equal(fake.widget("board").show, "reminder");
  const todo = await use("board_add", { kind: "todo", title: "Buy milk" }, turn);
  assert.equal(todo.boardNowShown, true);
  assert.equal(fake.widget("board").show, "all");
  assert.equal(turn.state.snapshot.widgets.find((widget) => widget.id === "board").show, undefined);
  // With the board known to list everything, a further item costs no look at the layout.
  const before = fake.requests().length;
  await use("board_add", { kind: "note", title: "Welcome home" }, turn);
  assert.deepEqual(fake.requests().slice(before).map((request) => request.method), ["PUT"]);
});

test("a tending run would not put the board on the glass by itself", async (t) => {
  const { use, fake } = await startTools(t);
  await use("board_add", { kind: "note", title: "Posted by a run of its own" }, newTurn("tend"));
  assert.equal(fake.widget("board").visible, false);
});

test("board_update marks an item done and changes its fields", async (t) => {
  const { use, fake } = await startTools(t);
  const turn = await turnWithState(use);
  const { added } = await use("board_add", { kind: "todo", title: "Buy milk" }, turn);
  const done = await use("board_update", { id: added.id, done: true }, turn);
  assert.equal(done.updated.state, "done");
  assert.equal(done.updated.doneAt, "2026-10-03T07:12:00-07:00");
  const changed = await use("board_update", { id: added.id, done: false, title: "Buy oat milk", due: "2026-10-03T18:00:00-07:00", priority: "low" }, turn);
  assert.deepEqual(
    [changed.updated.title, changed.updated.due, changed.updated.priority, changed.updated.state],
    ["Buy oat milk", "2026-10-03T18:00:00-07:00", "low", "open"],
  );
  assert.equal(fake.state.board.items[0].due, Date.UTC(2026, 9, 4, 1, 0, 0));
});

test("board_update passes on the mirror's refusals in its own words", async (t) => {
  const { use } = await startTools(t);
  const turn = await turnWithState(use);
  assert.match((await use("board_update", { id: "nothere1", done: true }, turn)).error, /The mirror refused: The board has no item "nothere1"/);
  const { added } = await use("board_add", { kind: "note", title: "Welcome home" }, turn);
  assert.match((await use("board_update", { id: added.id, done: true }, turn)).error, /A note cannot be done/);
  assert.match((await use("board_update", { id: added.id }, turn)).error, /Say what to change/);
  assert.match((await use("board_update", { id: added.id, due: "2026-10-04T07:00:00" }, turn)).error, /needs its offset/);
});

test("board_remove takes off one item, the finished ones, or everything", async (t) => {
  const { use, fake } = await startTools(t);
  const ids = [];
  for (const title of ["One", "Two", "Three", "Four"]) ids.push((await use("board_add", { kind: "todo", title })).added.id);
  assert.deepEqual(await use("board_remove", { id: ids[0] }), { removed: 1 });
  await use("board_update", { id: ids[1], done: true });
  assert.deepEqual(await use("board_remove", { all: "done" }), { removed: 1 });
  assert.deepEqual(fake.state.board.items.map((item) => item.title), ["Three", "Four"]);
  assert.deepEqual(await use("board_remove", { all: "everything" }), { removed: 2 });
  assert.deepEqual(await use("board_remove", { all: "done" }), { removed: 0 });
});

test("board_remove refuses what is unclear or not there", async (t) => {
  const { use } = await startTools(t);
  assert.match((await use("board_remove", {})).error, /Give either the id of one item, or all/);
  assert.match((await use("board_remove", { id: "abc", all: "done" })).error, /Give either/);
  assert.match((await use("board_remove", { id: "nothere1" })).error, /The board has no item "nothere1"/);
  assert.match((await use("board_remove", { all: "some" })).error, /arguments are not right/);
});

test("board_add puts a hidden board where it covers nothing, and says where it went", async (t) => {
  const { use, fake } = await startTools(t);
  // The note was made bigger while the board was hidden, over the place the board is kept.
  Object.assign(fake.widget("note"), { visible: true, x: 40, y: 320, w: 715, h: 320 });
  const turn = await turnWithState(use);
  const answer = await use("board_add", { kind: "todo", title: "Buy milk" }, turn);
  assert.equal(answer.boardNowShown, true);
  assert.equal(answer.boardMoved, 'Its usual place was taken, so "board" is now below "note".');
  const board = fake.widget("board");
  assert.deepEqual([board.visible, board.x, board.y, board.w, board.h], [true, 50, 660, 520, 230]);
  assert.deepEqual(turn.state.snapshot.widgets.find((widget) => widget.id === "board"), {
    id: "board", visible: true, x: 50, y: 660, w: 520, h: 230, align: "start", size: "medium",
  });
  // Nothing else moved, and a second item leaves the board where it now is.
  const note = fake.widget("note");
  assert.deepEqual([note.x, note.y, note.w, note.h], [40, 320, 715, 320]);
  const second = await use("board_add", { kind: "todo", title: "Buy eggs" }, turn);
  assert.deepEqual([second.boardNowShown, second.boardMoved], [undefined, undefined]);
  assert.equal(fake.widget("board").y, 660);
});

test("board_add says nothing of the board's place when it could stay, and leaves a locked board where it is", async (t) => {
  const { use, fake } = await startTools(t);
  const first = await use("board_add", { kind: "todo", title: "Buy milk" });
  assert.deepEqual([first.boardNowShown, first.boardMoved], [true, undefined]);
  fake.widget("board").visible = false;
  fake.widget("board").locked = true;
  Object.assign(fake.widget("note"), { visible: true, x: 40, y: 320, w: 715, h: 320 });
  const locked = await use("board_add", { kind: "todo", title: "Buy eggs" });
  assert.deepEqual([locked.boardNowShown, locked.boardMoved], [true, undefined]);
  assert.deepEqual([fake.widget("board").x, fake.widget("board").y], [50, 440]);
});

test("board_add makes the board less tall where its height fits nowhere", async (t) => {
  const { use, fake } = await startTools(t);
  for (const id of ["clock", "date", "weather", "forecast"]) fake.widget(id).visible = false;
  Object.assign(fake.widget("photo"), { visible: true, x: 0, y: 0, w: 1000, h: 800 });
  const answer = await use("board_add", { kind: "todo", title: "Buy milk" });
  assert.equal(answer.boardMoved, 'Its usual place was taken, so "board" is now at x 50, y 820. It is less tall than before.');
  assert.deepEqual([fake.widget("board").y, fake.widget("board").h], [820, 160]);
});
