import assert from "node:assert/strict";
import test from "node:test";
import { newTurn, toolsFor } from "../src/tools.js";
import { startTools } from "./helpers.js";

const chosen = (fake) => fake.writes().map((request) => [request.method, request.path, request.body]);

test("set_character makes the mirror answer as the one asked for, by its name or by what it is", async (t) => {
  const { use, fake } = await startTools(t);
  assert.deepEqual(await use("set_character", { character: "Mochi" }), { character: "Mochi (a cat)", was: "none", changed: true });
  assert.equal(fake.state.mascot, "mochi");
  assert.deepEqual(chosen(fake), [["PUT", "/api/v1/assistant", { mascot: "mochi" }]]);
  // People ask for what they see, and the model passes that on as it heard it.
  for (const [said, id, answer] of [
    ["the ghost", "wisp", "Wisp (a ghost)"],
    ["A Moon", "lune", "Lune (a moon)"],
    ["eyes", "blink", "Blink (two eyes)"],
    ["two eyes", "blink", "Blink (two eyes)"],
    ["cat", "mochi", "Mochi (a cat)"],
    ["wisp", "wisp", "Wisp (a ghost)"],
  ]) {
    fake.state.mascot = "none";
    assert.equal((await use("set_character", { character: said })).character, answer, said);
    assert.equal(fake.state.mascot, id, said);
  }
});

test("next goes through the characters in the mirror's order and round again", async (t) => {
  const { use, fake } = await startTools(t);
  const seen = [];
  for (let step = 0; step < 5; step += 1) {
    seen.push((await use("set_character", { character: "next" })).character);
  }
  assert.deepEqual(seen, ["Blink (two eyes)", "Wisp (a ghost)", "Mochi (a cat)", "Lune (a moon)", "Blink (two eyes)"]);
  assert.equal(fake.state.mascot, "blink");
});

test("none takes the character away and says which one it was", async (t) => {
  const { use, fake } = await startTools(t);
  fake.state.mascot = "lune";
  assert.deepEqual(await use("set_character", { character: "none" }), { character: "none", was: "Lune (a moon)", changed: true });
  assert.equal(fake.state.mascot, "none");
  assert.deepEqual(chosen(fake), [["PUT", "/api/v1/assistant", { mascot: "none" }]]);
});

test("asking for the one that is there changes nothing and says so", async (t) => {
  const { use, fake } = await startTools(t);
  fake.state.mascot = "wisp";
  const turn = newTurn("conversation");
  assert.deepEqual(await use("set_character", { character: "ghost" }, turn), {
    character: "Wisp (a ghost)", changed: false, note: "You already are Wisp.",
  });
  fake.state.mascot = "none";
  assert.match((await use("set_character", { character: "off" }, turn)).note, /There was no character/);
  // The mirror was not asked to show again what it shows.
  assert.equal(fake.writes().length, 0);
});

test("a character that is not there is answered with the ones that are", async (t) => {
  const { use, fake } = await startTools(t);
  const answer = await use("set_character", { character: "a dragon" });
  assert.equal(
    answer.error,
    'No character matches "a dragon". The choices are: Blink (two eyes), Wisp (a ghost), Mochi (a cat), Lune (a moon), none.',
  );
  assert.match((await use("set_character", {})).error, /The arguments are not right\. character/);
  assert.equal(fake.writes().length, 0);
});

test("a character that a later Mirror Home adds can be chosen by its name", async (t) => {
  const { use, fake } = await startTools(t);
  fake.state.mascots.push({ id: "pip", name: "Pip" });
  assert.deepEqual(await use("set_character", { character: "pip" }), { character: "Pip", was: "none", changed: true });
  assert.equal(fake.state.mascot, "pip");
});

test("a Mirror Home without characters says so, and one that refuses is passed on", async (t) => {
  const { use, fake } = await startTools(t);
  fake.state.mascots = null;
  assert.match((await use("set_character", { character: "cat" })).error, /has no characters yet/);
  assert.equal(fake.writes().length, 0);
  fake.state.mascots = [{ id: "blink", name: "Blink" }];
  fake.state.failing.set("/api/v1/assistant", 503);
  assert.match((await use("set_character", { character: "blink" })).error, /^The mirror refused|^I can't reach the display/);
});

test("only a conversation may change the character", async (t) => {
  const { tools } = await startTools(t);
  const allowed = (kind) => toolsFor(kind, tools).some((tool) => tool.name === "set_character");
  assert.deepEqual([allowed("conversation"), allowed("greeting"), allowed("tend")], [true, false, false]);
  assert.equal(tools.find((tool) => tool.name === "set_character").changes, true);
});
