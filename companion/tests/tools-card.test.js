import assert from "node:assert/strict";
import test from "node:test";
import { newTurn } from "../src/tools.js";
import { startTools } from "./helpers.js";

const row = (text, label = "To do") => ({ label, text });

test("present makes the turn's answer a card and ends the turn", async (t) => {
  const { use, fake, tools } = await startTools(t);
  const turn = newTurn("conversation");
  const answer = await use("present", { headline: " Three things\non your list ", rows: [row("Buy milk"), { text: "Call the plumber" }] }, turn);
  assert.deepEqual(answer, { shown: true });
  assert.deepEqual(turn.card, {
    reply: "Three things on your list",
    details: [{ label: "To do", text: "Buy milk" }, { label: "", text: "Call the plumber" }],
  });
  assert.deepEqual(turn.acted, ["present"]);
  assert.equal(fake.state.requests.length, 0, "it asks nothing of the mirror: the card goes out with the answer");
  for (const name of ["present", "briefing"]) {
    const tool = tools.find((candidate) => candidate.name === name);
    assert.equal(tool.endsTurn, true);
    assert.ok(!tool.changes, `${name} changes nothing on the mirror`);
  }
});

test("present takes a card at the limits: 60, 14 and 90 characters, five rows, 3 to 30 seconds", async (t) => {
  const { use } = await startTools(t);
  const turn = newTurn("conversation");
  const rows = [1, 2, 3, 4, 5].map(() => ({ label: "l".repeat(14), text: "t".repeat(90) }));
  assert.deepEqual(await use("present", { headline: "h".repeat(60), rows, seconds: 30 }, turn), { shown: true });
  assert.deepEqual([turn.card.reply.length, turn.card.details.length, turn.card.seconds], [60, 5, 30]);
  const none = newTurn("conversation");
  assert.deepEqual(await use("present", { headline: "Nothing on your list", rows: [], seconds: 3 }, none), { shown: true });
  assert.deepEqual(none.card, { reply: "Nothing on your list", details: [], seconds: 3 });
});

test("present names everything about a card that breaks the limits, and shows nothing", async (t) => {
  const { use } = await startTools(t);
  const turn = newTurn("conversation");
  const answer = await use(
    "present",
    {
      headline: "h".repeat(61),
      rows: [row("t".repeat(91)), row("fine", "l".repeat(15)), row("  "), row("four"), row("five"), row("six")],
      seconds: 45,
    },
    turn,
  );
  assert.equal(
    answer.error,
    "Nothing was shown. " +
      "The headline has 61 characters and may have 60: say less there and put the rest in rows. " +
      "There are 6 rows and a card holds 5: put several items in one row, with \" · \" between them. " +
      "The text of row 1 has 91 characters and may have 90: shorten it or split it over two rows. " +
      "The label of row 2 has 15 characters and may have 14. " +
      "Row 3 has no text. " +
      "seconds must be a whole number from 3 to 30. " +
      "Call present once more with that put right.",
  );
  assert.equal(turn.card, undefined);
  assert.match((await use("present", { headline: "List", rows: [row("x")], seconds: 2.5 }, newTurn("conversation"))).error, /seconds must be a whole number/);
  assert.match((await use("present", { headline: "List" }, newTurn("conversation"))).error, /The arguments are not right\. rows/);
  assert.match((await use("present", { headline: "List", rows: [{ label: "To do" }] }, newTurn("conversation"))).error, /The arguments are not right\. rows\.0\.text/);
});

test("a second card that breaks the limits is cut to fit, so that the person gets an answer", async (t) => {
  const { use } = await startTools(t);
  const turn = newTurn("conversation");
  const long = "Call the plumber about the dripping tap in the upstairs bathroom before the weekend, and then the boiler";
  const card = { headline: "Everything that is on your list for today and for the rest of this week", rows: [row(long, "Things to do today"), row(" ")], seconds: 90 };
  assert.match((await use("present", card, turn)).error, /Nothing was shown/);
  assert.deepEqual(await use("present", card, turn), { shown: true });
  assert.deepEqual(turn.card, {
    reply: "Everything that is on your list for today and for the...",
    details: [{ label: "Things to...", text: "Call the plumber about the dripping tap in the upstairs bathroom before the weekend..." }],
    seconds: 30,
  });
  assert.ok(turn.card.reply.length <= 60 && turn.card.details[0].label.length <= 14 && turn.card.details[0].text.length <= 90);
  // Without a headline there is nothing to cut down to.
  assert.match((await use("present", { headline: " ", rows: [row("x")] }, turn)).error, /The headline is empty/);
});

test("briefing builds the card from the mirror as it is now and keeps it for the turn", async (t) => {
  const { use, fake, clock } = await startTools(t);
  const stretch = fake.state.board.create({ kind: "reminder", title: "Stretch", due: clock.now() - 2 * 60 * 60 * 1000 }, "Kitchen agent");
  const turn = newTurn("conversation");
  const answer = await use("briefing", { kind: "home" }, turn);
  const details = [
    { label: "Weather", text: "Overcast, 12° now." },
    { label: "Missed", text: "Stretch, 2 hours ago" },
  ];
  assert.deepEqual(answer, { shown: { headline: "Welcome home", rows: details } });
  assert.deepEqual(turn.card, { reply: "Welcome home", details, seconds: 12 });
  assert.deepEqual(turn.briefing.briefing.missed, [stretch.id]);
  assert.equal(turn.briefing.state.items[0].title, "Stretch");
  assert.equal(fake.writes().length, 0, "a briefing changes nothing on the mirror");
  assert.deepEqual(fake.requests().map((request) => request.path).sort(), ["/api/v1/board/items?limit=100", "/api/v1/status"]);
  assert.match((await use("briefing", { kind: "noon" }, newTurn("conversation"))).error, /The arguments are not right\. kind/);
});

test("briefing says so when the mirror cannot be reached, and makes no card", async (t) => {
  const { use, fake } = await startTools(t);
  fake.state.dropNext = 1000;
  const turn = newTurn("conversation");
  assert.match((await use("briefing", { kind: "morning" }, turn)).error, /I can't reach the display right now/);
  assert.equal(turn.card, undefined);
});
