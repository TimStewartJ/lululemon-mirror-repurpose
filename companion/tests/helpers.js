import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { parseConfig } from "../src/config.js";
import { createLog } from "../src/log.js";
import { createMemory } from "../src/memory.js";
import { createMirror } from "../src/mirror.js";
import { serve } from "../src/serve.js";
import { createTools, newTurn, runTool } from "../src/tools.js";
import { scriptedBrain } from "./fakes/brain.js";
import { fakeClock } from "./fakes/clock.js";
import { startFakeMirror } from "./fakes/mirror.js";
import { fakeStt } from "./fakes/stt.js";

export const SECRET = "test-secret-0123456789";

export function temporaryDirectory(t) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "companion-test-"));
  t.after(() => fs.rmSync(folder, { recursive: true, force: true }));
  return folder;
}

/**
 * A whole companion on a free port, with a fake mirror, a scripted brain, a
 * fake speech-to-text and a clock that the test moves.
 *
 * @param {import("node:test").TestContext} t
 * @param {object[]} [script] The scripted brain's turns.
 * @param {object} [proactive] Proactive settings; everything is off unless given.
 */
export async function startCompanion(t, script = [], proactive = {}) {
  const clock = fakeClock();
  const mirror = await startFakeMirror({ now: clock.now });
  const lines = [];
  const log = createLog({ write: (line) => lines.push(line), clock, secrets: [SECRET, mirror.token] });
  const config = parseConfig({
    secret: SECRET,
    mirror: { host: mirror.host, port: mirror.port, token: mirror.token },
    proactive: { greet: false, reminders: false, tend: false, ...proactive },
    stateDir: temporaryDirectory(t),
  });
  config.listen = { host: "127.0.0.1", port: 0 };
  const brain = scriptedBrain(clock, script);
  const stt = fakeStt(clock);
  const running = await serve(config, { clock, log, brain, stt });
  t.after(async () => {
    await running.stop();
    await mirror.close();
  });
  const base = `http://127.0.0.1:${running.port}`;

  /** Sends a request to the companion and returns { status, body }. */
  async function call(method, route, { body, headers = {}, auth = `Bearer ${SECRET}` } = {}) {
    const response = await fetch(base + route, { method, body, headers: { ...(auth ? { Authorization: auth } : {}), ...headers } });
    const text = await response.text();
    return { status: response.status, body: text ? JSON.parse(text) : null };
  }

  return {
    clock,
    mirror,
    brain,
    stt,
    lines,
    config,
    running,
    call,
    /** Says something to the mirror: posts a recording of the words. */
    say: (wav, { addressed = "name", id = "utt-1" } = {}) =>
      call("POST", "/v1/utterance", {
        body: wav,
        headers: { "Content-Type": "audio/wav", "X-Mirror-Addressed": addressed, "X-Mirror-Utterance": id },
      }),
    ask: (text, source = "controls") =>
      call("POST", "/v1/ask", { body: JSON.stringify({ text, source }), headers: { "Content-Type": "application/json" } }),
    /** The log lines so far, parsed. */
    events: () => lines.map((line) => JSON.parse(line)),
  };
}

/**
 * The tools against a fake mirror, without a server around them.
 *
 * @param {import("node:test").TestContext} t
 */
export async function startTools(t) {
  const clock = fakeClock();
  const fake = await startFakeMirror({ now: clock.now });
  const mirror = createMirror({ host: fake.host, port: fake.port, token: fake.token });
  const memory = createMemory(temporaryDirectory(t));
  t.after(async () => {
    mirror.close();
    await fake.close();
  });
  const tools = createTools({ mirror, memory, clock, log: () => {} });
  /** Runs one tool as a model in a conversation would. */
  async function use(name, args = {}, turn = newTurn("conversation")) {
    const tool = tools.find((candidate) => candidate.name === name);
    return runTool(tool, args, turn);
  }
  return { clock, fake, mirror, memory, tools, use };
}
