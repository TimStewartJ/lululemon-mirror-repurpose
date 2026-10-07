import assert from "node:assert/strict";
import test from "node:test";
import {
  createModels,
  fauxAssistantMessage,
  fauxProvider,
  fauxText,
  fauxToolCall,
  getCurrentSystemPrompt,
  getCurrentTools,
  validateToolArguments,
} from "@earendil-works/pi-ai";
import { z } from "zod";
import { BrainError, createBrain } from "../src/brain.js";
import { createTools, newTurn } from "../src/tools.js";
import { fakeClock, until } from "./fakes/clock.js";

/**
 * Pi's own agent loop over Pi's scripted provider, which stands in for a
 * model. `answers` holds what the model says to each call, in order: a
 * message, or a function of the transcript it was sent. `signedIn` and
 * `offered` can be changed while a test runs.
 */
function fauxSource({ ids = ["gpt-6-luna", "another-model"], offered = ids } = {}) {
  const faux = fauxProvider({ provider: "faux", models: ids.map((id) => ({ id, reasoning: true, input: ["text", "image"] })) });
  const source = {
    faux,
    signedIn: true,
    offered,
    opened: [],
    /** What each call to the model was given, the first question of a start among them. */
    calls: [],
    answers: [],
    signIn: "Sign in with: node src/cli.js login faux",
  };
  const answer = async (context, options) => {
    source.calls.push({ context, options });
    const next = source.answers.shift() ?? "Done.";
    const said = typeof next === "function" ? await next(context, options) : next;
    // Pi's scripted provider does not look at the signal while a function of the script is awaited.
    if (options?.signal?.aborted) return fauxAssistantMessage("", { stopReason: "aborted", errorMessage: "Request was aborted" });
    return typeof said === "string" ? fauxAssistantMessage(said) : said;
  };
  // An answer for every call that could come; each takes the next one of the test's.
  faux.setResponses(Array.from({ length: 50 }, () => answer));
  const models = createModels();
  models.setProvider({
    ...faux.provider,
    auth: { apiKey: { name: "Faux", resolve: async () => (source.signedIn ? { auth: { apiKey: "a-key" }, source: "a test" } : undefined) } },
    filterModels: (all) => all.filter((model) => source.offered.includes(model.id)),
  });
  source.open = (options) => {
    source.opened.push(options);
    return { models, providerId: "faux", signIn: source.signIn, close: () => (source.closed += 1) };
  };
  source.closed = 0;
  return source;
}

const failed = (errorMessage) => fauxAssistantMessage("", { stopReason: "error", errorMessage });
const asks = (...calls) => fauxAssistantMessage(calls.map(([name, args]) => fauxToolCall(name, args)), { stopReason: "toolUse" });
/** What the tools answered, as the model reads it in the transcript. */
const results = (context) =>
  context.messages
    .filter((message) => message.role === "toolResult")
    .map((message) => ({ tool: message.toolName, failed: message.isError, content: message.content }));
const prompts = (context) =>
  context.messages.filter((message) => message.role === "user").map((message) => message.content.map((block) => block.text).join(""));

const calls = [];
const tools = [
  {
    name: "set_power",
    description: "Puts the display to sleep or wakes it.",
    schema: z.object({ state: z.enum(["asleep", "awake"]) }),
    changes: true,
    handler: async (args) => {
      calls.push(args);
      return { power: args.state };
    },
  },
  {
    name: "look",
    description: "Takes a picture of the glass.",
    schema: z.object({}),
    handler: async () => ({ image: { data: "anVzdCBhIHRlc3Q=", mimeType: "image/jpeg" }, text: "This is the glass as it is now." }),
  },
  {
    name: "ignore",
    description: "The words were not meant for the mirror.",
    schema: z.object({ reason: z.string() }),
    endsTurn: true,
    handler: async (args, turn) => {
      turn.ignored = args.reason;
      return { ignored: true };
    },
  },
];

async function startBrain(t, sourceOptions, brainOptions = {}) {
  const source = fauxSource(sourceOptions);
  const clock = fakeClock();
  const events = [];
  const brain = createBrain({
    provider: "faux",
    model: "gpt-6-luna",
    reasoningEffort: "low",
    authFile: "auth.json",
    clock,
    log: (event, fields) => events.push({ event, ...fields }),
    openModels: source.open,
    ...brainOptions,
  });
  t.after(() => brain.stop());
  await brain.start();
  calls.length = 0;
  const ask = (prompt, options = {}) =>
    brain.run({ session: "conversation", system: "You are the mirror.", prompt, tools, turn: newTurn("conversation"), timeoutMs: 30_000, ...options });
  /** The calls to the model for real work, not the first question of the start. */
  const asked = () => source.calls.slice(1);
  return { source, clock, events, brain, ask, asked };
}

test("at the start the model is looked for and asked a first question, whose answer is thrown away", async (t) => {
  const { source, brain, events } = await startBrain(t);
  assert.deepEqual(brain.health(), { ready: true, detail: "" });
  assert.deepEqual(source.opened.map(({ log, ...rest }) => rest), [{ provider: "faux", model: "gpt-6-luna", endpoint: null, authFile: "auth.json", userAgent: undefined }]);
  assert.equal(source.calls.length, 1);
  assert.equal(getCurrentSystemPrompt(source.calls[0].context.messages), "You are a test. Answer with the word ready.");
  assert.equal(source.calls[0].options.reasoning, "low");
  assert.deepEqual(events.at(-1), { event: "brain.ready", provider: "faux", model: "gpt-6-luna", signIn: "a test", reasoning: "low" });
});

test("a model the sign-in is not offered is reported, with the ones it is", async (t) => {
  const { brain, source, clock } = await startBrain(t, { ids: ["gpt-6-luna", "another-model", "a-third-model"], offered: ["another-model", "a-third-model"] });
  const health = brain.health();
  assert.equal(health.ready, false);
  assert.match(health.detail, /The model gpt-6-luna of faux is not usable: the model gpt-6-luna is not offered to this sign-in\. Set "model" in the config to one of: another-model, a-third-model\./);
  assert.match(health.detail, /Trying again in 5 s/);
  assert.equal(source.calls.length, 0, "a model that is not offered is not asked anything");
  await assert.rejects(brain.run({ session: "conversation", system: "", prompt: "hello", tools, turn: newTurn("conversation"), timeoutMs: 1000 }),
    (error) => error instanceof BrainError && error.kind === "not-ready");
  await clock.advance(5000);
  assert.equal(source.opened.length, 2, "it tries again");
  assert.match(brain.health().detail, /Trying again in 10 s/);
});

test("a model the provider does not have is told apart from one that is not offered", async (t) => {
  const { brain } = await startBrain(t, {}, { model: "gpt-7" });
  assert.match(brain.health().detail, /the model gpt-7 is not one that faux has\. Set "model" in the config to one of: gpt-6-luna, another-model\./);
});

test("with nobody signed in it says how to sign in, and tries again, less and less often, until someone is", async (t) => {
  const source = fauxSource();
  source.signedIn = false;
  const clock = fakeClock();
  const brain = createBrain({ provider: "faux", model: "gpt-6-luna", clock, openModels: source.open });
  t.after(() => brain.stop());
  await brain.start();
  assert.equal(brain.health().detail, "The model gpt-6-luna of faux is not usable: nobody is signed in to faux. Sign in with: node src/cli.js login faux. Trying again in 5 s.");
  await clock.advance(5000);
  assert.match(brain.health().detail, /Trying again in 10 s/);
  source.signedIn = true;
  await clock.advance(9999);
  assert.equal(source.opened.length, 2);
  await clock.advance(1);
  await until(() => brain.health().ready, "the brain to be ready");
  assert.deepEqual(brain.health(), { ready: true, detail: "" });
  assert.equal(source.opened.length, 3);
});

test("a key the provider does not accept shows at the start, in the provider's words", async (t) => {
  const source = fauxSource();
  source.answers.push(failed("401 invalid x-api-key"));
  const brain = createBrain({ provider: "faux", model: "gpt-6-luna", clock: fakeClock(), openModels: source.open });
  t.after(() => brain.stop());
  await brain.start();
  assert.equal(brain.health().detail, "The model gpt-6-luna of faux is not usable: 401 invalid x-api-key. Trying again in 5 s.");
});

test("a provider or an endpoint the config has wrong is reported as it is", async (t) => {
  const brain = createBrain({
    provider: "nowhere",
    model: "gpt-6-luna",
    clock: fakeClock(),
    openModels: () => {
      throw new Error('There is no provider called "nowhere"');
    },
  });
  t.after(() => brain.stop());
  await brain.start();
  assert.match(brain.health().detail, /The model gpt-6-luna of nowhere is not usable: There is no provider called "nowhere"\. Trying again/);
});

test("how hard to think is what the config says, or nothing where it says default or none", async (t) => {
  for (const [effort, wanted] of [["default", undefined], ["none", undefined], [undefined, undefined], ["medium", "medium"]]) {
    const { source, ask, asked } = await startBrain(t, {}, { reasoningEffort: effort });
    await ask("hello");
    assert.equal(source.calls[0].options.reasoning, wanted, `the first question with ${effort}`);
    assert.equal(asked()[0].options.reasoning, wanted, `a turn with ${effort}`);
  }
});

test("a session gets only the mirror's tools, the standing instructions and the model", async (t) => {
  const { ask, asked } = await startBrain(t);
  assert.deepEqual(await ask("go to sleep"), { text: "Done." });
  const { context, options } = asked()[0];
  assert.equal(getCurrentSystemPrompt(context.messages), "You are the mirror.");
  const declared = getCurrentTools(context.messages);
  assert.deepEqual(declared.map((tool) => tool.name), ["set_power", "look", "ignore"]);
  assert.equal(declared[0].description, "Puts the display to sleep or wakes it.");
  assert.deepEqual(declared[0].parameters, tools[0].schema.toJSONSchema());
  assert.deepEqual(prompts(context), ["go to sleep"]);
  assert.equal(options.reasoning, "low");
  assert.match(options.sessionId, /^[0-9a-f-]{36}$/);
});

test("a tool the model calls runs for the turn in hand, and a picture goes back as a picture", async (t) => {
  const { source, ask, events } = await startBrain(t);
  source.answers.push(
    asks(["set_power", { state: "asleep" }], ["look", {}], ["set_power", { state: "sideways" }]),
    (context) => JSON.stringify(results(context)),
  );
  const turn = newTurn("conversation");
  const { text } = await ask("go to sleep and look", { turn });
  const [power, picture, wrong] = JSON.parse(text);
  assert.deepEqual(power, { tool: "set_power", failed: false, content: [{ type: "text", text: '{"power":"asleep"}' }] });
  assert.deepEqual(picture, {
    tool: "look",
    failed: false,
    content: [
      { type: "text", text: "This is the glass as it is now." },
      { type: "image", data: "anVzdCBhIHRlc3Q=", mimeType: "image/jpeg" },
    ],
  });
  assert.equal(wrong.failed, true);
  assert.match(wrong.content[0].text, /Validation failed for tool "set_power"/);
  // A call that Pi turned down for its arguments is still one the model made.
  assert.deepEqual(turn.acted, ["set_power", "look", "set_power"]);
  assert.deepEqual(calls, [{ state: "asleep" }]);
  const refused = events.filter((entry) => entry.event === "tool" && entry.ok === false);
  assert.equal(refused.length, 1);
  assert.equal(refused[0].tool, "set_power");
  assert.equal(refused[0].args, '{"state":"sideways"}');
});

test("what a tool refuses goes back to the model as a failure, in the tool's own words", async (t) => {
  const picky = {
    name: "set_name",
    description: "Sets the name on the glass.",
    schema: z.object({ name: z.string() }),
    handler: async ({ name }) => (name.length > 12 ? { error: "The name is too long for the glass." } : { name }),
  };
  const { source, brain } = await startBrain(t);
  source.answers.push(asks(["set_name", { name: "Bartholomew the Third" }]), (context) => JSON.stringify(results(context)));
  const { text } = await brain.run({ session: "conversation", system: "", prompt: "call it Bartholomew the Third", tools: [picky], turn: newTurn("conversation"), timeoutMs: 30_000 });
  assert.deepEqual(JSON.parse(text), [{ tool: "set_name", failed: true, content: [{ type: "text", text: '{"error":"The name is too long for the glass."}' }] }]);
});

test("a conversation keeps its session until it is ended or a new one is asked for", async (t) => {
  const { brain, ask, asked } = await startBrain(t);
  await ask("show the clock");
  await ask("make it bigger");
  assert.deepEqual(prompts(asked()[1].context), ["show the clock", "make it bigger"]);
  assert.equal(asked()[1].options.sessionId, asked()[0].options.sessionId);
  await ask("something else entirely", { fresh: true });
  assert.deepEqual(prompts(asked()[2].context), ["something else entirely"]);
  assert.notEqual(asked()[2].options.sessionId, asked()[0].options.sessionId);
  await ask("and more of it");
  assert.deepEqual(prompts(asked()[3].context), ["something else entirely", "and more of it"]);
  await brain.endConversation();
  await ask("hello again");
  assert.deepEqual(prompts(asked()[4].context), ["hello again"]);
});

test("a proactive run has a session of its own, beside an open conversation", async (t) => {
  const { brain, ask, asked } = await startBrain(t);
  await ask("show the clock");
  await brain.run({ session: "proactive", system: "You are tending.", prompt: "look over the display", tools: [tools[0]], turn: newTurn("tend"), timeoutMs: 30_000 });
  const tending = asked()[1].context;
  assert.equal(getCurrentSystemPrompt(tending.messages), "You are tending.");
  assert.deepEqual(getCurrentTools(tending.messages).map((tool) => tool.name), ["set_power"]);
  assert.deepEqual(prompts(tending), ["look over the display"]);
  await ask("make it bigger");
  assert.deepEqual(prompts(asked()[2].context), ["show the clock", "make it bigger"], "the conversation is left open");
});

test("a turn that fails before any tool ran is tried once more with a new session", async (t) => {
  const { source, ask, asked, events } = await startBrain(t);
  source.answers.push(failed("503 the model service is overloaded"), "Here I am.");
  assert.deepEqual(await ask("are you there?"), { text: "Here I am." });
  assert.equal(asked().length, 2);
  assert.notEqual(asked()[1].options.sessionId, asked()[0].options.sessionId);
  assert.deepEqual(prompts(asked()[1].context), ["are you there?"]);
  assert.ok(events.some((entry) => entry.event === "brain.turn_failed" && entry.attempt === 1 && /overloaded/.test(entry.detail)));
  assert.ok(events.some((entry) => entry.event === "brain.model_call" && /overloaded/.test(entry.failed ?? "")));
});

test("a turn that fails twice gives up", async (t) => {
  const { source, ask, asked } = await startBrain(t);
  source.answers.push(failed("the model service answered 500"), failed("the model service answered 500"), "never reached");
  await assert.rejects(ask("hello"), (error) => error instanceof BrainError && error.kind === "failed" && /answered 500/.test(error.message));
  assert.equal(asked().length, 2);
});

test("a turn that fails after a tool has acted is never tried again", async (t) => {
  const { source, ask, asked } = await startBrain(t);
  source.answers.push(asks(["set_power", { state: "asleep" }]), failed("the connection dropped"), "must not be asked");
  const turn = newTurn("conversation");
  await assert.rejects(ask("go to sleep", { turn }), (error) => error instanceof BrainError && error.kind === "failed");
  assert.equal(asked().length, 2);
  assert.deepEqual(calls, [{ state: "asleep" }], "the tool acted once");
  assert.equal(source.answers.length, 1, "the third answer was never asked for");
  // The broken session is not used for the next request either.
  source.answers.length = 0;
  await ask("hello");
  assert.deepEqual(prompts(asked()[2].context), ["hello"]);
});

test("a turn that takes too long is stopped, and what the model asks for afterwards is not done", async (t) => {
  const { source, ask, clock, asked } = await startBrain(t);
  let answer;
  source.answers.push(() => new Promise((resolve) => (answer = resolve)));
  const pending = ask("think forever", { timeoutMs: 30_000 });
  const refused = assert.rejects(pending, (error) => error instanceof BrainError && error.kind === "timeout");
  await until(() => answer !== undefined, "the model to be asked");
  await clock.advance(30_000);
  await refused;
  assert.equal(asked()[0].options.signal.aborted, true, "the model's work is stopped");
  answer(asks(["set_power", { state: "asleep" }]));
  await clock.advance(0);
  assert.deepEqual(calls, []);
  // The session that was cut off is not the one the next request gets.
  await ask("hello");
  assert.deepEqual(prompts(asked()[1].context), ["hello"]);
});

test("a tool that is still at work when the time is up finishes, and the calls after it are not made", async (t) => {
  let release;
  const slow = {
    name: "slow",
    description: "Takes its time.",
    schema: z.object({}),
    handler: () => new Promise((resolve) => (release = () => resolve({ done: true }))),
  };
  const { source, brain, clock, asked } = await startBrain(t);
  source.answers.push(asks(["slow", {}], ["set_power", { state: "asleep" }]), "must not be asked");
  const turn = newTurn("conversation");
  const pending = brain.run({ session: "conversation", system: "", prompt: "do both", tools: [slow, tools[0]], turn, timeoutMs: 30_000 });
  const refused = assert.rejects(pending, (error) => error instanceof BrainError && error.kind === "timeout");
  await until(() => release !== undefined, "the slow tool to be called");
  await clock.advance(30_000);
  await refused;
  release();
  await clock.advance(0);
  assert.deepEqual(turn.acted, ["slow"]);
  assert.deepEqual(calls, []);
  assert.equal(asked().length, 1);
});

test("a run can be stopped by its signal", async (t) => {
  const { source, brain, asked } = await startBrain(t);
  source.answers.push(() => new Promise(() => {}));
  const controller = new AbortController();
  const pending = brain.run({
    session: "proactive", system: "", prompt: "tend", tools, turn: newTurn("tend"), timeoutMs: 30_000, signal: controller.signal,
  });
  const refused = assert.rejects(pending, (error) => error instanceof BrainError && error.kind === "aborted");
  await until(() => asked().length === 1, "the model to be asked");
  controller.abort();
  await refused;
  assert.equal(asked()[0].options.signal.aborted, true);
});

test("a sign-in that has gone is noticed when a request fails, and the model is reached again once it is back", async (t) => {
  const { source, ask, clock, brain } = await startBrain(t);
  await ask("hello");
  // Pi fails the request itself when nobody is signed in.
  source.signedIn = false;
  await assert.rejects(ask("are you still there?"), (error) => error instanceof BrainError && error.kind === "not-ready" && /nobody is signed in to faux/.test(error.message));
  assert.equal(brain.health().ready, false);
  source.signedIn = true;
  await clock.advance(5000);
  await until(() => brain.health().ready, "the brain to be ready");
  assert.deepEqual(await ask("and now?"), { text: "Done." });
});

test("a sign-in that went while nobody asked is found by the minute's check", async (t) => {
  const { source, clock, brain, events } = await startBrain(t);
  await clock.advance(60_000);
  assert.equal(brain.health().ready, true);
  assert.equal(source.opened.length, 1);
  source.signedIn = false;
  await clock.advance(60_000);
  assert.deepEqual(events.find((entry) => entry.event === "brain.lost"), { event: "brain.lost", detail: "the sign-in is gone" });
  assert.equal(brain.health().ready, false);
  source.signedIn = true;
  await clock.advance(5000);
  await until(() => brain.health().ready, "the brain to be ready");
});

test("a session asked for ahead is the one the turn uses", async (t) => {
  const { brain, ask, asked } = await startBrain(t);
  brain.prepare({ fresh: true, system: "You are the mirror.", tools });
  brain.prepare({ fresh: true, system: "You are the mirror.", tools });
  await ask("good morning", { fresh: true });
  brain.prepare({ fresh: false, system: "You are the mirror.", tools });
  await ask("and the weather?");
  assert.deepEqual(prompts(asked()[1].context), ["good morning", "and the weather?"], "one session, although a new conversation was asked for three times");
  brain.prepare({ fresh: true, system: "You are the mirror.", tools });
  await ask("a new subject", { fresh: true });
  assert.deepEqual(prompts(asked()[2].context), ["a new subject"]);
});

test("stopping ends the conversation and the checks", async (t) => {
  const { brain, ask, clock, source } = await startBrain(t);
  await ask("hello");
  assert.equal(source.closed, 0);
  await brain.stop();
  assert.equal(source.closed, 1, "what the providers kept open is closed");
  assert.deepEqual(brain.health(), { ready: false, detail: "Stopped." });
  await assert.rejects(ask("hello?"), (error) => error instanceof BrainError && error.kind === "not-ready");
  await clock.advance(120_000);
  assert.equal(source.opened.length, 1);
  assert.equal(clock.pending(), 0);
});

test("only the Pi harness exists so far", () => {
  assert.throws(() => createBrain({ harness: "other", provider: "x", model: "x" }), /no agent harness called "other"/);
});

test("a tool that ends the turn ends it without another call to the model, but not when it refused", async (t) => {
  const present = {
    name: "present",
    description: "Shows the answer as a card.",
    schema: z.object({ headline: z.string() }),
    endsTurn: true,
    handler: async ({ headline }) => (headline.length > 60 ? { error: "The headline is too long." } : { shown: true }),
  };
  const { source, brain, asked } = await startBrain(t);
  const run = (turn) =>
    brain.run({ session: "conversation", fresh: true, system: "You are the mirror.", prompt: "what's on my list?", tools: [...tools, present], turn, timeoutMs: 30_000 });

  source.answers.push(asks(["present", { headline: "Three things" }]), "must not be asked");
  assert.deepEqual(await run(newTurn("conversation")), { text: "" });
  assert.equal(asked().length, 1, "the turn was over with the card");
  source.answers.length = 0;

  // Refused by the tool, then by Pi for its arguments: both times the model reads why and goes on.
  let seen;
  source.answers.push(
    asks(["present", { headline: "x".repeat(61) }]),
    asks(["present", {}]),
    (context) => {
      seen = results(context);
      return fauxAssistantMessage([fauxText("Here it is."), fauxToolCall("present", { headline: "Three things" })], { stopReason: "toolUse" });
    },
    "must not be asked",
  );
  const turn = newTurn("conversation");
  assert.deepEqual(await run(turn), { text: "Here it is." });
  assert.deepEqual(seen.map((result) => result.failed), [true, true]);
  assert.equal(seen[0].content[0].text, '{"error":"The headline is too long."}');
  assert.match(seen[1].content[0].text, /Validation failed for tool "present"/);
  assert.deepEqual(turn.acted, ["present", "present", "present"]);
  assert.equal(asked().length, 4);

  // Beside a call that goes on, a call that ends the turn does not end it.
  source.answers.length = 0;
  source.answers.push(asks(["ignore", { reason: "talk" }], ["set_power", { state: "awake" }]), "Awake.");
  assert.deepEqual(await run(newTurn("conversation")), { text: "Awake." });
});

test("where the provider tells a remark from the answer, the answer alone is the text", async (t) => {
  const { source, ask } = await startBrain(t);
  const signed = (text, phase) => ({ ...fauxText(text), textSignature: JSON.stringify({ v: 1, id: `msg_${phase}`, phase }) });
  source.answers.push(fauxAssistantMessage([signed("I will look at the state first.", "commentary"), signed("It is half past seven.", "final_answer")]));
  assert.deepEqual(await ask("what time is it?"), { text: "It is half past seven." });
  source.answers.push(fauxAssistantMessage([fauxText("Half past "), fauxText("seven.")]));
  assert.deepEqual(await ask("and now?"), { text: "Half past seven." });
});

test("each call to the model leaves a line with its time and its tokens", async (t) => {
  const { ask, events } = await startBrain(t);
  await ask("hello");
  const line = events.find((entry) => entry.event === "brain.model_call");
  assert.ok(line.ms >= 0 && line.firstTokenMs >= 0 && line.firstTokenMs <= line.ms + 1);
  assert.ok(line.inputTokens > 0 && line.outputTokens > 0);
  assert.equal(line.failed, undefined);
});

test("every tool of the mirror has a schema that Pi can check arguments against", () => {
  const all = createTools({ mirror: {}, memory: {}, clock: fakeClock(), log: () => {} });
  assert.ok(all.length >= 25);
  for (const tool of all) {
    const declared = { name: tool.name, description: tool.description, parameters: tool.schema.toJSONSchema() };
    assert.equal(declared.parameters.type, "object", tool.name);
    try {
      validateToolArguments(declared, { type: "toolCall", id: "call-1", name: tool.name, arguments: { "no-such-argument": true } });
      assert.fail(`${tool.name} took an argument it does not have`);
    } catch (error) {
      // Anything else would be Pi failing on the schema itself, which would refuse every call of the tool.
      assert.match(error.message, /^Validation failed for tool/, tool.name);
    }
  }
});
