import fs from "node:fs";
import path from "node:path";
import { createActivity } from "./activity.js";
import { createAssistant } from "./assistant.js";
import { createBrain } from "./brain.js";
import { createBriefingMemory } from "./briefing.js";
import { systemClock } from "./clock.js";
import { defaultAuthPath, readableByOthers } from "./config.js";
import { createHabits } from "./habits.js";
import { createHealth } from "./health.js";
import { createLog, describeError } from "./log.js";
import { createMcp, serveLines } from "./mcp.js";
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
 * With `toolsOnly` it serves the mirror's tools to programs on the network and nothing else: no model is
 * started, no speech-to-text, and it does nothing unasked.
 *
 * @param {import("./config.js").Config} config
 * @param {Object} [parts]
 * @param {boolean} [parts.toolsOnly]
 * @returns {Promise<{ server: import("node:http").Server, port: number, stop: () => Promise<void>, proactive: object, habits: import("./habits.js").Habits }>}
 */
export async function serve(config, parts = {}) {
  const clock = parts.clock ?? systemClock;
  const log = parts.log ?? createLog({ secrets: [config.secret, config.mirror.token, config.mcp.key] });
  const version = companionVersion();
  const toolsOnly = parts.toolsOnly === true;
  if (toolsOnly && !config.mcp.key) {
    throw new Error("To serve the tools only, programs need a key to come with. Make one with: node src/cli.js mcp-key");
  }

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
  const brain =
    parts.brain ??
    createBrain({
      provider: config.provider,
      model: config.model,
      reasoningEffort: config.reasoningEffort,
      endpoint: config.endpoint,
      authFile: defaultAuthPath(),
      userAgent: `mirror-companion/${version}`,
      log,
      clock,
    });
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
  // The same tools for programs on the network, which wait in the same queue as people do.
  const mcp = config.mcp.key ? createMcp({ tools, mirror, queue, clock, version, log, activity }) : null;
  const health = createHealth({ version, provider: config.provider, model: config.model, brain, stt, mirror, queue, activity, clock, mcp, toolsOnly });
  const server = createServer({ secret: config.secret, assistant, proactive, activity, queue, health, log, clock, mcp, mcpKey: config.mcp.key });

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
  log("listening", { host: config.listen.host, port, version, provider: config.provider, model: config.model, mcp: Boolean(mcp), toolsOnly });

  if (toolsOnly) {
    // Nothing looks at the mirror now and then in this mode, so one look says whether it is there.
    mirror.get("/api/v1/status").catch(() => {});
  } else {
    // The port answers at once; the model and the speech worker come up behind it
    // and the health report says how far they are.
    stt.start();
    brain.start().catch((error) => log("brain.start_failed", { detail: describeError(error) }));
    proactive.start();
  }

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

/**
 * Runs the mirror's tools for one MCP client on standard input and output,
 * which is how a client talks to a server it starts itself. It goes straight
 * to the mirror: no companion has to run, and no model or speech-to-text is
 * needed. What it changes does not wait behind a request that a companion is
 * serving at that moment.
 *
 * @param {import("./config.js").Config} config
 * @param {Object} [parts]
 * @returns {Promise<void>} settles when the client has closed its end
 */
export async function serveStdio(config, parts = {}) {
  if (!config.mirror.host || !config.mirror.token) {
    throw new Error("Not paired with a mirror yet. Run: node src/cli.js pair --host MIRROR_ADDRESS --code CODE");
  }
  const clock = parts.clock ?? systemClock;
  // Standard output carries the protocol, so the log goes to standard error.
  const log = parts.log ?? createLog({ write: (line) => process.stderr.write(line + "\n"), secrets: [config.secret, config.mirror.token, config.mcp.key] });
  const version = companionVersion();
  const mirror = parts.mirror ?? createMirror({ ...config.mirror, log });
  const tools = createTools({ mirror, memory: createMemory(config.stateDir), clock, log });
  const mcp = createMcp({ tools, mirror, queue: createQueue(), clock, version, log });
  log("mcp.stdio", { version, mirror: config.mirror.host });
  try {
    await serveLines(mcp, { input: parts.input, output: parts.output });
  } finally {
    mirror.close();
  }
}

function companionVersion() {
  return JSON.parse(fs.readFileSync(new URL("../package.json", import.meta.url), "utf8")).version;
}
