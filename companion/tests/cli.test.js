import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { speech } from "./fakes/stt.js";
import { SECRET, startCompanion, temporaryDirectory } from "./helpers.js";

const CLI = fileURLToPath(new URL("../src/cli.js", import.meta.url));

/** Runs the command line with its config in the given file. Its sign-ins are kept beside that file. */
function cli(config, ...args) {
  return cliWith({ config }, ...args);
}

/** The same with words to type, or an environment of its own. */
function cliWith({ config, typed, env = {} }, ...args) {
  return new Promise((resolve) => {
    // No sign-in of the machine the tests run on is to be found: no token, and a home without the Copilot CLI.
    const clean = { ...process.env, MIRROR_COMPANION_CONFIG: config, COPILOT_GITHUB_TOKEN: "", COPILOT_HOME: path.join(path.dirname(config), "no-copilot-cli"), ...env };
    delete clean.MIRROR_COMPANION_AUTH;
    const child = execFile(process.execPath, [CLI, ...args], { env: clean }, (error, stdout, stderr) => {
      resolve({ code: error ? error.code : 0, stdout, stderr });
    });
    child.stdin.end(typed ?? "");
  });
}

test("providers lists where a model can come from, and login keeps a key that models and logout then use", async (t) => {
  const folder = temporaryDirectory(t);
  const file = path.join(folder, "config.json");
  await cli(file, "init");
  const listed = await cli(file, "providers");
  assert.equal(listed.code, 0, listed.stderr);
  // A fresh config names nobody's model, and the list marks none.
  assert.match(listed.stdout, /^Where a model can come from \("provider" in the config\)\. None is chosen yet\.$/m);
  assert.doesNotMatch(listed.stdout, /^\* /m);
  assert.match(listed.stdout, /^ {2}copilot-cli +GitHub Copilot, with the sign-in of its CLI +not signed in +\(sign in with: the Copilot CLI\)$/m);
  const ids = [...listed.stdout.matchAll(/^ {2}(\S+) /gm)].map((match) => match[1]);
  assert.deepEqual(ids, [...ids].sort((a, b) => a.localeCompare(b)), "the providers stand in the order of their names, none first");
  assert.match(listed.stdout, /^ {2}anthropic +Anthropic +not signed in +\(sign in with: login \(browser\), login --key, environment\)$/m);
  assert.match(listed.stdout, /^ {2}github-copilot +GitHub Copilot +not signed in/m);
  assert.match(listed.stdout, /A server of your own .*"endpoint" in the config/);

  // With no provider in the config, a command that is about one asks for its name.
  for (const command of ["models", "login", "logout"]) {
    const unnamed = await cli(file, command);
    assert.equal(unnamed.code, 1);
    assert.match(unnamed.stderr, /No provider is chosen in the config yet\. Name one/);
  }
  // A provider nobody is signed in to: the commands say what to do.
  const none = await cli(file, "models", "copilot-cli");
  assert.equal(none.code, 1);
  assert.match(none.stderr, /Nobody is signed in to copilot-cli\. The Copilot CLI has no config at .* Either sign in with the Copilot CLI/);
  const elsewhere = await cli(file, "login", "copilot-cli");
  assert.match(elsewhere.stderr, /"copilot-cli" uses the sign-in the Copilot CLI has: sign in there/);
  assert.match((await cli(file, "login", "nowhere")).stderr, /There is no provider called "nowhere"/);

  const signedIn = await cliWith({ config: file, typed: "a-key-for-the-test-0123456789\n" }, "login", "groq");
  assert.equal(signedIn.code, 0, signedIn.stderr);
  assert.match(signedIn.stdout, /Signed in to Groq\. The sign-in is kept in .*auth\.json, which only you can read\./);
  assert.ok(!signedIn.stdout.includes("a-key-for-the-test"), "the key is not printed");
  const authFile = path.join(folder, "auth.json");
  assert.deepEqual(JSON.parse(fs.readFileSync(authFile, "utf8")), { groq: { type: "api_key", key: "a-key-for-the-test-0123456789" } });
  if (process.platform !== "win32") assert.equal(fs.statSync(authFile).mode & 0o777, 0o600);
  assert.match((await cli(file, "providers")).stdout, /^ {2}groq +Groq +signed in: stored credential$/m);

  const models = await cli(file, "models", "groq");
  assert.equal(models.code, 0, models.stderr);
  assert.match(models.stdout, /The models of groq that this sign-in is offered \("model" in the config\):\n {2}\S+/);

  const out = await cli(file, "logout", "groq");
  assert.match(out.stdout, /The sign-in to groq is forgotten\./);
  assert.deepEqual(JSON.parse(fs.readFileSync(authFile, "utf8")), {});
  assert.match((await cli(file, "logout", "copilot-cli")).stderr, /Sign out in the Copilot CLI\./);
});

test("a provider that is signed in to through the environment is shown as such, and marked when it is the one in use", async (t) => {
  const file = path.join(temporaryDirectory(t), "config.json");
  await cli(file, "init");
  const raw = JSON.parse(fs.readFileSync(file, "utf8"));
  fs.writeFileSync(file, JSON.stringify({ ...raw, provider: "anthropic", model: "claude-haiku-4-5" }));
  const listed = await cliWith({ config: file, env: { ANTHROPIC_API_KEY: "sk-ant-a-key-for-the-test" } }, "providers");
  assert.match(listed.stdout, /^\* anthropic +Anthropic +signed in: ANTHROPIC_API_KEY$/m);
  assert.match(listed.stdout, /^ {2}copilot-cli /m);
  const models = await cliWith({ config: file, env: { ANTHROPIC_API_KEY: "sk-ant-a-key-for-the-test" } }, "models");
  assert.match(models.stdout, /^\* claude-haiku-4-5 +thinks, sees pictures$/m);
});

test("init writes a config with a fresh secret and does not overwrite one", async (t) => {
  const file = path.join(temporaryDirectory(t), "config.json");
  const first = await cli(file, "init");
  assert.equal(first.code, 0);
  assert.match(first.stdout, /A config with a fresh secret was written to .*config\.json\./);
  const written = JSON.parse(fs.readFileSync(file, "utf8"));
  assert.ok(written.secret.length >= 32);
  assert.ok(!first.stdout.includes(written.secret), "init does not print the secret");
  assert.deepEqual(Object.keys(written), ["listen", "secret", "mirror", "provider", "model", "reasoningEffort", "stt", "proactive", "keepUtterances", "stateDir"]);
  assert.deepEqual([written.provider, written.model], ["", ""]);
  assert.match(first.stdout, /And choose a model: "node src\/cli\.js providers" lists where one can come from/);

  const second = await cli(file, "init");
  assert.equal(second.code, 1);
  assert.match(second.stderr, /A config already exists at .*config\.json\. It was left as it is\./);
  assert.equal(JSON.parse(fs.readFileSync(file, "utf8")).secret, written.secret);

  const secret = await cli(file, "secret");
  assert.equal(secret.stdout, written.secret + "\n");
});

test("pair stores the token it is given and never prints it", async (t) => {
  const file = path.join(temporaryDirectory(t), "config.json");
  await cli(file, "init");
  const asked = [];
  const mirror = http.createServer((request, response) => {
    let text = "";
    request.on("data", (chunk) => (text += chunk));
    request.on("end", () => {
      asked.push({ path: request.url, body: JSON.parse(text) });
      const right = JSON.parse(text).code === "123456";
      response.writeHead(right ? 200 : 401, { "Content-Type": "application/json" });
      response.end(JSON.stringify(right ? { token: "test-token-from-pairing", clientId: "c1" } : { error: "That code is not right. Check the code and try again." }));
    });
  });
  await new Promise((resolve) => mirror.listen(0, "127.0.0.1", resolve));
  t.after(() => mirror.close());
  const port = String(mirror.address().port);

  const wrong = await cli(file, "pair", "--host", "127.0.0.1", "--port", port, "--code", "000000");
  assert.equal(wrong.code, 1);
  assert.match(wrong.stderr, /That code is not right/);

  const paired = await cli(file, "pair", "--host", "127.0.0.1", "--port", port, "--code", "123456");
  assert.equal(paired.code, 0, paired.stderr);
  assert.match(paired.stdout, /Paired with the mirror at 127\.0\.0\.1 as "Mirror companion"\. The token is stored in/);
  assert.ok(!paired.stdout.includes("test-token-from-pairing"));
  // The mirror is given a code and a name and nothing else: a time zone here would change the mirror's clock.
  assert.deepEqual(asked[1], { path: "/api/v1/pair", body: { code: "123456", name: "Mirror companion" } });
  const written = JSON.parse(fs.readFileSync(file, "utf8"));
  assert.deepEqual(written.mirror, { host: "127.0.0.1", port: Number(port), token: "test-token-from-pairing" });

  const incomplete = await cli(file, "pair", "--host", "127.0.0.1");
  assert.match(incomplete.stderr, /Give the mirror's address and the code it shows/);
});

test("without a config the commands say how to make one, and an unknown command shows the usage", async (t) => {
  const file = path.join(temporaryDirectory(t), "config.json");
  const secret = await cli(file, "secret");
  assert.equal(secret.code, 1);
  assert.match(secret.stderr, /There is no config at .*Create one with: node src\/cli\.js init/);
  const unknown = await cli(file, "dance");
  assert.equal(unknown.code, 2);
  assert.match(unknown.stdout, /Usage: node src\/cli\.js <command>/);
  assert.equal((await cli(file)).code, 0);
});

test("health, ask and say-wav talk to the running companion", async (t) => {
  const { running, config } = await startCompanion(t, [
    { text: "It is 7:12." },
    { calls: [{ tool: "set_power", args: { state: "asleep" } }], text: "Good night." },
  ]);
  const folder = temporaryDirectory(t);
  const file = path.join(folder, "config.json");
  fs.writeFileSync(file, JSON.stringify({ secret: SECRET, listen: { host: "0.0.0.0", port: running.port }, stateDir: config.stateDir }));

  const health = await cli(file, "health");
  assert.equal(health.code, 0, health.stderr);
  assert.match(health.stdout, /mirror-companion 0\.1\.0 is well\./);
  assert.match(health.stdout, /Speech-to-text small\.en on cuda: ready/);
  assert.match(health.stdout, /Mirror: reachable, Mirror Home 2\.3\.0/);
  assert.ok(!health.stdout.includes(SECRET));

  const asked = await cli(file, "ask", "what", "time", "is", "it?");
  assert.equal(asked.code, 0, asked.stderr);
  assert.match(asked.stdout, /Heard: what time is it\?\nReply: It is 7:12\.\nTime: \d+ ms in all/);

  const wav = path.join(folder, "sleep.wav");
  fs.writeFileSync(wav, speech("Mirror, go to sleep"));
  const said = await cli(file, "say-wav", wav);
  assert.equal(said.code, 0, said.stderr);
  assert.match(said.stdout, /Heard: go to sleep\nReply: Good night\.\nTools: set_power\n/);

  assert.match((await cli(file, "say-wav", path.join(folder, "missing.wav"))).stderr, /could not be read/);
  assert.match((await cli(file, "ask")).stderr, /Give the words to send/);
});

test("with no companion running the commands say how to start one", async (t) => {
  const file = path.join(temporaryDirectory(t), "config.json");
  fs.writeFileSync(file, JSON.stringify({ secret: SECRET, listen: { host: "127.0.0.1", port: 9 } }));
  const health = await cli(file, "health");
  assert.equal(health.code, 1);
  assert.match(health.stderr, /No companion answers at http:\/\/127\.0\.0\.1:9\/v1\/health .*Start it with: node src\/cli\.js serve/);
});

test("ask sends a shortcut with --shortcut, and the rows of a card are printed under the reply", async (t) => {
  const { running, config, brain } = await startCompanion(t, [
    { calls: [{ tool: "present", args: { headline: "Two things on your list", rows: [{ label: "7:00 AM", text: "Take out the trash" }, { text: "Buy milk" }] } }] },
    { text: "The answer is -5." },
  ]);
  const file = path.join(temporaryDirectory(t), "config.json");
  fs.writeFileSync(file, JSON.stringify({ secret: SECRET, listen: { host: "127.0.0.1", port: running.port }, stateDir: config.stateDir }));

  const morning = await cli(file, "ask", "good", "morning", "--shortcut", "good-morning");
  assert.equal(morning.code, 0, morning.stderr);
  assert.match(
    morning.stdout,
    /^Heard: good morning\nReply: Good morning\n  WEATHER  Overcast, 12\u00B0 now\. High 16\u00B0, rain likely by 11 AM\.\n  TODAY    Nothing on your list\.\nTime: \d+ ms in all, 0 ms hearing, 0 ms thinking\.\n$/,
  );
  const home = await cli(file, "ask", "--shortcut=home", "I'm home");
  assert.match(home.stdout, /^Heard: I'm home\nReply: Welcome home\n  WEATHER  Overcast, 12\u00B0 now\.\n/);
  assert.equal(brain.runs.length, 0, "a shortcut does not reach the model");

  const list = await cli(file, "ask", "what is on my list?");
  assert.match(list.stdout, /Reply: Two things on your list\n  7:00 AM  Take out the trash\n           Buy milk\nTools: present\n/);
  // Words are sent as they are, also when one of them begins with a dash.
  const sum = await cli(file, "ask", "what", "is", "-2", "-3?");
  assert.match(sum.stdout, /^Heard: what is -2 -3\?\nReply: The answer is -5\.\nTime: /);

  const wrong = await cli(file, "ask", "good day", "--shortcut", "good-day");
  assert.equal(wrong.code, 1);
  assert.equal(wrong.stderr, "--shortcut must be one of: good-morning, good-afternoon, good-evening, good-night, home.\n");
  assert.match((await cli(file, "ask", "--shortcut", "home")).stderr, /Give the words to send/);
  assert.match((await cli(file, "help")).stdout, /ask "text" +Send typed words to a running companion\. With --shortcut NAME/);
});
