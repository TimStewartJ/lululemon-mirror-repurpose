import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { z } from "zod";
import { BrainError, createBrain } from "../src/brain.js";
import { COPILOT_CLI, copilotCliProvider, credentialFile, endpointProvider, findCopilotCliSignIn, openModels } from "../src/providers.js";
import { responsesOverSocket } from "../src/responses-socket.js";
import { newTurn } from "../src/tools.js";
import { until } from "./fakes/clock.js";
import { startResponsesServer } from "./fakes/responses-server.js";
import { parseConfig } from "../src/config.js";
import { createModels } from "@earendil-works/pi-ai";

function folder(t) {
  const made = fs.mkdtempSync(path.join(os.tmpdir(), "companion-providers-"));
  t.after(() => fs.rmSync(made, { recursive: true, force: true }));
  return made;
}

/** A home folder in which the Copilot CLI has left its config. */
function homeWithCopilotCli(t, config, { comments = true } = {}) {
  const home = folder(t);
  fs.mkdirSync(path.join(home, ".copilot"));
  const text = (comments ? "// User settings belong in settings.json.\n// This file is managed automatically.\n" : "") + JSON.stringify(config, null, 2);
  fs.writeFileSync(path.join(home, ".copilot", "config.json"), text);
  return home;
}

const question = { systemPrompt: "You are a test.", messages: [{ role: "user", content: "Are you ready?", timestamp: 1 }] };
const wordsOf = (message) => message.content.filter((block) => block.type === "text").map((block) => block.text).join("");

test("sign-ins are kept in a file only its owner can read, one entry for each provider", async (t) => {
  const file = path.join(folder(t), "kept", "auth.json");
  const store = credentialFile(file);
  assert.equal(await store.read("anthropic"), undefined);
  assert.deepEqual(await store.list(), []);
  assert.deepEqual(await store.modify("anthropic", async (current) => ({ type: "api_key", key: `was ${current}` })), { type: "api_key", key: "was undefined" });
  await store.modify("github-copilot", async () => ({ type: "oauth", refresh: "r", access: "a", expires: 5 }));
  assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf8")), {
    anthropic: { type: "api_key", key: "was undefined" },
    "github-copilot": { type: "oauth", refresh: "r", access: "a", expires: 5 },
  });
  assert.deepEqual(await store.list(), [{ providerId: "anthropic", type: "api_key" }, { providerId: "github-copilot", type: "oauth" }]);
  if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  // A change that decides to leave things as they are writes nothing.
  assert.deepEqual(await store.modify("anthropic", async () => undefined), { type: "api_key", key: "was undefined" });
  await store.delete("anthropic");
  assert.deepEqual(Object.keys(JSON.parse(fs.readFileSync(file, "utf8"))), ["github-copilot"]);
  assert.equal(fs.existsSync(`${file}.lock`), false);
});

test("a sign-in written by another process while one is being renewed is not lost", async (t) => {
  const file = path.join(folder(t), "auth.json");
  const store = credentialFile(file);
  await store.modify("github-copilot", async () => ({ type: "oauth", refresh: "r", access: "old", expires: 1 }));
  const order = [];
  const renewing = store.modify("github-copilot", async (current) => {
    order.push("renewing");
    // Meanwhile a "login" in a terminal writes another provider's entry.
    fs.writeFileSync(file, JSON.stringify({ ...JSON.parse(fs.readFileSync(file, "utf8")), openai: { type: "api_key", key: "k" } }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    return { ...current, access: "new" };
  });
  const second = store.modify("anthropic", async () => {
    order.push("second");
    return { type: "api_key", key: "a" };
  });
  await Promise.all([renewing, second]);
  assert.deepEqual(order, ["renewing", "second"], "changes take their turn");
  assert.deepEqual(Object.keys(JSON.parse(fs.readFileSync(file, "utf8"))).sort(), ["anthropic", "github-copilot", "openai"]);
  assert.equal((await store.read("github-copilot")).access, "new");
});

test("a lock left behind by a process that died does not stop the next change", async (t) => {
  const file = path.join(folder(t), "auth.json");
  fs.writeFileSync(`${file}.lock`, "");
  const longAgo = new Date(Date.now() - 60_000);
  fs.utimesSync(`${file}.lock`, longAgo, longAgo);
  await credentialFile(file).modify("openai", async () => ({ type: "api_key", key: "k" }));
  assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf8")), { openai: { type: "api_key", key: "k" } });
});

test("a file of sign-ins that is not JSON is reported, not overwritten", async (t) => {
  const file = path.join(folder(t), "auth.json");
  fs.writeFileSync(file, "{ not json");
  await assert.rejects(credentialFile(file).modify("openai", async () => ({ type: "api_key", key: "k" })), /are not valid JSON/);
  assert.equal(fs.readFileSync(file, "utf8"), "{ not json");
});

test("the Copilot CLI's sign-in is found in its config, under its comment lines", (t) => {
  const home = homeWithCopilotCli(t, {
    authTokens: {
      "https://github.com:first-user": { token: "gho_first" },
      "https://github.com:second-user:github": { token: "gho_second_github" },
      "https://github.com:second-user": { token: "gho_second" },
    },
    lastLoggedInUser: { host: "https://github.com", login: "second-user" },
  });
  const file = path.join(home, ".copilot", "config.json");
  assert.deepEqual(findCopilotCliSignIn({ env: {}, home }), { token: "gho_second", host: "github.com", source: `the Copilot CLI's sign-in (${file})` });
  // The token the CLI itself would take first.
  assert.deepEqual(findCopilotCliSignIn({ env: { COPILOT_GITHUB_TOKEN: "github_pat_x" }, home }), { token: "github_pat_x", host: "github.com", source: "COPILOT_GITHUB_TOKEN" });
  // Another folder for the CLI's files, as the CLI takes it.
  const elsewhere = homeWithCopilotCli(t, { authTokens: { "https://example.ghe.com:someone": "gho_plain" } }, { comments: false });
  assert.deepEqual(findCopilotCliSignIn({ env: { COPILOT_HOME: path.join(elsewhere, ".copilot") }, home }).token, "gho_plain");
  assert.equal(findCopilotCliSignIn({ env: {}, home: elsewhere }).host, "example.ghe.com");
});

test("why no sign-in of the Copilot CLI was found is said", (t) => {
  const empty = folder(t);
  assert.match(findCopilotCliSignIn({ env: {}, home: empty }).missing, /The Copilot CLI has no config at .*config\.json\./);
  const keychain = homeWithCopilotCli(t, { loggedInUsers: [{ host: "https://github.com", login: "someone" }] });
  assert.match(findCopilotCliSignIn({ env: {}, home: keychain }).missing, /is signed in, but keeps the sign-in in this machine's keychain/);
  const nobody = homeWithCopilotCli(t, { trustedFolders: [] });
  assert.match(findCopilotCliSignIn({ env: {}, home: nobody }).missing, /Nobody is signed in to the Copilot CLI/);
  const broken = folder(t);
  fs.mkdirSync(path.join(broken, ".copilot"));
  fs.writeFileSync(path.join(broken, ".copilot", "config.json"), "{ not json");
  assert.match(findCopilotCliSignIn({ env: {}, home: broken }).missing, /could not be read/);
});

test("with the Copilot CLI's sign-in, GitHub is asked where the account's models are and which it is offered", async (t) => {
  const server = await startResponsesServer();
  t.after(() => server.close());
  const home = homeWithCopilotCli(t, { authTokens: { "https://github.com:someone": { token: "gho_from_the_cli" } } });
  let now = 1_000_000;
  const { provider, close } = copilotCliProvider({ env: {}, home, fetch: server.fetch, userAgent: "mirror-companion/9.9.9", now: () => now });
  t.after(close);
  const models = createModels();
  models.setProvider(provider);

  const auth = await models.getAuth("github-copilot");
  assert.deepEqual(auth.auth, { apiKey: "gho_from_the_cli", baseUrl: server.url });
  assert.match(auth.source, /the Copilot CLI's sign-in/);
  assert.deepEqual(server.lookups.map((lookup) => [lookup.path, lookup.headers.authorization]), [
    ["/copilot_internal/user", "token gho_from_the_cli"],
    ["/models", "Bearer gho_from_the_cli"],
  ]);
  // The requests say whose sign-in they come with, and that it is the companion that sends them.
  assert.equal(server.lookups[1].headers["copilot-integration-id"], "copilot-developer-cli");
  assert.equal(server.lookups[1].headers["user-agent"], "mirror-companion/9.9.9");
  const model = models.getModel("github-copilot", "gpt-6-luna");
  assert.deepEqual(model.headers, { "User-Agent": "mirror-companion/9.9.9", "Copilot-Integration-Id": "copilot-developer-cli" });
  assert.equal(model.api, "openai-responses");

  // Of Pi's Copilot models, those the account can pick and has not switched off.
  assert.deepEqual((await models.getAvailable("github-copilot")).map((offered) => offered.id).sort(), ["gpt-5-mini", "gpt-6-luna"]);
  assert.equal(server.lookups.length, 2, "what GitHub said is kept");
  now += 31 * 60_000;
  await models.getAuth("github-copilot");
  await until(() => server.lookups.length === 4, "the account to be looked up again after half an hour");
});

test("a sign-in GitHub does not accept is reported in words that say what to do", async (t) => {
  const server = await startResponsesServer();
  t.after(() => server.close());
  server.refuseLookups = 401;
  const { provider } = copilotCliProvider({ env: { COPILOT_GITHUB_TOKEN: "gho_stale" }, home: folder(t), fetch: server.fetch });
  const models = createModels();
  models.setProvider(provider);
  await assert.rejects(models.getAuth("github-copilot"), (error) => /GitHub answered 401 to the Copilot CLI's sign-in.*Sign in to the Copilot CLI again\./.test(error.cause.message));
  // With no sign-in at all the provider is simply not configured.
  const none = createModels();
  none.setProvider(copilotCliProvider({ env: {}, home: folder(t), fetch: server.fetch }).provider);
  assert.equal(await none.getAuth("github-copilot"), undefined);
});

test("a model that can be asked over a socket is, on one socket for as long as the requests are alike", async (t) => {
  const server = await startResponsesServer();
  t.after(() => server.close());
  const events = [];
  const { provider, close } = copilotCliProvider({ env: { COPILOT_GITHUB_TOKEN: "gho_token" }, home: folder(t), fetch: server.fetch, log: (event, fields) => events.push({ event, ...fields }) });
  t.after(close);
  const models = createModels();
  models.setProvider(provider);
  const model = models.getModel("github-copilot", "gpt-6-luna");

  server.answers.push("Ready.", "Still ready.");
  assert.equal(wordsOf(await models.completeSimple(model, question, { reasoning: "low" })), "Ready.");
  const second = await models.completeSimple(model, question, { reasoning: "low" });
  assert.equal(wordsOf(second), "Still ready.");
  assert.deepEqual(second.usage.input + second.usage.cacheRead, 120);
  assert.deepEqual(server.requests.map((request) => request.over), ["socket", "socket"]);
  assert.equal(server.socketsOpened, 1);
  const { headers, body } = server.requests[0];
  assert.equal(headers.authorization, "Bearer gho_token");
  assert.equal(headers["copilot-integration-id"], "copilot-developer-cli");
  assert.equal(headers["x-initiator"], "user");
  assert.equal(body.type, "response.create");
  assert.equal(body.model, "gpt-6-luna");
  assert.equal("stream" in body, false);
  assert.deepEqual(body.reasoning.effort, "low");

  // A request that is not begun by a person says so in a header, and so goes over a socket of its own.
  const followUp = {
    systemPrompt: "You are a test.",
    messages: [...question.messages, second, { role: "toolResult", toolCallId: "call_1", toolName: "look", content: [{ type: "text", text: "{}" }], isError: false, timestamp: 2 }],
  };
  await models.completeSimple(model, followUp, { reasoning: "low" });
  assert.equal(server.requests[2].headers["x-initiator"], "agent");
  assert.equal(server.socketsOpened, 2);
  // A model that is not said to take a socket is asked in the plain way.
  await models.completeSimple(models.getModel("github-copilot", "gpt-5-mini"), question);
  assert.equal(server.requests[3].over, "http");
  assert.deepEqual(events, []);
});

test("what the service refuses over the socket comes back as its refusal over HTTP would", async (t) => {
  const server = await startResponsesServer();
  t.after(() => server.close());
  const { provider, close } = copilotCliProvider({ env: { COPILOT_GITHUB_TOKEN: "gho_token" }, home: folder(t), fetch: server.fetch });
  t.after(close);
  const models = createModels();
  models.setProvider(provider);
  server.answers.push({ error: "The requested model is not supported.", code: "model_not_supported" });
  const refused = await models.completeSimple(models.getModel("github-copilot", "gpt-6-luna"), question);
  assert.equal(refused.stopReason, "error");
  assert.match(refused.errorMessage, /The requested model is not supported/);
  assert.equal(server.requests.length, 1, "a refusal is not asked for a second time over HTTP");
});

test("when no socket can be had the plain request is used, and for a while no socket is tried", async (t) => {
  const server = await startResponsesServer({ sockets: false });
  t.after(() => server.close());
  const events = [];
  const { provider, close } = copilotCliProvider({ env: { COPILOT_GITHUB_TOKEN: "gho_token" }, home: folder(t), fetch: server.fetch, log: (event, fields) => events.push({ event, ...fields }) });
  t.after(close);
  const models = createModels();
  models.setProvider(provider);
  const model = models.getModel("github-copilot", "gpt-6-luna");
  assert.equal(wordsOf(await models.completeSimple(model, question)), "Done.");
  assert.equal(wordsOf(await models.completeSimple(model, question)), "Done.");
  assert.deepEqual(server.requests.map((request) => request.over), ["http", "http"]);
  assert.equal(events.length, 1);
  assert.equal(events[0].event, "brain.socket_failed");
  assert.equal(events[0].plainRequestsForMs, 60_000);
});

test("MIRROR_COMPANION_NO_WEBSOCKET keeps to plain requests", async (t) => {
  const server = await startResponsesServer();
  t.after(() => server.close());
  const { provider } = copilotCliProvider({ env: { COPILOT_GITHUB_TOKEN: "gho_token", MIRROR_COMPANION_NO_WEBSOCKET: "1" }, home: folder(t), fetch: server.fetch });
  const models = createModels();
  models.setProvider(provider);
  assert.equal(wordsOf(await models.completeSimple(models.getModel("github-copilot", "gpt-6-luna"), question)), "Done.");
  assert.deepEqual([server.requests[0].over, server.socketsOpened], ["http", 0]);
});

test("a socket that takes a request and says nothing is given up, and the request is asked the plain way", async (t) => {
  const server = await startResponsesServer();
  t.after(() => server.close());
  server.silent = true;
  const events = [];
  const sockets = responsesOverSocket({ firstWithinMs: 150, log: (event, fields) => events.push({ event, ...fields }) });
  t.after(() => sockets.close());
  const body = JSON.stringify({ model: "gpt-6-luna", input: [], stream: true });
  const response = await sockets.fetch(`${server.url}/responses`, { method: "POST", headers: { Authorization: "Bearer k", "Content-Type": "application/json" }, body });
  assert.equal(response.status, 200);
  assert.match(await response.text(), /response\.completed/);
  assert.deepEqual(server.requests.map((request) => request.over), ["socket", "http"]);
  assert.match(events[0].detail, /brought no answer within 150 ms/);
  await until(() => server.live.size === 0, "the silent socket to be closed");
});

test("a request that is stopped in the middle of its answer ends as stopped, and its socket is closed", async (t) => {
  const server = await startResponsesServer();
  t.after(() => server.close());
  const sockets = responsesOverSocket();
  t.after(() => sockets.close());
  server.holdAfterFirstEvent = true;
  const controller = new AbortController();
  const request = { method: "POST", headers: { Authorization: "Bearer k" }, body: JSON.stringify({ model: "gpt-6-luna", input: [], stream: true }), signal: controller.signal };
  const response = await sockets.fetch(`${server.url}/responses`, request);
  const reading = response.text();
  controller.abort();
  await assert.rejects(reading, (error) => error.name === "AbortError");
  await until(() => server.live.size === 0, "the socket to be closed");
  // What is not a streamed request for an answer is left to the plain request.
  const plain = await sockets.fetch(`${server.url}/models`, { method: "GET" });
  assert.equal(plain.status, 200);
  assert.equal(server.socketsOpened, 1);
});

test("a socket the service closes in the middle of an answer fails that answer; one closed while unused is not used again", async (t) => {
  const server = await startResponsesServer();
  t.after(() => server.close());
  const sockets = responsesOverSocket();
  t.after(() => sockets.close());
  const request = () => ({ method: "POST", headers: { Authorization: "Bearer k" }, body: JSON.stringify({ model: "gpt-6-luna", input: [], stream: true }) });
  assert.match(await (await sockets.fetch(`${server.url}/responses`, request())).text(), /response\.completed/);
  for (const ws of server.live) ws.close();
  await until(() => server.live.size === 0, "the unused socket to close");
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.match(await (await sockets.fetch(`${server.url}/responses`, request())).text(), /response\.completed/);
  assert.equal(server.socketsOpened, 2);

  server.holdAfterFirstEvent = true;
  const response = await sockets.fetch(`${server.url}/responses`, request());
  const reading = response.text();
  for (const ws of server.live) ws.terminate();
  await assert.rejects(reading, /closed before its answer was complete/);
});

test("a server of one's own is one model at an address, with a key from the environment or none", async (t) => {
  const server = await startResponsesServer();
  t.after(() => server.close());
  const endpoint = parseConfig({ secret: "x".repeat(16), provider: "ollama", model: "llama3.2", endpoint: { baseUrl: server.url } }).endpoint;
  const authFile = path.join(folder(t), "auth.json");
  const opened = openModels({ provider: "ollama", model: "llama3.2", endpoint, authFile, env: {} });
  assert.equal(opened.providerId, "ollama");
  const model = opened.models.getModel("ollama", "llama3.2");
  assert.deepEqual([model.api, model.baseUrl, model.input, model.reasoning], ["openai-completions", server.url, ["text"], false]);
  server.answers.push("Ready, from a server of your own.");
  assert.equal(wordsOf(await opened.models.completeSimple(model, question)), "Ready, from a server of your own.");
  const { headers, body } = server.requests[0];
  assert.equal(headers.authorization, "Bearer none");
  assert.deepEqual(body.messages.map((message) => message.role), ["system", "user"], "the instructions go as a system message, which such servers know");

  // With a key named in the config, the server is signed in to only when the key is there.
  const keyed = { ...endpoint, apiKeyEnv: "MY_SERVER_KEY" };
  const without = openModels({ provider: "my-server", model: "m", endpoint: keyed, authFile, env: {} });
  assert.equal(await without.models.getAuth("my-server"), undefined);
  assert.equal(without.signIn, "Put the key in MY_SERVER_KEY, or store one with: node src/cli.js login my-server");
  const withKey = openModels({ provider: "my-server", model: "m", endpoint: keyed, authFile, env: { MY_SERVER_KEY: "secret-key" } });
  assert.deepEqual(await withKey.models.getAuth("my-server"), { auth: { apiKey: "secret-key" }, source: "MY_SERVER_KEY" });
  // A key stored with "login" comes before the environment.
  await credentialFile(authFile).modify("my-server", async () => ({ type: "api_key", key: "stored-key" }));
  assert.equal((await withKey.models.getAuth("my-server")).auth.apiKey, "stored-key");
});

test("an endpoint can speak another API and say what its model can do", () => {
  const endpoint = parseConfig({
    secret: "x".repeat(16),
    endpoint: { baseUrl: "http://localhost:8000/v1", api: "openai-responses", images: true, reasoning: true, contextWindow: 200_000, maxTokens: 8000, compat: { supportsDeveloperRole: false } },
  }).endpoint;
  const model = endpointProvider("vllm", "qwen", endpoint).getModels()[0];
  assert.deepEqual(
    { api: model.api, input: model.input, reasoning: model.reasoning, contextWindow: model.contextWindow, maxTokens: model.maxTokens, compat: model.compat },
    { api: "openai-responses", input: ["text", "image"], reasoning: true, contextWindow: 200_000, maxTokens: 8000, compat: { supportsDeveloperRole: false } },
  );
});

test("the provider in the config is one of Pi's, the Copilot CLI's sign-in, or a server of one's own", (t) => {
  const authFile = path.join(folder(t), "auth.json");
  const home = folder(t);
  const anthropic = openModels({ provider: "anthropic", model: "claude-haiku-4-5", authFile, env: {}, home });
  assert.equal(anthropic.providerId, "anthropic");
  assert.equal(anthropic.signIn, "Sign in with: node src/cli.js login anthropic");
  assert.match(openModels({ provider: "groq", model: "x", authFile, env: {}, home }).signIn, /^Store a key with: node src\/cli\.js login groq/);

  const copilot = openModels({ provider: COPILOT_CLI, model: "gpt-6-luna", authFile, env: {}, home });
  t.after(copilot.close);
  assert.equal(copilot.providerId, "github-copilot");
  assert.match(copilot.signIn, /^The Copilot CLI has no config at .* Either sign in with the Copilot CLI as this user .* COPILOT_GITHUB_TOKEN.* node src\/cli\.js login github-copilot$/);

  assert.throws(() => openModels({ provider: "nowhere", model: "x", authFile, env: {}, home }), /There is no provider called "nowhere"\. Set "provider" in the config to "copilot-cli" or to one of: .*anthropic.*openrouter/);
  const endpoint = parseConfig({ secret: "x".repeat(16), endpoint: { baseUrl: "http://localhost:11434/v1" } }).endpoint;
  assert.throws(() => openModels({ provider: "openai", model: "x", endpoint, authFile, env: {}, home }), /"endpoint" is for a server Pi has no provider for, and "openai" is one of Pi's/);
  assert.throws(() => openModels({ provider: COPILOT_CLI, model: "x", endpoint, authFile, env: {}, home }), /Give it another name than "copilot-cli"/);
});

test("one of Pi's providers takes its key from the environment or from the file of sign-ins", async (t) => {
  const authFile = path.join(folder(t), "auth.json");
  const home = folder(t);
  assert.equal(await openModels({ provider: "anthropic", model: "x", authFile, env: {}, home }).models.getAuth("anthropic"), undefined);
  const fromEnv = await openModels({ provider: "anthropic", model: "x", authFile, env: { ANTHROPIC_API_KEY: "sk-ant-env" }, home }).models.getAuth("anthropic");
  assert.deepEqual([fromEnv.auth.apiKey, fromEnv.source], ["sk-ant-env", "ANTHROPIC_API_KEY"]);
  await credentialFile(authFile).modify("anthropic", async () => ({ type: "api_key", key: "sk-ant-stored" }));
  const stored = await openModels({ provider: "anthropic", model: "x", authFile, env: { ANTHROPIC_API_KEY: "sk-ant-env" }, home }).models.getAuth("anthropic");
  assert.equal(stored.auth.apiKey, "sk-ant-stored");
});

test("from the config to the answer: the brain on Pi, the Copilot CLI's sign-in, a tool call and the socket", async (t) => {
  const server = await startResponsesServer();
  t.after(() => server.close());
  const events = [];
  const acted = [];
  const tools = [
    {
      name: "set_power",
      description: "Puts the display to sleep or wakes it.",
      schema: z.object({ state: z.enum(["asleep", "awake"]), note: z.string().optional() }),
      changes: true,
      handler: async (args) => {
        acted.push(args);
        return { power: args.state };
      },
    },
  ];
  const brain = createBrain({
    provider: COPILOT_CLI,
    model: "gpt-6-luna",
    reasoningEffort: "low",
    userAgent: "mirror-companion/9.9.9",
    log: (event, fields) => events.push({ event, ...fields }),
    openModels: (options) => openModels({ ...options, env: { COPILOT_GITHUB_TOKEN: "gho_token" }, home: os.tmpdir(), fetch: server.fetch }),
  });
  t.after(() => brain.stop());
  server.answers.push("ready", { call: ["set_power", { state: "asleep" }] }, "Good night.");
  await brain.start();
  assert.deepEqual(brain.health(), { ready: true, detail: "" });
  assert.equal(events.find((entry) => entry.event === "brain.ready").signIn, "COPILOT_GITHUB_TOKEN");

  const turn = newTurn("conversation");
  const answer = await brain.run({ session: "conversation", system: "You are the mirror.", prompt: "go to sleep", tools, turn, timeoutMs: 20_000 });
  assert.deepEqual(answer, { text: "Good night." });
  assert.deepEqual(acted, [{ state: "asleep" }]);
  assert.deepEqual(turn.acted, ["set_power"]);
  assert.deepEqual(server.requests.map((request) => request.over), ["socket", "socket", "socket"]);

  const [, asked, afterTheTool] = server.requests.map((request) => request.body);
  // The tools are declared as not strict, so that an argument that may be left out is left out.
  assert.deepEqual(asked.tools.map((tool) => [tool.type, tool.name, tool.strict]), [["function", "set_power", false]]);
  assert.deepEqual(asked.tools[0].parameters.required, ["state"]);
  // No summary of the thinking is asked for: nobody reads it, and it costs time.
  assert.deepEqual(asked.reasoning, { effort: "low" });
  assert.equal(asked.store, false);
  assert.ok(JSON.stringify(asked.input).includes("You are the mirror."));
  // The tool's answer goes back with the call it belongs to.
  const output = afterTheTool.input.find((item) => item.type === "function_call_output");
  assert.equal(output.call_id, "call_resp_2");
  assert.equal(output.output, '{"power":"asleep"}');
  const lines = events.filter((entry) => entry.event === "brain.model_call");
  assert.deepEqual(lines.map((line) => [line.inputTokens, line.cachedTokens, line.outputTokens]), [[120, 100, 7], [120, 100, 7]]);

  // A refusal by the service is what the turn fails with, after one more try.
  server.answers.push({ error: "The requested model is not supported." }, { error: "The requested model is not supported." });
  await assert.rejects(
    brain.run({ session: "conversation", fresh: true, system: "You are the mirror.", prompt: "hello", tools, turn: newTurn("conversation"), timeoutMs: 20_000 }),
    (error) => error instanceof BrainError && error.kind === "failed" && /The requested model is not supported/.test(error.message),
  );
  await brain.stop();
  await until(() => server.live.size === 0, "the sockets to be closed when the brain stops");
});
