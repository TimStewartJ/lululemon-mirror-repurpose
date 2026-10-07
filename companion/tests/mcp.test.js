import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { PassThrough } from "node:stream";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { createMcp, KEPT_BACK, PROTOCOL_VERSIONS, serveLines } from "../src/mcp.js";
import { tendingMessage } from "../src/prompt.js";
import { createQueue } from "../src/queue.js";
import { until } from "./fakes/clock.js";
import { MCP_KEY, SECRET, startCompanion, startTools, temporaryDirectory } from "./helpers.js";

const CLI = fileURLToPath(new URL("../src/cli.js", import.meta.url));

const NAMES = [
  "get_state", "look", "set_power", "set_brightness", "set_background", "set_character", "set_answer_place", "arrange_widgets",
  "show_moment", "end_moment", "set_clock", "set_display_rules", "set_weather", "set_film_schedule", "set_text_color", "set_name",
  "board_add", "board_update", "board_remove", "say", "ask",
];

/** The MCP server over the tools and a fake mirror, without HTTP around it. */
async function startMcp(t) {
  const parts = await startTools(t);
  const noted = [];
  const lines = [];
  const mcp = createMcp({
    tools: parts.tools,
    mirror: parts.mirror,
    queue: createQueue(),
    clock: parts.clock,
    version: "9.9.9",
    log: (event, fields) => lines.push({ event, ...fields }),
    activity: { add: (entry) => noted.push(entry) },
  });
  let id = 0;
  /** Calls a tool and returns its result as tools/call gives it, with the JSON of its text read. */
  async function use(name, args = {}) {
    const answer = await mcp.answer({ jsonrpc: "2.0", id: ++id, method: "tools/call", params: { name, arguments: args } });
    assert.equal(answer.error, undefined, JSON.stringify(answer.error));
    const text = answer.result.content[0].text;
    // What is not an error and not beside a picture is the tool's answer as JSON.
    const plain = !answer.result.isError && answer.result.content.length === 1;
    return { ...answer.result, text, value: plain ? JSON.parse(text) : null };
  }
  return { ...parts, mcp, use, noted, lines, ask: (method, params) => mcp.answer({ jsonrpc: "2.0", id: ++id, method, params }) };
}

/** Sends one delivery to /mcp of a running companion: { status, headers, body }. */
async function post(base, delivered, { key = MCP_KEY, method = "POST" } = {}) {
  const response = await fetch(`${base}/mcp`, {
    method,
    headers: { ...(key ? { Authorization: `Bearer ${key}` } : {}), "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
    body: method === "POST" ? (typeof delivered === "string" ? delivered : JSON.stringify(delivered)) : undefined,
  });
  const text = await response.text();
  return { status: response.status, headers: response.headers, body: text ? JSON.parse(text) : null };
}

const call = (id, name, args = {}) => ({ jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args } });

test("the network gets the assistant's tools, less the ones that only make sense in a turn of its own", async (t) => {
  const { mcp, tools } = await startMcp(t);
  assert.deepEqual(mcp.tools.map((tool) => tool.name), NAMES);
  // A tool that is added to the assistant reaches the network only when somebody decides that it should.
  for (const tool of tools) {
    const offered = NAMES.includes(tool.name);
    assert.ok(offered || Object.hasOwn(KEPT_BACK, tool.name), `${tool.name} is neither offered nor kept back with a reason`);
  }
  for (const tool of mcp.tools) {
    assert.equal(tool.inputSchema.type, "object", tool.name);
    assert.equal(tool.inputSchema.$schema, undefined, tool.name);
    assert.ok(tool.description.length > 40, tool.name);
    // Nothing in them speaks to the mirror's own assistant, or of a state that comes with each message.
    assert.doesNotMatch(tool.description, /\byour (words|answers?|line|character)\b|\byou answer\b|call(s)? you\b|each message/i, tool.name);
  }
  const hints = Object.fromEntries(mcp.tools.map((tool) => [tool.name, tool.annotations]));
  assert.deepEqual(hints.get_state, { readOnlyHint: true, destructiveHint: false, openWorldHint: false });
  assert.deepEqual(hints.look, { readOnlyHint: true, destructiveHint: false, openWorldHint: false });
  assert.deepEqual(hints.show_moment, { readOnlyHint: false, destructiveHint: false, openWorldHint: false });
  assert.deepEqual(hints.board_remove, { readOnlyHint: false, destructiveHint: true, openWorldHint: false });
  assert.equal(hints.ask.openWorldHint, true);
  // What a moment is made of is said once, for both.
  const moment = tools.find((tool) => tool.name === "show_moment");
  assert.ok(moment.description.includes("beside your line of words") && !moment.outside.includes("your line"));
  assert.ok(moment.outside.includes("countdown (countdownSeconds from now"));
  assert.deepEqual(mcp.tools.find((tool) => tool.name === "set_power").inputSchema, {
    type: "object",
    properties: { state: { type: "string", enum: ["asleep", "awake"] } },
    required: ["state"],
  });
});

test("initialize agrees on a version and says what this glass is like", async (t) => {
  const { ask, lines } = await startMcp(t);
  const hello = await ask("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "a-client", version: "1.2.3" } });
  assert.equal(hello.result.protocolVersion, "2025-06-18");
  assert.deepEqual(hello.result.capabilities, { tools: { listChanged: false } });
  assert.deepEqual(hello.result.serverInfo, { name: "mirror-companion", title: "MIRROR", version: "9.9.9" });
  assert.match(hello.result.instructions, /black is mirror/);
  assert.match(hello.result.instructions, /Read it first with get_state/);
  assert.deepEqual(lines.find((line) => line.event === "mcp.client"), { event: "mcp.client", name: "a-client", version: "1.2.3", protocol: "2025-06-18", via: "mcp" });
  // A version it does not know is answered with the newest it does, and the client decides.
  assert.equal((await ask("initialize", { protocolVersion: "2099-01-01" })).result.protocolVersion, PROTOCOL_VERSIONS[0]);
  assert.equal((await ask("initialize", {})).result.protocolVersion, PROTOCOL_VERSIONS[0]);
});

test("what is not a request gets no answer, and what cannot be answered says why", async (t) => {
  const { mcp, ask } = await startMcp(t);
  assert.equal(await mcp.answer({ jsonrpc: "2.0", method: "notifications/initialized" }), null);
  assert.equal(await mcp.answer({ jsonrpc: "2.0", method: "notifications/cancelled", params: { requestId: 3 } }), null);
  assert.equal(await mcp.answer({ jsonrpc: "2.0", method: "no/such-thing" }), null, "a notification is never answered");
  assert.equal(await mcp.answer({ jsonrpc: "2.0", id: 4, result: {} }), null, "an answer to nothing is let pass");
  assert.deepEqual(await ask("ping"), { jsonrpc: "2.0", id: 1, result: {} });
  assert.deepEqual((await ask("resources/list")).result, { resources: [] });
  assert.deepEqual((await ask("prompts/list")).result, { prompts: [] });
  assert.deepEqual((await ask("sampling/createMessage")).error, { code: -32601, message: 'This server has tools only and does not know "sampling/createMessage".' });
  assert.deepEqual((await ask("tools/call", { name: "remember", arguments: { note: "x" } })).error, {
    code: -32602,
    message: 'There is no tool called "remember". tools/list names the tools.',
  });
  assert.equal((await ask("tools/call", [])).error.code, -32602);
  for (const bad of [null, 5, "ping", [], { id: 1, method: "ping" }, { jsonrpc: "1.0", id: 1, method: "ping" }]) {
    assert.equal((await mcp.answer(bad)).error.code, -32600, JSON.stringify(bad));
  }
  assert.equal((await mcp.answer({ jsonrpc: "2.0", id: 9 })).error.code, -32600);
  // Several in one delivery, as older clients send them.
  const several = await mcp.receive([{ jsonrpc: "2.0", id: "a", method: "ping" }, { jsonrpc: "2.0", method: "notifications/initialized" }, { jsonrpc: "2.0", id: "b", method: "ping" }]);
  assert.deepEqual(several.map((answer) => answer.id), ["a", "b"]);
  assert.equal(await mcp.receive([{ jsonrpc: "2.0", method: "notifications/initialized" }]), null);
  assert.equal((await mcp.receive([])).error.code, -32600);
});

test("a program adds a reminder by the mirror's clock, and the board comes onto the glass", async (t) => {
  const { use, fake, noted, lines, clock } = await startMcp(t);
  assert.equal(fake.widget("board").visible, false);
  const added = await use("board_add", { kind: "reminder", title: "Start the dishwasher", due: "2026-10-03T21:30:00-07:00" });
  assert.equal(added.isError, undefined);
  assert.deepEqual(added.value.added.due, "2026-10-03T21:30:00-07:00", "the due time comes back on the mirror's wall clock");
  assert.equal(added.value.boardNowShown, true);
  assert.equal(fake.widget("board").visible, true);
  const [item] = fake.state.board.items;
  assert.deepEqual([item.kind, item.title, item.due], ["reminder", "Start the dishwasher", Date.UTC(2026, 9, 4, 4, 30)]);
  // A time without its offset is handed back with the mirror's own, which was read for the call.
  const vague = await use("board_add", { kind: "reminder", title: "Later", due: "2026-10-03T22:00:00" });
  assert.equal(vague.isError, true);
  assert.match(vague.text, /due needs its offset from UTC\. The mirror's offset is -07:00\./);
  assert.deepEqual(noted, [{ at: clock.now(), source: "mcp", acted: ["board_add"], ms: 0 }]);
  assert.deepEqual(lines.filter((line) => line.event === "tool").map((line) => [line.tool, line.ok, line.via]), [
    ["board_add", true, "mcp"],
    ["board_add", false, "mcp"],
  ]);
});

test("the mirror's clock is read for a change, kept for the next ones, and read again after a while", async (t) => {
  const { use, fake, clock } = await startMcp(t);
  const reads = () => fake.requests().filter((request) => request.path === "/api/v1/status").length;
  const first = await use("show_moment", { kind: "countdown", countdownSeconds: 300, id: "tea" });
  assert.deepEqual(first.value, { showing: "tea", kind: "countdown", leavesIn: "320 s", runsOutIn: "300 s" });
  assert.equal(fake.state.moments[0].endsAt, clock.now() + 300_000);
  await use("show_moment", { kind: "text", text: "Dinner is ready" });
  assert.equal(reads(), 1, "two changes in a row read the clock once");
  await clock.advance(11_000);
  await use("end_moment", { id: "tea" });
  assert.equal(reads(), 2);
  // A read needs no clock, and a clock that was set is read anew.
  await use("look");
  assert.equal(reads(), 2);
  await use("set_clock", { format: "24-hour" });
  const before = reads();
  await use("show_moment", { kind: "text", text: "Again" });
  assert.ok(reads() > before);
});

test("a refusal comes back as an error that a model can read, and a picture as a picture", async (t) => {
  const { use, fake } = await startMcp(t);
  const both = await use("set_brightness", { level: 100, change: "dimmer" });
  assert.deepEqual([both.isError, both.text], [true, "Give either level or change, not both and not neither."]);
  const wrong = await use("set_power", { state: "on" });
  assert.equal(wrong.isError, true);
  assert.match(wrong.text, /^The arguments are not right\. state: /);
  assert.equal(fake.writes().length, 0);

  const seen = await use("look");
  assert.deepEqual(seen.content.map((part) => part.type), ["text", "image"]);
  assert.equal(seen.content[1].mimeType, "image/jpeg");
  assert.ok(Buffer.from(seen.content[1].data, "base64").length > 100);
  fake.state.automation.sleeping = true;
  const dark = await use("look");
  assert.equal(dark.isError, true);
  assert.match(dark.text, /The display is dark/);

  const state = await use("get_state");
  assert.equal(state.value.display.power, "asleep");
  assert.equal(state.value.now.utcOffset, "-07:00");
});

test("say shows a notice or a small card, and tells when the display is dark", async (t) => {
  const { use, fake, noted } = await startMcp(t);
  assert.deepEqual((await use("say", { text: "The laundry is done.", seconds: 12 })).value, { shown: true });
  assert.deepEqual(fake.state.said.at(-1), { text: "The laundry is done.", kind: "notice", seconds: 12, shown: true });
  const card = await use("say", { text: "Build **finished**", rows: [{ label: "main", text: "All 43 checks passed" }, { text: "Took 12 minutes" }] });
  assert.deepEqual(card.value, { shown: true });
  assert.deepEqual(fake.state.said.at(-1), {
    text: "Build finished",
    kind: "notice",
    seconds: null,
    shown: true,
    details: [{ label: "main", text: "All 43 checks passed" }, { label: "", text: "Took 12 minutes" }],
  });
  // With rows the line is a headline, which is cut to the glass's limit and not refused.
  await use("say", { text: "x".repeat(40) + " " + "y".repeat(40), rows: [{ text: "one" }] });
  assert.equal(fake.state.said.at(-1).text, "x".repeat(40) + "...");
  assert.equal((await use("say", { text: "\u{1F600}" })).text, "text has nothing that this glass can draw. Nothing was shown.");
  assert.equal((await use("say", { text: "ok", rows: [{ text: "x".repeat(91) }] })).isError, true);

  fake.state.automation.sleeping = true;
  assert.deepEqual((await use("say", { text: "Anyone there?" })).value, {
    shown: false,
    reason: "sleeping",
    note: "The display is dark, so nobody saw it. It was not woken.",
  });
  assert.equal(fake.state.automation.sleeping, true);
  assert.deepEqual(noted.map((entry) => entry.acted), [["say"]], "the same tool is noted once in ten minutes");
});

test("ask hands the words to the mirror's assistant and tells what it answered", async (t) => {
  const { use, fake, noted } = await startMcp(t);
  const off = await use("ask", { text: "what is on my list?" });
  assert.deepEqual([off.isError, off.text], [true, "The mirror's assistant gave no answer: The assistant is switched off."]);
  fake.state.assistantAnswers = (text) => ({
    id: "ask-1", heard: text, reply: "Two things on your list", details: [{ label: "7:00 AM", text: "Take out the trash" }, { label: "", text: "Buy milk" }],
    ignored: false, reason: "", listen: false, acted: ["get_state", "present"], ms: { stt: 0, agent: 900, total: 950 },
  });
  const answered = await use("ask", { text: "  what is on my list?  " });
  assert.deepEqual(answered.value, {
    reply: "Two things on your list",
    rows: [{ label: "7:00 AM", text: "Take out the trash" }, { label: "", text: "Buy milk" }],
    used: ["get_state", "present"],
  });
  assert.deepEqual(fake.state.asked, ["what is on my list?"]);
  fake.state.assistantAnswers = () => ({ reply: "I can't think right now. Please try again in a minute.", details: [], acted: [], error: "not-ready: No model is chosen yet." });
  assert.deepEqual((await use("ask", { text: "hello" })).value, {
    reply: "I can't think right now. Please try again in a minute.",
    trouble: "not-ready: No model is chosen yet.",
  });
  // The assistant keeps its own record of what it was asked.
  assert.deepEqual(noted, []);
});

test("what a program changed is told to the tending run as asked for", () => {
  const message = tendingMessage({
    requests: [
      { time: "7:02 AM", heard: "show the flowers", acted: ["set_background"] },
      { time: "7:40 AM", heard: "", acted: ["arrange_widgets"] },
    ],
    snapshot: {},
  });
  assert.match(message, /^- 7:02 AM "show the flowers" \(set_background\)$/m);
  assert.match(message, /^- 7:40 AM a program on the network \(arrange_widgets\)$/m);
});

test("/mcp is not there until a key is made, and then takes that key and no other", async (t) => {
  const without = await startCompanion(t);
  const none = await post(without.base, { jsonrpc: "2.0", id: 1, method: "ping" });
  assert.equal(none.status, 404);
  assert.deepEqual(none.body, {
    jsonrpc: "2.0",
    id: null,
    error: { code: -32000, message: 'MCP is not switched on in this companion. On its machine, "node src/cli.js mcp-key" makes a key; then restart it.' },
  });

  const { base, call: companion, events } = await startCompanion(t, [], {}, { mcp: true });
  for (const key of [null, SECRET, "test-mcp-key-012345678", `${MCP_KEY}0`]) {
    const refused = await post(base, { jsonrpc: "2.0", id: 1, method: "ping" }, { key });
    assert.equal(refused.status, 401, String(key));
    assert.match(refused.body.error.message, /This needs the companion's MCP key as a bearer token/);
  }
  // The key of the programs does not open what the mirror calls.
  assert.equal((await companion("GET", "/v1/health", { auth: `Bearer ${MCP_KEY}` })).status, 401);
  assert.deepEqual(await post(base, { jsonrpc: "2.0", id: 1, method: "ping" }).then((answer) => [answer.status, answer.body]), [200, { jsonrpc: "2.0", id: 1, result: {} }]);

  for (const method of ["GET", "DELETE"]) {
    const refused = await post(base, null, { method });
    assert.deepEqual([refused.status, refused.headers.get("allow")], [405, "POST"]);
  }
  const garbled = await post(base, "{not json");
  assert.deepEqual([garbled.status, garbled.body.error.code], [400, -32700]);
  const quiet = await post(base, { jsonrpc: "2.0", method: "notifications/initialized" });
  assert.deepEqual([quiet.status, quiet.body], [202, null]);
  const several = await post(base, [{ jsonrpc: "2.0", id: 1, method: "ping" }, { jsonrpc: "2.0", id: 2, method: "tools/list" }]);
  assert.deepEqual(several.body.map((answer) => answer.id), [1, 2]);
  assert.equal(several.body[1].result.tools.length, NAMES.length);
  assert.equal((await post(base, JSON.stringify({ jsonrpc: "2.0", id: 1, method: "ping", params: { pad: "y".repeat(300_000) } }))).status, 413);
  assert.ok(!JSON.stringify(events()).includes(MCP_KEY), "the key is not written to the log");
});

test("over HTTP a program changes the mirror, health counts it and the activity shows it", async (t) => {
  const { base, call: companion, mirror } = await startCompanion(t, [], {}, { mcp: true });
  const shown = await post(base, call(1, "show_moment", { kind: "text", text: "Hello", seconds: 30 }));
  assert.equal(shown.status, 200);
  assert.equal(JSON.parse(shown.body.result.content[0].text).kind, "text");
  assert.equal(mirror.state.moments[0].text, "Hello");
  await post(base, call(2, "get_state"));
  const health = (await companion("GET", "/v1/health")).body;
  assert.deepEqual(health.mcp, { on: true, toolsOnly: false, calls: 2 });
  const { entries } = (await companion("GET", "/v1/activity")).body;
  assert.deepEqual(entries.map((entry) => [entry.source, entry.acted]), [["mcp", ["show_moment"]]]);
  assert.equal(health.last, null, "what a program did is not something a person said");
});

test("a change waits for the request that is being served, and a read does not", async (t) => {
  let release;
  const held = new Promise((resolve) => (release = resolve));
  const { base, ask, mirror, brain } = await startCompanion(t, [{ before: () => held, calls: [{ tool: "set_power", args: { state: "asleep" } }], text: "Good night." }], {}, { mcp: true });
  const typed = ask("go to sleep");
  await until(() => brain.runs.length === 1, "the model to be at work");
  let changed = false;
  const change = post(base, call(1, "show_moment", { kind: "text", text: "Hello" })).then((answer) => {
    changed = true;
    return answer;
  });
  const read = await post(base, call(2, "get_state"));
  assert.equal(JSON.parse(read.body.result.content[0].text).display.power, "awake");
  assert.equal(changed, false);
  assert.equal(mirror.state.moments.length, 0);
  release();
  assert.equal((await typed).body.reply, "Good night.");
  // The moment went up after the display was put to sleep, and says so.
  assert.match(JSON.parse((await change).body.result.content[0].text).note, /The display is dark/);
  const order = mirror.writes().map((request) => request.path);
  assert.ok(order.indexOf("/api/v1/automation/sleep") < order.indexOf("/api/v1/moments"), order.join(" "));
});

test("the reference client connects over HTTP, lists the tools and calls them", async (t) => {
  const { base, mirror } = await startCompanion(t, [], {}, { mcp: true });
  const client = new Client({ name: "reference-client", version: "1.0.0" });
  const transport = new StreamableHTTPClientTransport(new URL(`${base}/mcp`), { requestInit: { headers: { Authorization: `Bearer ${MCP_KEY}` } } });
  await client.connect(transport);
  t.after(() => client.close());
  assert.deepEqual(client.getServerVersion(), { name: "mirror-companion", title: "MIRROR", version: "0.1.0" });
  assert.deepEqual(client.getServerCapabilities(), { tools: { listChanged: false } });
  assert.match(client.getInstructions(), /On this glass black is mirror/);
  const { tools } = await client.listTools();
  assert.deepEqual(tools.map((tool) => tool.name), NAMES);
  const drawn = await client.callTool({
    name: "show_moment",
    arguments: { kind: "drawing", title: "Heart", shapes: [{ shape: "path", d: "M50 80 L20 50 A15 15 0 0 1 50 30 A15 15 0 0 1 80 50 Z", stroke: "#ff8080" }] },
  });
  assert.equal(drawn.isError, undefined);
  assert.equal(mirror.state.moments[0].kind, "drawing");
  const picture = await client.callTool({ name: "look", arguments: {} });
  assert.deepEqual(picture.content.map((part) => part.type), ["text", "image"]);
  const refused = await client.callTool({ name: "board_add", arguments: { kind: "reminder", title: "No time given" } });
  assert.equal(refused.isError, true);
  assert.match(refused.content[0].text, /A reminder needs due/);
  await assert.rejects(client.callTool({ name: "forget", arguments: { containing: "everything" } }), /There is no tool called "forget"/);
  await client.ping();
});

test("a wrong key is refused before the reference client gets anywhere", async (t) => {
  const { base } = await startCompanion(t, [], {}, { mcp: true });
  const client = new Client({ name: "reference-client", version: "1.0.0" });
  const transport = new StreamableHTTPClientTransport(new URL(`${base}/mcp`), { requestInit: { headers: { Authorization: "Bearer not-the-key-0123456789" } } });
  await assert.rejects(client.connect(transport));
});

test("lines in, lines out: a client that starts the server itself is answered in any order", async (t) => {
  const { mcp, fake } = await startMcp(t);
  const input = new PassThrough();
  const output = new PassThrough();
  const answers = [];
  let rest = "";
  output.on("data", (chunk) => {
    rest += chunk;
    const lines = rest.split("\n");
    rest = lines.pop();
    for (const line of lines) answers.push(JSON.parse(line));
  });
  const served = serveLines(mcp, { input, output });
  input.write(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-03-26" } }) + "\n");
  input.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) + "\r\n\n");
  input.write("{broken\n");
  input.write(JSON.stringify(call(2, "show_moment", { kind: "text", text: "Hello" })) + "\n");
  input.end(JSON.stringify({ jsonrpc: "2.0", id: 3, method: "ping" }) + "\n");
  await served;
  assert.deepEqual(answers.map((answer) => answer.id).sort(), [1, 2, 3, null].sort());
  assert.equal(answers.find((answer) => answer.id === null).error.code, -32700);
  assert.equal(answers.find((answer) => answer.id === 1).result.protocolVersion, "2025-03-26");
  assert.equal(fake.state.moments[0].text, "Hello");
});

test("the reference client starts the mcp command itself and uses the mirror through it", async (t) => {
  const { fake } = await startTools(t);
  const folder = temporaryDirectory(t);
  const file = path.join(folder, "config.json");
  fs.writeFileSync(file, JSON.stringify({ secret: SECRET, mirror: { host: fake.host, port: fake.port, token: fake.token }, stateDir: path.join(folder, "state") }));
  const transport = new StdioClientTransport({ command: process.execPath, args: [CLI, "mcp"], env: { ...process.env, MIRROR_COMPANION_CONFIG: file }, stderr: "pipe" });
  let logged = "";
  transport.stderr.on("data", (chunk) => (logged += chunk));
  const client = new Client({ name: "reference-client", version: "1.0.0" });
  t.after(() => client.close());
  await client.connect(transport);
  assert.deepEqual((await client.listTools()).tools.map((tool) => tool.name), NAMES);
  const added = await client.callTool({ name: "board_add", arguments: { kind: "todo", title: "Water the plants" } });
  assert.equal(added.isError, undefined, added.content[0].text);
  assert.equal(fake.state.board.items[0].title, "Water the plants");
  const state = JSON.parse((await client.callTool({ name: "get_state", arguments: {} })).content[0].text);
  assert.equal(state.board.items[0].title, "Water the plants");
  await client.close();
  // The log goes where the protocol is not, and holds no token.
  await until(() => /"event":"tool"/.test(logged), "the log of the command");
  assert.match(logged, /"event":"mcp\.stdio"/);
  assert.match(logged, /"event":"tool","tool":"board_add","ok":true/);
  assert.ok(!logged.includes(fake.token));
});

test("the mcp command without a pairing says how to pair", async (t) => {
  const file = path.join(temporaryDirectory(t), "config.json");
  fs.writeFileSync(file, JSON.stringify({ secret: SECRET }));
  const ended = await new Promise((resolve) => {
    const child = execFile(process.execPath, [CLI, "mcp"], { env: { ...process.env, MIRROR_COMPANION_CONFIG: file } }, (error, stdout, stderr) =>
      resolve({ code: error ? error.code : 0, stdout, stderr }),
    );
    child.stdin.end();
  });
  assert.deepEqual([ended.code, ended.stdout], [1, ""]);
  assert.match(ended.stderr, /Not paired with a mirror yet\. Run: node src\/cli\.js pair --host MIRROR_ADDRESS --code CODE/);
});

test("a companion that serves its tools only starts no model and no speech-to-text, and is well when the mirror is there", async (t) => {
  const { base, call: companion, brain, stt, mirror, events } = await startCompanion(t, [], { greet: true, reminders: true, tend: true }, { toolsOnly: true });
  await until(() => mirror.requests().some((request) => request.path === "/api/v1/status"), "the one look at the mirror");
  await until(() => events().some((line) => line.event === "mirror.reachable"), "the mirror to be found");
  const health = (await companion("GET", "/v1/health")).body;
  assert.equal(health.ok, true);
  assert.deepEqual(health.mcp, { on: true, toolsOnly: true, calls: 0 });
  assert.deepEqual(health.brain, { ready: false, detail: "Not run: this companion serves its tools only." });
  assert.deepEqual([health.stt.ready, health.stt.detail], [false, "Not run: this companion serves its tools only."]);
  assert.deepEqual([brain.started, stt.started], [0, 0]);
  assert.equal(events().find((line) => line.event === "listening").toolsOnly, true);
  const shown = await post(base, call(1, "say", { text: "Hello" }));
  assert.deepEqual(JSON.parse(shown.body.result.content[0].text), { shown: true });
});
