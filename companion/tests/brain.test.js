import assert from "node:assert/strict";
import test from "node:test";
import { z } from "zod";
import { BrainError, createBrain } from "../src/brain.js";
import { newTurn } from "../src/tools.js";
import { fakeClock, until } from "./fakes/clock.js";

/**
 * A stand-in for @github/copilot-sdk. `turns` holds what the model does for
 * each prompt: a function that gets the session and returns the final text,
 * calling the session's tools on the way if it likes.
 */
function fakeSdk({ models = ["gpt-6-luna", "another-model"], turns = [] } = {}) {
  const sdk = {
    clients: [],
    sessions: [],
    deleted: [],
    turns,
    startFailures: 0,
    approveAll: () => ({ kind: "approved" }),
    defineTool: (name, config) => ({ name, ...config }),
  };
  class Session {
    constructor(config, client) {
      this.config = config;
      this.client = client;
      this.sessionId = `session-${sdk.sessions.length + 1}`;
      this.prompts = [];
    }

    /** Calls one of the session's tools, as the runtime does for the model. */
    use(name, args) {
      return this.config.tools.find((tool) => tool.name === name).handler(args);
    }

    async sendAndWait({ prompt }) {
      if (!this.client.alive) throw new Error("Connection is closed.");
      this.prompts.push(prompt);
      const turn = sdk.turns.shift() ?? (() => "Done.");
      const content = await turn(this);
      return { data: { content } };
    }

    async abort() {
      this.aborted = true;
    }

    async disconnect() {
      this.disconnected = true;
    }
  }
  sdk.CopilotClient = class {
    constructor(options) {
      this.options = options;
      this.alive = false;
      sdk.clients.push(this);
    }

    async start() {
      if (sdk.startFailures > 0) {
        sdk.startFailures -= 1;
        throw new Error("No authentication information found");
      }
      this.alive = true;
    }

    async stop() {
      this.alive = false;
      return [];
    }

    async forceStop() {
      this.alive = false;
    }

    async listModels() {
      return models.map((id) => ({ id }));
    }

    async ping() {
      if (!this.alive) throw new Error("Connection is closed.");
      return { message: "pong" };
    }

    async createSession(config) {
      if (!this.alive) throw new Error("Connection is closed.");
      const session = new Session(config, this);
      sdk.sessions.push(session);
      return session;
    }

    async deleteSession(id) {
      sdk.deleted.push(id);
    }
  };
  return sdk;
}

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

async function startBrain(t, sdkOptions) {
  const sdk = fakeSdk(sdkOptions);
  const clock = fakeClock();
  const events = [];
  const brain = createBrain({
    model: "gpt-6-luna",
    workingDirectory: "state-directory",
    clock,
    log: (event, fields) => events.push({ event, ...fields }),
    loadSdk: async () => sdk,
  });
  t.after(() => brain.stop());
  await brain.start();
  calls.length = 0;
  const ask = (prompt, options = {}) =>
    brain.run({ session: "conversation", system: "You are the mirror.", prompt, tools, turn: newTurn("conversation"), timeoutMs: 30_000, ...options });
  /** The sessions opened for real work, not the one opened and closed at the start. */
  const sessions = () => sdk.sessions.slice(1);
  return { sdk, clock, events, brain, ask, sessions };
}

test("at the start the model is checked and a first session is opened and thrown away", async (t) => {
  const { sdk, brain } = await startBrain(t);
  assert.deepEqual(brain.health(), { ready: true, detail: "" });
  assert.equal(sdk.clients[0].options.workingDirectory, "state-directory");
  assert.equal(sdk.sessions.length, 1);
  assert.equal(sdk.sessions[0].disconnected, true);
  assert.deepEqual(sdk.deleted, ["session-1"]);
});

test("a model the login is not offered is reported, with the ones it is", async (t) => {
  const { brain, sdk, clock } = await startBrain(t, { models: ["another-model", "a-third-model"] });
  const health = brain.health();
  assert.equal(health.ready, false);
  assert.match(health.detail, /the model gpt-6-luna is not offered to this Copilot login\. Set "model" in the config to one of: another-model, a-third-model/);
  assert.match(health.detail, /Trying again in 5 s/);
  await assert.rejects(brain.run({ session: "conversation", system: "", prompt: "hello", tools, turn: newTurn("conversation"), timeoutMs: 1000 }),
    (error) => error instanceof BrainError && error.kind === "not-ready");
  await clock.advance(5000);
  assert.equal(sdk.clients.length, 2, "it tries again");
  assert.match(brain.health().detail, /Trying again in 10 s/);
});

test("a client that cannot start is tried again, less and less often, until it can", async (t) => {
  const sdk = fakeSdk();
  sdk.startFailures = 2;
  const clock = fakeClock();
  const brain = createBrain({ model: "gpt-6-luna", workingDirectory: ".", clock, loadSdk: async () => sdk });
  t.after(() => brain.stop());
  await brain.start();
  assert.match(brain.health().detail, /GitHub Copilot is not usable: No authentication information found\. Check the login with the Copilot CLI/);
  await clock.advance(5000);
  assert.equal(brain.health().ready, false);
  await clock.advance(9999);
  assert.equal(sdk.clients.length, 2);
  await clock.advance(1);
  assert.deepEqual(brain.health(), { ready: true, detail: "" });
  assert.equal(sdk.clients.length, 3);
});

test("a session gets only the mirror's tools, the standing instructions in place of the default, and the model", async (t) => {
  const { ask, sessions } = await startBrain(t);
  assert.deepEqual(await ask("go to sleep"), { text: "Done." });
  const { config } = sessions()[0];
  assert.equal(config.model, "gpt-6-luna");
  assert.deepEqual(config.availableTools, ["custom:*"]);
  assert.deepEqual(config.systemMessage, { mode: "replace", content: "You are the mirror." });
  assert.equal(config.enableSessionStore, false);
  assert.equal(config.streaming, true);
  assert.equal(config.reasoningEffort, undefined);
  assert.deepEqual(config.tools.map((tool) => [tool.name, tool.skipPermission, tool.isTerminal]), [
    ["set_power", true, false],
    ["look", true, false],
    ["ignore", true, true],
  ]);
  assert.equal(config.tools[0].description, "Puts the display to sleep or wakes it.");
  assert.equal(config.tools[0].parameters, tools[0].schema);
  assert.deepEqual(sessions()[0].prompts, ["go to sleep"]);
});

test("a tool the model calls runs for the turn in hand, and a picture goes back as a picture", async (t) => {
  const { ask } = await startBrain(t, {
    turns: [
      async (session) => {
        const power = await session.use("set_power", { state: "asleep" });
        const picture = await session.use("look", {});
        const wrong = await session.use("set_power", { state: "sideways" });
        return JSON.stringify({ power, picture, wrong });
      },
    ],
  });
  const turn = newTurn("conversation");
  const { text } = await ask("go to sleep and look", { turn });
  const seen = JSON.parse(text);
  assert.deepEqual(seen.power, { power: "asleep" });
  assert.deepEqual(seen.picture, {
    textResultForLlm: "This is the glass as it is now.",
    binaryResultsForLlm: [{ type: "image", data: "anVzdCBhIHRlc3Q=", mimeType: "image/jpeg" }],
    resultType: "success",
  });
  assert.match(seen.wrong.error, /The arguments are not right/);
  assert.deepEqual(turn.acted, ["set_power", "look", "set_power"]);
  assert.deepEqual(calls, [{ state: "asleep" }]);
});

test("a conversation keeps its session until it is ended or a new one is asked for", async (t) => {
  const { sdk, brain, ask, sessions } = await startBrain(t);
  await ask("show the clock");
  await ask("make it bigger");
  assert.equal(sessions().length, 1);
  assert.deepEqual(sessions()[0].prompts, ["show the clock", "make it bigger"]);
  await ask("something else entirely", { fresh: true });
  assert.equal(sessions().length, 2);
  await until(() => sdk.deleted.includes("session-2"), "the old session to be deleted");
  assert.equal(sessions()[0].disconnected, true);
  await brain.endConversation();
  assert.equal(sessions()[1].disconnected, true);
  assert.deepEqual(sdk.deleted, ["session-1", "session-2", "session-3"]);
  await ask("hello again");
  assert.equal(sessions().length, 3);
});

test("a proactive run has a session of its own, closed afterwards, beside an open conversation", async (t) => {
  const { sdk, brain, ask, sessions } = await startBrain(t);
  await ask("show the clock");
  await brain.run({ session: "proactive", system: "You are tending.", prompt: "look over the display", tools: [tools[0]], turn: newTurn("tend"), timeoutMs: 30_000 });
  assert.equal(sessions().length, 2);
  assert.equal(sessions()[1].config.systemMessage.content, "You are tending.");
  assert.deepEqual(sessions()[1].config.tools.map((tool) => tool.name), ["set_power"]);
  await until(() => sdk.deleted.includes("session-3"));
  assert.equal(sessions()[0].disconnected, undefined, "the conversation is left open");
  await ask("make it bigger");
  assert.deepEqual(sessions()[0].prompts, ["show the clock", "make it bigger"]);
});

test("a turn that fails before any tool ran is tried once more with a new session", async (t) => {
  const { ask, sessions, events } = await startBrain(t, {
    turns: [
      async () => {
        throw new Error("Session not found");
      },
      async () => "Here I am.",
    ],
  });
  assert.deepEqual(await ask("are you there?"), { text: "Here I am." });
  assert.equal(sessions().length, 2);
  assert.equal(sessions()[0].disconnected, true);
  assert.deepEqual(sessions()[1].prompts, ["are you there?"]);
  assert.ok(events.some((entry) => entry.event === "brain.turn_failed" && entry.attempt === 1));
});

test("a turn that fails twice gives up", async (t) => {
  const fail = async () => {
    throw new Error("the model service answered 500");
  };
  const { ask, sessions } = await startBrain(t, { turns: [fail, fail, async () => "never reached"] });
  await assert.rejects(ask("hello"), (error) => error instanceof BrainError && error.kind === "failed" && /answered 500/.test(error.message));
  assert.equal(sessions().length, 2);
});

test("a turn that fails after a tool has acted is never tried again", async (t) => {
  const { ask, sessions, sdk } = await startBrain(t, {
    turns: [
      async (session) => {
        await session.use("set_power", { state: "asleep" });
        throw new Error("the connection dropped");
      },
      async () => "must not be asked",
    ],
  });
  const turn = newTurn("conversation");
  await assert.rejects(ask("go to sleep", { turn }), (error) => error instanceof BrainError && error.kind === "failed");
  assert.equal(sessions().length, 1);
  assert.deepEqual(calls, [{ state: "asleep" }], "the tool acted once");
  assert.equal(sdk.turns.length, 1, "the second turn was never asked for");
  // The broken session is not used for the next request either.
  await ask("hello");
  assert.equal(sessions().length, 2);
});

test("a turn that takes too long is stopped, and what it calls afterwards does nothing", async (t) => {
  let late;
  const { ask, clock, sessions } = await startBrain(t, {
    turns: [
      (session) => {
        late = session;
        return new Promise(() => {});
      },
    ],
  });
  const pending = ask("think forever", { timeoutMs: 30_000 });
  const refused = assert.rejects(pending, (error) => error instanceof BrainError && error.kind === "timeout");
  await until(() => late !== undefined);
  await clock.advance(30_000);
  await refused;
  assert.equal(sessions()[0].aborted, true, "the model's work is stopped");
  await until(() => sessions()[0].disconnected === true);
  assert.match((await late.use("set_power", { state: "asleep" })).error, /This request is over/);
  assert.deepEqual(calls, []);
});

test("a run can be stopped by its signal", async (t) => {
  const { brain, sessions } = await startBrain(t, { turns: [() => new Promise(() => {})] });
  const controller = new AbortController();
  const pending = brain.run({
    session: "proactive", system: "", prompt: "tend", tools, turn: newTurn("tend"), timeoutMs: 30_000, signal: controller.signal,
  });
  const refused = assert.rejects(pending, (error) => error instanceof BrainError && error.kind === "aborted");
  await until(() => sessions().length === 1 && sessions()[0].prompts.length === 1);
  controller.abort();
  await refused;
  assert.equal(sessions()[0].aborted, true);
});

test("a Copilot runtime that died is noticed at the next request and started again", async (t) => {
  const { sdk, ask, sessions, brain } = await startBrain(t);
  await ask("hello");
  sdk.clients[0].alive = false;
  assert.deepEqual(await ask("are you still there?"), { text: "Done." });
  assert.equal(sdk.clients.length, 2);
  assert.equal(brain.health().ready, true);
  assert.deepEqual(sessions().at(-1).prompts, ["are you still there?"]);
});

test("a Copilot runtime that died while nobody asked is found by the minute's check", async (t) => {
  const { sdk, clock, brain, events } = await startBrain(t);
  await clock.advance(60_000);
  assert.equal(sdk.clients.length, 1);
  sdk.clients[0].alive = false;
  await clock.advance(60_000);
  assert.ok(events.some((entry) => entry.event === "brain.lost"));
  assert.equal(sdk.clients.length, 2);
  assert.deepEqual(brain.health(), { ready: true, detail: "" });
});

test("a session asked for ahead is the one the turn uses", async (t) => {
  const { brain, ask, sessions } = await startBrain(t);
  brain.prepare({ fresh: true, system: "You are the mirror.", tools });
  brain.prepare({ fresh: true, system: "You are the mirror.", tools });
  await ask("good morning", { fresh: true });
  assert.equal(sessions().length, 1, "one session, although a new conversation was asked for three times");
  brain.prepare({ fresh: false, system: "You are the mirror.", tools });
  await ask("and the weather?");
  assert.equal(sessions().length, 1);
  brain.prepare({ fresh: true, system: "You are the mirror.", tools });
  await ask("a new subject", { fresh: true });
  assert.equal(sessions().length, 2);
});

test("stopping closes the conversation and the client", async (t) => {
  const { sdk, brain, ask, sessions } = await startBrain(t);
  await ask("hello");
  await brain.stop();
  assert.equal(sessions()[0].disconnected, true);
  assert.equal(sdk.clients[0].alive, false);
  assert.deepEqual(brain.health(), { ready: false, detail: "Stopped." });
});

test("only the Copilot harness exists so far", () => {
  assert.throws(() => createBrain({ harness: "other", model: "x", workingDirectory: "." }), /no agent harness called "other"/);
});
