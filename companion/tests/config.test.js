import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { defaultAuthPath, defaultPython, expandHome, freshConfig, loadConfig, parseConfig, updateConfigFile, writeConfig } from "../src/config.js";
import { temporaryDirectory } from "./helpers.js";

test("a config with only a secret gets every default", () => {
  const config = parseConfig({ secret: "test-secret-0123456789" });
  assert.deepEqual(config.listen, { host: "0.0.0.0", port: 8790 });
  assert.deepEqual(config.mirror, { host: "", port: 8787, token: "" });
  // Nobody's model is chosen for anyone.
  assert.equal(config.provider, "");
  assert.equal(config.endpoint, null);
  assert.equal(config.model, "");
  assert.equal(config.reasoningEffort, "low");
  assert.equal(config.stt.model, "small.en");
  assert.equal(config.stt.device, "auto");
  assert.deepEqual(config.proactive, {
    greet: true, morningBriefing: true, reminders: true, tend: true, tendMinutes: 60, quietHours: ["22:30", "06:30"],
  });
  assert.equal(config.keepUtterances, 20);
});

test("the state directory and the Python path are worked out from the home directory", () => {
  const config = parseConfig({ secret: "test-secret-0123456789" });
  assert.equal(config.stateDir, path.resolve(expandHome("~/.local/state/mirror-companion")));
  assert.equal(config.stt.python, defaultPython(config.stateDir));
  const own = parseConfig({ secret: "test-secret-0123456789", stt: { python: "~/venv/bin/python" } });
  assert.equal(own.stt.python, expandHome("~/venv/bin/python"));
});

test("what is given is kept", () => {
  const config = parseConfig({
    secret: "test-secret-0123456789",
    listen: { host: "127.0.0.1", port: 28790 },
    mirror: { host: "192.0.2.10", port: 8787, token: "test-mirror-token" },
    model: "another-model",
    stt: { model: "base.en", device: "cpu" },
    proactive: { tend: false, quietHours: null },
    keepUtterances: 0,
  });
  assert.equal(config.listen.port, 28790);
  assert.equal(config.mirror.host, "192.0.2.10");
  assert.equal(config.model, "another-model");
  assert.equal(config.stt.device, "cpu");
  assert.equal(config.proactive.tend, false);
  assert.equal(config.proactive.greet, true);
  assert.equal(config.proactive.morningBriefing, true);
  assert.equal(config.proactive.quietHours, null);
  assert.equal(parseConfig({ secret: "test-secret-0123456789", proactive: { morningBriefing: false } }).proactive.morningBriefing, false);
  assert.equal(config.keepUtterances, 0);
});

test("the example config in the folder is a usable one", () => {
  const example = JSON.parse(fs.readFileSync(new URL("../config.example.json", import.meta.url), "utf8"));
  const config = parseConfig(example);
  assert.equal(config.listen.port, 8790);
  assert.equal(config.reasoningEffort, "low");
  assert.equal(config.stt.python, expandHome("~/.local/state/mirror-companion/venv/bin/python"));
  // It names every setting there is, the same ones init writes.
  assert.deepEqual(Object.keys(example.proactive), Object.keys(freshConfig().proactive));
  assert.deepEqual(Object.keys(config.proactive), Object.keys(example.proactive));
});

test("a mistake is named with where it is", () => {
  assert.throws(() => parseConfig({}), /secret/);
  assert.throws(() => parseConfig({ secret: "short" }), /secret: must be at least 16 characters/);
  assert.throws(() => parseConfig({ secret: "test-secret-0123456789", listen: { port: 70000 } }), /listen\.port/);
  assert.throws(() => parseConfig({ secret: "test-secret-0123456789", stt: { device: "gpu" } }), /stt\.device/);
  assert.throws(
    () => parseConfig({ secret: "test-secret-0123456789", proactive: { quietHours: ["22:30", "6.30"] } }),
    /proactive\.quietHours\.1: must be a time such as 22:30/,
  );
  assert.throws(() => parseConfig({ secret: "test-secret-0123456789", reasoningEffort: "hard" }), /reasoningEffort/);
  assert.throws(() => parseConfig({ secret: "test-secret-0123456789", endpoint: { baseUrl: "localhost" } }), /endpoint\.baseUrl: must be an address such as http:\/\/localhost:11434\/v1/);
  assert.throws(() => parseConfig({ secret: "test-secret-0123456789", endpoint: { baseUrl: "http://localhost:1234/v1", api: "grpc" } }), /endpoint\.api/);
});

test("whose model answers is a provider by its name, or a server of one's own", () => {
  const hosted = parseConfig({ secret: "test-secret-0123456789", provider: "anthropic", model: "claude-haiku-4-5", reasoningEffort: "minimal" });
  assert.deepEqual([hosted.provider, hosted.model, hosted.reasoningEffort, hosted.endpoint], ["anthropic", "claude-haiku-4-5", "minimal", null]);
  const own = parseConfig({ secret: "test-secret-0123456789", provider: "ollama", model: "llama3.2", endpoint: { baseUrl: "http://localhost:11434/v1" } });
  assert.deepEqual(own.endpoint, {
    baseUrl: "http://localhost:11434/v1", api: "openai-completions", apiKeyEnv: "", images: false, reasoning: false, contextWindow: 32_768, maxTokens: 4096, compat: {},
  });
  assert.equal(defaultAuthPath({ MIRROR_COMPANION_CONFIG: path.join("somewhere", "config.json") }), path.join("somewhere", "auth.json"));
  assert.equal(defaultAuthPath({ MIRROR_COMPANION_AUTH: "elsewhere.json" }), "elsewhere.json");
});

test("a missing or broken file says what to do", (t) => {
  const folder = temporaryDirectory(t);
  assert.throws(() => loadConfig(path.join(folder, "none.json")), /There is no config at .*none\.json\. Create one with: node src\/cli\.js init/);
  const broken = path.join(folder, "broken.json");
  fs.writeFileSync(broken, "{ not json");
  assert.throws(() => loadConfig(broken), /is not valid JSON/);
});

test("init's config is complete, loads, and is not overwritten", (t) => {
  const file = path.join(temporaryDirectory(t), "sub", "config.json");
  const fresh = freshConfig();
  assert.ok(fresh.secret.length >= 32);
  assert.notEqual(fresh.secret, freshConfig().secret);
  writeConfig(file, fresh);
  assert.equal(loadConfig(file).secret, fresh.secret);
  if (process.platform !== "win32") assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  assert.throws(() => writeConfig(file, freshConfig()), { code: "EEXIST" });
  assert.equal(loadConfig(file).secret, fresh.secret);
});

test("pairing changes the mirror's entry and nothing else", (t) => {
  const file = path.join(temporaryDirectory(t), "config.json");
  const fresh = { ...freshConfig(), model: "kept-model" };
  writeConfig(file, fresh);
  updateConfigFile(file, (raw) => {
    raw.mirror = { ...raw.mirror, host: "192.0.2.10", token: "test-mirror-token" };
  });
  const loaded = loadConfig(file);
  assert.deepEqual(loaded.mirror, { host: "192.0.2.10", port: 8787, token: "test-mirror-token" });
  assert.equal(loaded.model, "kept-model");
  assert.equal(loaded.secret, fresh.secret);
});
