import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { createStt, gpuLibraryPath } from "../src/stt.js";
import { fakeClock, until } from "./fakes/clock.js";
import { temporaryDirectory } from "./helpers.js";

// A stand-in for stt_worker.py that speaks the same lines, written in
// JavaScript so that the tests need no Python. Its "model" argument picks
// how it behaves.
const WORKER = `
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
const [behaviour, device, folder] = process.argv.slice(2);
const write = (message) => process.stdout.write(JSON.stringify(message) + "\\n");
const marker = path.join(folder, "started-before");
const firstStart = !fs.existsSync(marker);
fs.writeFileSync(marker, "yes");
if (behaviour === "fatal") {
  write({ event: "fatal", detail: "The speech model missing.en could not be loaded: no such model" });
  process.exit(1);
}
if (behaviour === "crash-first" && firstStart) process.exit(3);
process.stdout.write("a line that is not JSON\\n");
setTimeout(() => {
  write(
    behaviour === "no-gpu"
      ? { event: "ready", device: "cpu", detail: "The GPU could not be used (no CUDA), so speech is recognised on the CPU, which is slower.", loadMs: 40 }
      : { event: "ready", device: device === "auto" ? "cuda" : device, detail: "", loadMs: 40 },
  );
}, 40);
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  const request = JSON.parse(line);
  const bytes = Buffer.from(request.pcm, "base64").length;
  if (behaviour === "stuck" && firstStart) return;
  if (behaviour === "dies-on-request" && firstStart) process.exit(4);
  if (bytes === 2) write({ id: request.id, error: "too short to decode" });
  else write({ id: request.id, text: "heard " + bytes + " bytes", ms: 7, device: "cuda" });
});
`;

function startStt(t, behaviour, { device = "auto", python = process.execPath } = {}) {
  const folder = temporaryDirectory(t);
  const script = path.join(folder, "worker.mjs");
  fs.writeFileSync(script, WORKER);
  const clock = fakeClock();
  const events = [];
  const stt = createStt({
    python,
    script,
    model: behaviour,
    device,
    modelsDir: folder,
    clock,
    log: (event, fields) => events.push({ event, ...fields }),
  });
  t.after(() => stt.stop());
  return { stt, clock, events };
}

test("requests wait while the model loads and are answered in order", async (t) => {
  const { stt, events } = startStt(t, "ok");
  assert.deepEqual(stt.health(), { ready: false, model: "ok", device: "", detail: "Not started." });
  stt.start();
  assert.equal(stt.health().detail, "The speech model is loading.");
  const answers = await Promise.all([stt.transcribe(Buffer.alloc(100)), stt.transcribe(Buffer.alloc(200)), stt.transcribe(Buffer.alloc(300))]);
  assert.deepEqual(answers, [
    { text: "heard 100 bytes", ms: 7 },
    { text: "heard 200 bytes", ms: 7 },
    { text: "heard 300 bytes", ms: 7 },
  ]);
  assert.deepEqual(stt.health(), { ready: true, model: "ok", device: "cuda", detail: "" });
  assert.ok(events.some((entry) => entry.event === "stt.output"), "a line that is not JSON is logged, not obeyed");
});

test("a worker that falls back to the CPU says so in health", async (t) => {
  const { stt } = startStt(t, "no-gpu");
  stt.start();
  await until(() => stt.health().ready, "the worker to be ready");
  const health = stt.health();
  assert.equal(health.device, "cpu");
  assert.match(health.detail, /GPU could not be used.*CPU, which is slower/);
});

test("a failed transcription is an error for that request only", async (t) => {
  const { stt } = startStt(t, "ok");
  stt.start();
  await assert.rejects(stt.transcribe(Buffer.alloc(2)), /Speech-to-text failed: too short to decode/);
  assert.equal((await stt.transcribe(Buffer.alloc(64))).text, "heard 64 bytes");
});

test("a worker that exits is started again after a pause", async (t) => {
  const { stt, clock, events } = startStt(t, "crash-first");
  stt.start();
  await until(() => events.some((entry) => entry.event === "stt.stopped"), "the worker to exit");
  assert.equal(stt.health().ready, false);
  assert.match(stt.health().detail, /stopped \(exit code 3\)\. It is started again in 1 s/);
  await assert.rejects(stt.transcribe(Buffer.alloc(10)), /stopped \(exit code 3\)/, "while it is down a request fails at once");
  await clock.advance(1000);
  assert.equal((await stt.transcribe(Buffer.alloc(10))).text, "heard 10 bytes");
  assert.equal(stt.health().ready, true);
});

test("a worker that dies with a request in hand fails that request and comes back", async (t) => {
  const { stt, clock, events } = startStt(t, "dies-on-request");
  stt.start();
  await assert.rejects(stt.transcribe(Buffer.alloc(10)), /stopped \(exit code 4\)/);
  await clock.advance(1000);
  assert.equal((await stt.transcribe(Buffer.alloc(10))).text, "heard 10 bytes");
  assert.equal(events.filter((entry) => entry.event === "stt.ready").length, 2);
});

test("the pause before a new start grows while the worker keeps failing", async (t) => {
  const { stt, clock, events } = startStt(t, "fatal");
  stt.start();
  const stops = () => events.filter((entry) => entry.event === "stt.stopped");
  await until(() => stops().length === 1);
  assert.match(stt.health().detail, /The speech model missing\.en could not be loaded: no such model\. It is started again in 1 s/);
  await clock.advance(1000);
  await until(() => stops().length === 2);
  await clock.advance(2000);
  await until(() => stops().length === 3);
  assert.deepEqual(stops().map((entry) => entry.retryMs), [1000, 2000, 4000]);
});

test("a missing Python says where it looked and what to do", async (t) => {
  const missing = path.join(temporaryDirectory(t), "venv", "bin", "python");
  const { stt, events } = startStt(t, "ok", { python: missing });
  stt.start();
  await until(() => events.some((entry) => entry.event === "stt.stopped"));
  assert.match(stt.health().detail, /Python was not found at .*python\. Run deploy\/install\.sh, or set stt\.python in the config/);
  assert.equal(stt.health().ready, false);
});

test("a worker that sits on a request is given up on and replaced", async (t) => {
  const { stt, clock, events } = startStt(t, "stuck");
  stt.start();
  await until(() => stt.health().ready);
  const stuck = stt.transcribe(Buffer.alloc(10), { timeoutMs: 5000 });
  const refused = assert.rejects(stuck, /Speech-to-text took too long/);
  await clock.advance(5000);
  await refused;
  await until(() => events.some((entry) => entry.event === "stt.stopped"), "the stuck worker to be stopped");
  await clock.advance(1000);
  assert.equal((await stt.transcribe(Buffer.alloc(10))).text, "heard 10 bytes");
});

test("stopping ends the worker and refuses further requests", async (t) => {
  const { stt, events } = startStt(t, "ok");
  stt.start();
  await until(() => stt.health().ready);
  await stt.stop();
  assert.deepEqual(stt.health(), { ready: false, model: "ok", device: "cuda", detail: "Stopped." });
  await assert.rejects(stt.transcribe(Buffer.alloc(10)), /not running/);
  assert.ok(!events.some((entry) => entry.event === "stt.stopped"), "a wanted stop is not reported as a failure");
});

test("the GPU libraries that pip installed are found beside the Python", (t) => {
  const venv = temporaryDirectory(t);
  const nvidia = path.join(venv, "lib", "python3.12", "site-packages", "nvidia");
  for (const part of ["cublas", "cudnn"]) fs.mkdirSync(path.join(nvidia, part, "lib"), { recursive: true });
  fs.mkdirSync(path.join(nvidia, "empty"), { recursive: true });
  const found = gpuLibraryPath(path.join(venv, "bin", "python")).split(path.delimiter);
  assert.deepEqual(found, [path.join(nvidia, "cublas", "lib"), path.join(nvidia, "cudnn", "lib")]);
  assert.equal(gpuLibraryPath(path.join(temporaryDirectory(t), "bin", "python")), "");
});
