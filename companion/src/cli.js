#!/usr/bin/env node
import fs from "node:fs";
import { parseArgs } from "node:util";
import { SHORTCUTS } from "./briefing.js";
import { defaultAuthPath, defaultConfigPath, freshConfig, freshKey, loadConfig, updateConfigFile, writeConfig } from "./config.js";
import { describeError } from "./log.js";
import { pairWithMirror } from "./mirror.js";

const USAGE = `Mirror companion

Usage: node src/cli.js <command>

  init                          Write a config with a fresh secret. Does not overwrite one.
  pair --host H --code 123456   Pair with the mirror and store its token. Optional: --name, --port.
  providers                     List where a model can come from, and which of them are signed in.
  models [PROVIDER]             List the models a sign-in is offered. Without a name: the provider in the config.
  login [PROVIDER] [--key]      Sign in to a provider and keep the sign-in. With --key a key is asked for, where
                                the provider also has a sign-in in the browser.
  logout [PROVIDER]             Forget a sign-in that "login" kept.
  serve [--tools-only]          Run the companion. With --tools-only it serves the mirror's tools over MCP and
                                nothing else: no model, no speech-to-text, nothing done unasked.
  mcp-key [--new]               Print the key that MCP clients on the network send. Makes one if there is none,
                                which switches MCP on; --new replaces it.
  mcp                           Serve the mirror's tools to one MCP client on standard input and output. A client
                                starts this itself; no companion has to run.
  health                        Ask a running companion how it is.
  ask "text"                    Send typed words to a running companion. With --shortcut NAME they are sent as a
                                greeting the mirror recognised itself: ${SHORTCUTS.join(", ")}.
  say-wav FILE                  Send a recording to a running companion. Optional: --addressed name|window|follow-up.
  secret                        Print the secret, to give it to the mirror.

The config is read from ${defaultConfigPath()}
(set MIRROR_COMPANION_CONFIG to use another file). Sign-ins are kept beside it, in auth.json.`;

/** The provider a command is about: the one named, or the one in the config, with its endpoint if it has one. */
function chosenProvider(named) {
  const config = loadConfig();
  const provider = named ?? config.provider;
  if (!provider) throw new Error('No provider is chosen in the config yet. Name one, as in "models anthropic"; "node src/cli.js providers" lists them.');
  return { provider, model: config.model, endpoint: provider === config.provider ? config.endpoint : null, authFile: defaultAuthPath(), config };
}

/** Asks in the terminal what a sign-in needs to know, and shows what it has to say. */
async function inTheTerminal() {
  const readline = await import("node:readline");
  const ask = (question, { hidden = false } = {}) =>
    new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: process.stdin.isTTY === true });
      let answered = false;
      // What is typed for a key is not shown.
      if (hidden && process.stdin.isTTY) lines._writeToOutput = (text) => (text.includes(question) ? process.stdout.write(text) : undefined);
      lines.question(question, (answer) => {
        answered = true;
        lines.close();
        if (hidden && process.stdin.isTTY) process.stdout.write("\n");
        resolve(answer.trim());
      });
      lines.on("close", () => answered || reject(new Error("Nothing was entered.")));
    });
  return {
    async prompt(wanted) {
      if (wanted.type === "select") {
        wanted.options.forEach((option, at) => print(`  ${at + 1}  ${option.label}${option.description ? `: ${option.description}` : ""}`));
        const chosen = wanted.options[Number(await ask(`${wanted.message} (1 to ${wanted.options.length}): `)) - 1];
        if (!chosen) throw new Error("That is not one of the choices.");
        return chosen.id;
      }
      const hint = wanted.placeholder ? ` (${wanted.placeholder})` : "";
      return ask(`${wanted.message}${hint}: `, { hidden: wanted.type === "secret" });
    },
    notify(event) {
      if (event.type === "device_code") print(`Open ${event.verificationUri} in a browser, on any machine, and enter the code ${event.userCode}. Waiting for that.`);
      else if (event.type === "auth_url") print(`Open this address in a browser${event.instructions ? ` (${event.instructions})` : ""}:\n${event.url}`);
      else if (event.message) print(event.message);
      for (const link of event.links ?? []) print(`${link.label ?? "More"}: ${link.url}`);
    },
  };
}

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
    print('And choose a model: "node src/cli.js providers" lists where one can come from; "provider" and "model" in the config say which.');
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

  async providers() {
    const { COPILOT_CLI, findCopilotCliSignIn, piModels } = await import("./providers.js");
    let configured = null;
    try {
      configured = loadConfig();
    } catch {
      // Without a config the list is as useful.
    }
    const models = piModels({ authFile: defaultAuthPath() });
    const copilotCli = findCopilotCliSignIn();
    const rows = [
      { id: COPILOT_CLI, name: "GitHub Copilot, with the sign-in of its CLI", how: "the Copilot CLI", state: copilotCli.token ? `signed in: ${copilotCli.source}` : "not signed in" },
    ];
    for (const provider of models.getProviders()) {
      const how = [provider.auth.oauth ? "login (browser)" : "", provider.auth.apiKey?.login ? "login --key" : "", "environment"].filter(Boolean).join(", ");
      const auth = await models.checkAuth(provider.id).catch(() => undefined);
      rows.push({ id: provider.id, name: provider.name, how, state: auth ? `signed in: ${auth.source ?? auth.type}` : "not signed in" });
    }
    rows.sort((a, b) => a.id.localeCompare(b.id));
    if (configured?.endpoint) rows.push({ id: configured.provider, name: `the endpoint ${configured.endpoint.baseUrl}`, how: "the config", state: "" });
    const wide = (key) => Math.max(...rows.map((row) => row[key].length));
    print(`Where a model can come from ("provider" in the config). ${configured?.provider ? "The one in use is marked." : "None is chosen yet."}`);
    for (const row of rows) {
      print(`${row.id === configured?.provider ? "*" : " "} ${row.id.padEnd(wide("id"))}  ${row.name.padEnd(wide("name"))}  ${row.state}${row.state.startsWith("not") ? `  (sign in with: ${row.how})` : ""}`.trimEnd());
    }
    print('A server of your own (Ollama, LM Studio, vLLM): any other name, with "endpoint" in the config.');
  },

  async models(argv) {
    const { openModels } = await import("./providers.js");
    const chosen = chosenProvider(argv[0]);
    const { models, providerId, signIn, close } = openModels(chosen);
    try {
      if (!(await models.getAuth(providerId))) throw new Error(`Nobody is signed in to ${chosen.provider}. ${signIn}`.trim());
      await models.refresh({ providers: [providerId] });
      const offered = await models.getAvailable(providerId);
      if (offered.length === 0) throw new Error(`The sign-in to ${chosen.provider} is offered no model.`);
      print(`The models of ${chosen.provider} that this sign-in is offered ("model" in the config):`);
      const wide = Math.max(...offered.map((model) => model.id.length));
      for (const model of offered) {
        const notes = [model.reasoning ? "thinks" : "", model.input.includes("image") ? "sees pictures" : ""].filter(Boolean).join(", ");
        print(`${chosen.provider === chosen.config.provider && model.id === chosen.config.model ? "*" : " "} ${model.id.padEnd(wide)}  ${notes}`.trimEnd());
      }
    } finally {
      close();
    }
  },

  async login(argv) {
    const { values, positionals } = parseArgs({ args: argv, options: { key: { type: "boolean", default: false } }, allowPositionals: true });
    const { COPILOT_CLI, openModels } = await import("./providers.js");
    const chosen = chosenProvider(positionals[0]);
    if (chosen.provider === COPILOT_CLI) {
      throw new Error(
        `"${COPILOT_CLI}" uses the sign-in the Copilot CLI has: sign in there ("copilot", then "/login"), or put a token in COPILOT_GITHUB_TOKEN. ` +
          'To sign in here instead, set "provider" to "github-copilot" and run: login github-copilot',
      );
    }
    const { models, providerId } = openModels(chosen);
    const provider = models.getProvider(providerId);
    const type = provider.auth.oauth && !values.key ? "oauth" : "api_key";
    if (type === "api_key" && !provider.auth.apiKey?.login) {
      throw new Error(`${provider.name} takes its credentials from the environment the companion runs in; there is nothing to sign in to here.`);
    }
    await models.login(providerId, type, await inTheTerminal());
    print(`Signed in to ${provider.name}. The sign-in is kept in ${chosen.authFile}, which only you can read.`);
    print("A running companion that was waiting for it finds it by itself within five minutes; restarting it is quicker.");
  },

  async logout(argv) {
    const { COPILOT_CLI, openModels } = await import("./providers.js");
    const chosen = chosenProvider(argv[0]);
    if (chosen.provider === COPILOT_CLI) throw new Error(`"${COPILOT_CLI}" uses the sign-in the Copilot CLI has, which this command does not touch. Sign out in the Copilot CLI.`);
    const { models, providerId } = openModels(chosen);
    await models.logout(providerId);
    print(`The sign-in to ${chosen.provider} is forgotten.`);
  },

  async serve(argv) {
    const { values } = parseArgs({ args: argv, options: { "tools-only": { type: "boolean", default: false } } });
    const { serve } = await import("./serve.js");
    const running = await serve(loadConfig(), { toolsOnly: values["tools-only"] });
    const stop = (signal) => {
      // A second signal ends the process without waiting.
      process.once(signal, () => process.exit(1));
      running.stop().then(() => process.exit(0));
    };
    process.once("SIGINT", () => stop("SIGINT"));
    process.once("SIGTERM", () => stop("SIGTERM"));
  },

  async mcp() {
    const { serveStdio } = await import("./serve.js");
    await serveStdio(loadConfig());
  },

  "mcp-key"(argv) {
    const { values } = parseArgs({ args: argv, options: { new: { type: "boolean", default: false } } });
    const config = loadConfig();
    const made = values.new || !config.mcp.key;
    const key = made ? freshKey() : config.mcp.key;
    if (made) {
      updateConfigFile(config.path, (raw) => {
        raw.mcp = { ...raw.mcp, key };
      });
    }
    // The key alone on standard output, so that a script can take it; the rest is for the person.
    print(key);
    const note = (line) => process.stderr.write(line + "\n");
    note(`MCP clients on the network connect to http://THIS_MACHINE:${config.listen.port}/mcp and send this key as a bearer token.`);
    if (made) note(`The key is new and is stored in ${config.path}. If the companion is running, restart it so that it takes the key.`);
  },

  async health() {
    const report = await request("GET", "/v1/health");
    const part = (name, ready, detail) => print(`${name}: ${ready ? "ready" : "not ready"}${detail ? `. ${detail}` : ""}`);
    print(`${report.name} ${report.version} is ${report.ok ? "well" : "not ready"}. It has been up for ${report.uptimeSeconds} seconds.`);
    if (report.mcp?.toolsOnly) {
      print("It serves its tools only: no model and no speech-to-text run.");
    } else {
      part(report.model ? `Model ${report.model}${report.provider ? ` of ${report.provider}` : ""}` : "Model", report.brain.ready, report.brain.detail);
      part(`Speech-to-text ${report.stt.model} on ${report.stt.device || "an unknown device"}`, report.stt.ready, report.stt.detail);
    }
    print(
      `Mirror: ${report.mirror.reachable ? `reachable, Mirror Home ${report.mirror.version || "of unknown version"}` : "not reachable"}` +
        `${report.mirror.detail ? `. ${report.mirror.detail}` : ""}`,
    );
    if (report.mcp) print(report.mcp.on ? `MCP: on, ${report.mcp.calls} tool calls since the start.` : 'MCP: off. "mcp-key" switches it on.');
    if (report.last) print(`Last heard: "${report.last.heard}". Answered: "${report.last.reply}" in ${report.last.ms} ms.`);
    if (!report.ok) process.exitCode = 1;
  },

  async ask(argv) {
    // The words are taken as they come, so that a dash among them is not read as an option.
    const words = [...argv];
    let shortcut;
    const at = words.findIndex((word) => word === "--shortcut" || word.startsWith("--shortcut="));
    if (at >= 0) {
      const [option, name] = words.splice(at, words[at].includes("=") ? 1 : 2);
      shortcut = option.includes("=") ? option.slice(option.indexOf("=") + 1) : name;
      if (!SHORTCUTS.includes(shortcut)) throw new Error(`--shortcut must be one of: ${SHORTCUTS.join(", ")}.`);
    }
    const text = words.join(" ").trim();
    if (!text) throw new Error("Give the words to send: ask \"what is on my list?\"");
    const json = shortcut ? { text, source: "shortcut", shortcut } : { text, source: "test" };
    printAnswer(await request("POST", "/v1/ask", { json }));
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
  // The rows of a card as the glass sets them: the label in capitals beside its text.
  const rows = answer.details ?? [];
  const width = Math.max(0, ...rows.map((row) => row.label.length));
  for (const row of rows) print(`  ${row.label.toUpperCase().padEnd(width)}  ${row.text}`);
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
