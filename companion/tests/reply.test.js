import assert from "node:assert/strict";
import test from "node:test";
import { asksSomething, fitRows, headline, oneLine, withoutRepeat } from "../src/reply.js";

test("a reply becomes one trimmed line", () => {
  assert.equal(oneLine("  The clock is\nat the bottom now.\n"), "The clock is at the bottom now.");
  assert.equal(oneLine("Two\r\n\r\nparagraphs"), "Two paragraphs");
});

test("markdown marks and emoji are taken out", () => {
  assert.equal(oneLine("**Done.** The `clock` moved \u{1F44D}"), "Done. The clock moved");
  assert.equal(oneLine("# Heading"), "Heading");
});

test("a long reply is cut between words and stays within the limit", () => {
  const long = "word ".repeat(60).trim();
  const cut = oneLine(long);
  assert.ok(cut.length <= 200, `length ${cut.length}`);
  assert.ok(cut.endsWith("word..."), cut.slice(-12));
  assert.ok(!cut.includes("wor..."));
  assert.equal(oneLine("x".repeat(250)), "x".repeat(197) + "...");
});

test("a reply of exactly the limit is left alone", () => {
  const exact = "a".repeat(199) + ".";
  assert.equal(oneLine(exact), exact);
});

test("a shorter limit serves the same way", () => {
  assert.equal(oneLine("one two three four", 12), "one two...");
});

test("typographic marks are written plainly", () => {
  assert.equal(oneLine("I\u2019ll remember \u201Cthe flowers\u201D \u2014 9\u201316\u00B0C\u2026"), "I'll remember \"the flowers\", 9-16\u00B0C...");
});

test("an answer given twice in a row is shown once", () => {
  assert.equal(withoutRepeat("Clock moved to the bottom.Clock moved to the bottom."), "Clock moved to the bottom.");
  assert.equal(withoutRepeat("Clock moved to the bottom.\nClock moved to the bottom."), "Clock moved to the bottom.");
  assert.equal(withoutRepeat("Bye bye"), "Bye bye");
  assert.equal(withoutRepeat("no no no no"), "no no no no");
  assert.equal(withoutRepeat("The clock is at the bottom. The date is above it."), "The clock is at the bottom. The date is above it.");
});

test("only a question asks for an answer", () => {
  assert.equal(asksSomething("Which film?"), true);
  assert.equal(asksSomething("Done."), false);
  assert.equal(asksSomething(""), false);
});

test("rows are cut to what a card holds: 14 and 90 characters on one line, five rows", () => {
  const long = "Call the plumber about the dripping tap in the upstairs bathroom before the weekend, and then the boiler";
  const rows = fitRows([
    { label: "To do", text: "Buy milk" },
    { label: "Things to do today", text: long },
    { text: "  **Water**\nthe plants \u{1F331} " },
    { label: "Empty", text: "   " },
    { label: "3:00 PM", text: "Dentist" },
    { label: "", text: "Five" },
    { label: "", text: "Six" },
  ]);
  assert.deepEqual(rows, [
    { label: "To do", text: "Buy milk" },
    { label: "Things to...", text: "Call the plumber about the dripping tap in the upstairs bathroom before the weekend..." },
    { label: "", text: "Water the plants" },
    { label: "3:00 PM", text: "Dentist" },
    { label: "", text: "Five" },
  ]);
  for (const row of rows) assert.ok(row.label.length <= 14 && row.text.length >= 1 && row.text.length <= 90);
  // A degree sign and the dot between items are plain enough for the glass and are kept.
  assert.deepEqual(fitRows([{ label: "Weather", text: "Clear, 62\u00B0 now \u00B7 no rain" }]), [{ label: "Weather", text: "Clear, 62\u00B0 now \u00B7 no rain" }]);
  assert.deepEqual(fitRows(undefined), []);
  assert.deepEqual(fitRows([null, {}, { label: 7, text: 12 }]), [{ label: "7", text: "12" }]);
});

test("with rows the line above them is a headline of at most 60 characters", () => {
  const long = "Everything that is on your list for today and for the rest of this week";
  assert.equal(headline(long, [{ label: "", text: "Buy milk" }]), "Everything that is on your list for today and for the...");
  assert.equal(headline(long, []), long, "without rows it is the whole answer, as before");
});
