import assert from "node:assert/strict";
import test from "node:test";
import { asksSomething, oneLine, withoutRepeat } from "../src/reply.js";

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
