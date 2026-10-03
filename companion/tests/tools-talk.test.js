import assert from "node:assert/strict";
import test from "node:test";
import { newTurn } from "../src/tools.js";
import { startTools } from "./helpers.js";

test("say shows one line as a reply in a conversation and as a notice otherwise", async (t) => {
  const { use, fake } = await startTools(t);
  assert.deepEqual(await use("say", { text: "One moment,\nI am looking.", seconds: 4 }), { shown: true });
  assert.deepEqual(await use("say", { text: "Good morning. Rain from eleven." }, newTurn("greeting")), { shown: true });
  assert.deepEqual(fake.state.said, [
    { text: "One moment, I am looking.", kind: "reply", seconds: 4, shown: true },
    { text: "Good morning. Rain from eleven.", kind: "notice", seconds: null, shown: true },
  ]);
});

test("say reports a dark display, which shows nothing", async (t) => {
  const { use, fake } = await startTools(t);
  fake.state.automation.sleeping = true;
  const turn = newTurn("greeting");
  assert.deepEqual(await use("say", { text: "Hello" }, turn), { shown: false, reason: "sleeping" });
  assert.equal(turn.said, undefined);
});

test("say refuses a text that is empty or too long, and a greeting that comes too late", async (t) => {
  const { use, fake, clock } = await startTools(t);
  assert.match((await use("say", { text: "" })).error, /arguments are not right/);
  assert.match((await use("say", { text: "x".repeat(201) })).error, /arguments are not right/);
  assert.match((await use("say", { text: "Hello", seconds: 60 })).error, /arguments are not right/);
  const late = newTurn("greeting");
  late.deadline = clock.now() - 1;
  assert.match((await use("say", { text: "Good morning" }, late)).error, /Too late/);
  assert.equal(fake.state.said.length, 0);
});

test("remember and forget work on the memory", async (t) => {
  const { use, memory } = await startTools(t);
  assert.deepEqual(await use("remember", { note: "Sam prefers the flowers film" }), { remembered: "Sam prefers the flowers film" });
  assert.deepEqual(memory.lines(), ["Sam prefers the flowers film"]);
  assert.deepEqual(await use("forget", { containing: "flowers" }), { forgotten: ["Sam prefers the flowers film"] });
  assert.deepEqual(memory.lines(), []);
  assert.match((await use("forget", { containing: "flowers" })).error, /No note contains/);
  assert.match((await use("remember", { note: "" })).error, /arguments are not right/);
});

test("ignore marks the turn and touches nothing", async (t) => {
  const { use, fake, tools } = await startTools(t);
  const turn = newTurn("conversation");
  assert.deepEqual(await use("ignore", { reason: "two people talking about a mirror" }, turn), { ignored: true });
  assert.equal(turn.ignored, "two people talking about a mirror");
  assert.deepEqual(turn.acted, ["ignore"]);
  assert.equal(fake.state.requests.length, 0);
  assert.equal(tools.find((tool) => tool.name === "ignore").endsTurn, true);
});

test("every tool has a description and a schema the harness can pass on", async (t) => {
  const { tools } = await startTools(t);
  assert.deepEqual(
    tools.map((tool) => tool.name).sort(),
    [
      "arrange_widgets", "board_add", "board_remove", "board_update", "briefing", "forget", "get_state", "ignore",
      "look", "present", "remember", "say", "set_background", "set_brightness", "set_power",
    ],
  );
  for (const tool of tools) {
    assert.ok(tool.description.length > 40, `${tool.name} needs a description`);
    const schema = tool.schema.toJSONSchema();
    assert.equal(schema.type, "object", `${tool.name} takes an object`);
  }
  // The coordinate system is explained where the model reads it.
  assert.match(tools.find((tool) => tool.name === "arrange_widgets").description, /thousandths of the screen.*0,0 at the top left/);
});
