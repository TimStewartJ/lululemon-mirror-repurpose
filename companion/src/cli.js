#!/usr/bin/env node
import fs from "node:fs";
import { parseArgs } from "node:util";
import { defaultConfigPath, freshConfig, loadConfig, updateConfigFile, writeConfig } from "./config.js";
import { describeError } from "./log.js";
import { pairWithMirror } from "./mirror.js";

const USAGE = `Mirror companion

Usage: node src/cli.js <command>

  init                          Write a config with a fresh secret. Does not overwrite one.
  pair --host H --code 123456   Pair with the mirror and store its token. Optional: --name, --port.
  serve                         Run the companion.
  health                        Ask a running companion how it is.
  ask "text"                    Send typed words to a running companion.
  say-wav FILE                  Send a recording to a running companion. Optional: --addressed name|window|follow-up.
  secret                        Print the secret, to give it to the mirror.

The config is read from ${defaultConfigPath()}
(set MIRROR_COMPANION_CONFIG to use another file).`;

const commands = {
  init() {
    const file = defaultConfigPath();
    try {
      writeConfig(file, freshConfig());
    } catch (error) {
      if (error.code === "EEXIST") {
        throw new Error(`A config already exists at ${file}. It was left as it is.`);
      }
      throw error;
    }
    print(`A config with a fresh secret was written to ${file}.`);
    print("Next, pair with the mirror: node src/cli.js pair --host MIRROR_ADDRESS --code CODE");
    print("Then give the mirror the secret that this prints: node src/cli.js secret");
  },

  async pair(argv) {
    const { values } = parseArgs({
      args: argv,
      options: {
        host: { type: "string" },
        code: { type: "string" },
        name: { type: "string", default: "Mirror companion" },
        port: { type: "string", default: "8787" },
      },
    });
    if (!values.host || !values.code) {
      throw new Error("Give the mirror's address and the code it shows: pair --host MIRROR_ADDRESS --code 123456");
    }
    const port = Number(values.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("--port must be a number from 1 to 65535.");
    const config = loadConfig();
    const token = await pairWithMirror({ host: values.host, port, code: values.code, name: values.name });
    updateConfigFile(config.path, (raw) => {
      raw.mirror = { ...raw.mirror, host: values.host, port, token };
    });
    print(`Paired with the mirror at ${values.host} as "${values.name}". The token is stored in ${config.path}.`);
    print("If the companion is running, restart it so that it uses the new token.");
  },

  async serve() {
    const { serve } = await import("./serve.js");
    const running = await serve(loadConfig());
    const stop = (signal) => {
      // A second signal ends the process without waiting.
      process.once(signal, () => process.exit(1));
      running.stop().then(() => process.exit(0));
    };
    process.once("SIGINT", () => stop("SIGINT"));
    process.once("SIGTERM", () => stop("SIGTERM"));
  },

  async health() {
    const report = await request("GET", "/v1/health");
    const part = (name, ready, detail) => print(`${name}: ${ready ? "ready" : "not ready"}${detail ? `. ${detail}` : ""}`);
    print(`${report.name} ${report.version} is ${report.ok ? "well" : "not ready"}. It has been up for ${report.uptimeSeconds} seconds.`);
    part(`Model ${report.model}`, report.brain.ready, report.brain.detail);
    part(`Speech-to-text ${report.stt.model} on ${report.stt.device || "an unknown device"}`, report.stt.ready, report.stt.detail);
    print(
      `Mirror: ${report.mirror.reachable ? `reachable, Mirror Home ${report.mirror.version || "of unknown version"}` : "not reachable"}` +
        `${report.mirror.detail ? `. ${report.mirror.detail}` : ""}`,
    );
    if (report.last) print(`Last heard: "${report.last.heard}". Answered: "${report.last.reply}" in ${report.last.ms} ms.`);
    if (!report.ok) process.exitCode = 1;
  },

  async ask(argv) {
    const text = argv.join(" ").trim();
    if (!text) throw new Error("Give the words to send: ask \"what is on my list?\"");
    printAnswer(await request("POST", "/v1/ask", { json: { text, source: "test" } }));
  },

  async "say-wav"(argv) {
    const { values, positionals } = parseArgs({
      args: argv,
      options: { addressed: { type: "string", default: "name" } },
      allowPositionals: true,
    });
    if (positionals.length !== 1) throw new Error("Give one WAV file: say-wav FILE [--addressed name|window|follow-up]");
    let wav;
    try {
      wav = fs.readFileSync(positionals[0]);
    } catch (error) {
      throw new Error(`The file ${positionals[0]} could not be read: ${error.message}`);
    }
    const answer = await request("POST", "/v1/utterance", {
      body: wav,
      headers: {
        "Content-Type": "audio/wav",
        "X-Mirror-Addressed": values.addressed,
        "X-Mirror-Utterance": `cli-${Date.now().toString(36)}`,
      },
    });
    printAnswer(answer);
  },

  secret() {
    print(loadConfig().secret);
  },
};

/** Sends a request to the companion that runs on this machine. */
async function request(method, path, { json, body, headers = {} } = {}) {
  const config = loadConfig();
  const host = ["0.0.0.0", "::"].includes(config.listen.host) ? "127.0.0.1" : config.listen.host;
  const url = `http://${host}:${config.listen.port}${path}`;
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${config.secret}`,
        ...(json ? { "Content-Type": "application/json" } : {}),
        ...headers,
      },
      body: json ? JSON.stringify(json) : body,
      signal: AbortSignal.timeout(50_000),
    });
  } catch (error) {
    throw new Error(`No companion answers at ${url} (${error.cause?.code || error.message}). Start it with: node src/cli.js serve`);
  }
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`The companion answered ${response.status}: ${answer.error || "no reason given"}`);
  return answer;
}

function printAnswer(answer) {
  if (answer.heard) print(`Heard: ${answer.heard}`);
  if (answer.ignored) print(`Ignored (${answer.reason}).`);
  else print(`Reply: ${answer.reply || "(nothing to show)"}`);
  if (answer.listen) print("The mirror would now listen for an answer.");
  if (answer.acted.length > 0) print(`Tools: ${answer.acted.join(", ")}`);
  print(`Time: ${answer.ms.total} ms in all, ${answer.ms.stt} ms hearing, ${answer.ms.agent} ms thinking.`);
  if (answer.error) print(`Error: ${answer.error}`);
}

function print(line) {
  process.stdout.write(line + "\n");
}

// Piped into a command that stops reading, such as head, there is nobody left to write to.
process.stdout.on("error", (error) => {
  if (error.code === "EPIPE") process.exit(0);
  throw error;
});

const [name, ...rest] = process.argv.slice(2);
const command = Object.hasOwn(commands, name ?? "") ? commands[name] : null;
if (!command) {
  print(USAGE);
  process.exitCode = name === undefined || name === "help" || name === "--help" ? 0 : 2;
} else {
  try {
    await command(rest);
  } catch (error) {
    process.stderr.write(`${describeError(error)}\n`);
    process.exitCode = 1;
  }
}
