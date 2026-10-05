import fs from "node:fs";
import path from "node:path";
import { createActivity } from "./activity.js";
import { createAssistant } from "./assistant.js";
import { createBrain } from "./brain.js";
import { createBriefingMemory } from "./briefing.js";
import { systemClock } from "./clock.js";
import { readableByOthers } from "./config.js";
import { createHabits } from "./habits.js";
import { createHealth } from "./health.js";
import { createLog, describeError } from "./log.js";
import { createMemory } from "./memory.js";
import { createMirror } from "./mirror.js";
import { createProactive } from "./proactive.js";
import { createQueue } from "./queue.js";
import { createRecordings } from "./recordings.js";
import { createServer } from "./server.js";
import { createStt } from "./stt.js";
import { createTools } from "./tools.js";

/**
 * Puts the parts together and runs the companion until it is told to stop.
 * Tests pass their own brain, speech-to-text, clock or log in `parts`.
 *
 * @param {import("./config.js").Config} config
 * @param {Object} [parts]
 * @returns {Promise<{ server: import("node:http").Server, port: number, stop: () => Promise<void>, proactive: object, habits: import("./habits.js").Habits }>}
 */
export async function serve(config, parts = {}) {
  const clock = parts.clock ?? systemClock;
  const log = parts.log ?? createLog({ secrets: [config.secret, config.mirror.token] });
  const version = JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url), "utf8")).version;

  fs.mkdirSync(config.stateDir, { recursive: true, mode: 0o700 });
  if (readableByOthers(config.path)) {
    log("config.exposed", { detail: `Other accounts can read ${config.path}. Run: chmod 600 ${config.path}` });
  }

  const mirror = parts.mirror ?? createMirror({ ...config.mirror, log });
  const stt =
    parts.stt ??
    createStt({
      python: config.stt.python,
      model: config.stt.model,
      device: config.stt.device,
      modelsDir: path.join(config.stateDir, "models"),
      log,
      clock,
    });
  const brain = parts.brain ?? createBrain({ model: config.model, reasoningEffort: config.reasoningEffort === "default" ? undefined : config.reasoningEffort, workingDirectory: config.stateDir, log, clock });
  const memory = createMemory(config.stateDir);
  const activity = createActivity(config.stateDir, log);
  const recordings = createRecordings(config.stateDir, config.keepUtterances, log);
  const queue = createQueue();
  // What the companion does unasked can be changed by asking it; a change is written to the config it was started from.
  const habits = createHabits({ settings: config.proactive, file: config.onDisk ? config.path : null, log });
  const tools = createTools({ mirror, memory, clock, log, habits });
  // One memory of the last briefing for both: a card shown unasked can be answered with "dismiss those" as well.
  const briefings = createBriefingMemory(clock);
  const assistant = createAssistant({ brain, stt, mirror, tools, memory, activity, recordings, queue, briefings, log, clock });
  const proactive = createProactive({ settings: habits.settings, brain, mirror, tools, memory, activity, queue, briefings, log, clock });
  habits.onChange(() => proactive.settingsChanged());
  const health = createHealth({ version, model: config.model, brain, stt, mirror, queue, activity, clock });
  const server = createServer({ secret: config.secret, assistant, proactive, activity, queue, health, log, clock });

  await new Promise((resolve, reject) => {
    server.once("error", (error) => {
      reject(
        new Error(
          error.code === "EADDRINUSE"
            ? `Port ${config.listen.port} is already in use. Stop the other program or change listen.port in the config.`
            : `The companion cannot listen on ${config.listen.host}:${config.listen.port}: ${error.message}`,
        ),
      );
    });
    server.listen(config.listen.port, config.listen.host, resolve);
  });
  const port = server.address().port;
  log("listening", { host: config.listen.host, port, version, model: config.model });

  // The port answers at once; the model and the speech worker come up behind it
  // and the health report says how far they are.
  stt.start();
  brain.start().catch((error) => log("brain.start_failed", { detail: describeError(error) }));
  proactive.start();

  let stopping = null;
  return {
    server,
    port,
    proactive,
    habits,
    stop() {
      stopping ??= (async () => {
        log("stopping", {});
        proactive.stop();
        assistant.close();
        server.close();
        server.closeIdleConnections?.();
        await brain.stop().catch(() => {});
        await stt.stop().catch(() => {});
        mirror.close();
        server.closeAllConnections?.();
      })();
      return stopping;
    },
  };
}
