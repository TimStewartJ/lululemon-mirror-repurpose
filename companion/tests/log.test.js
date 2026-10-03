import assert from "node:assert/strict";
import test from "node:test";
import { createLog, describeError } from "../src/log.js";

test("an event is one JSON line with its time", () => {
  const lines = [];
  const log = createLog({ write: (line) => lines.push(line), clock: { now: () => Date.UTC(2026, 9, 3, 14, 12) } });
  log("exchange", { heard: "go to sleep", ms: { total: 12 } });
  assert.equal(lines.length, 1);
  assert.deepEqual(JSON.parse(lines[0]), {
    at: "2026-10-03T14:12:00.000Z",
    event: "exchange",
    heard: "go to sleep",
    ms: { total: 12 },
  });
});

test("a secret is cut out wherever it turns up", () => {
  const lines = [];
  const log = createLog({ write: (line) => lines.push(line), secrets: ["test-secret-0123456789", "test-mirror-token", ""] });
  log("mirror.request", { headers: { Authorization: "Bearer test-mirror-token" }, note: "secret is test-secret-0123456789" });
  assert.ok(!lines[0].includes("test-mirror-token"));
  assert.ok(!lines[0].includes("test-secret-0123456789"));
  assert.match(lines[0], /Bearer \[hidden\]/);
});

test("what cannot be written does not stop the log", () => {
  const lines = [];
  const log = createLog({ write: (line) => lines.push(line) });
  const loop = {};
  loop.self = loop;
  log("odd", { loop });
  assert.equal(JSON.parse(lines[0]).event, "odd");
});

test("anything thrown is described in one line", () => {
  assert.equal(describeError(new Error("it broke")), "it broke");
  assert.equal(describeError("plain text"), "plain text");
  assert.equal(describeError(new TypeError("")), "TypeError");
});
