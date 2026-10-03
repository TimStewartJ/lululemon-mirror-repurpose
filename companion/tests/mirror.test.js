import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";
import { createMirror, MirrorRefused, MirrorUnreachable, pairWithMirror } from "../src/mirror.js";
import { startFakeMirror } from "./fakes/mirror.js";

async function withFake(t, options = {}) {
  const fake = await startFakeMirror();
  const events = [];
  const mirror = createMirror({
    host: fake.host,
    port: fake.port,
    token: fake.token,
    log: (event, fields) => events.push({ event, ...fields }),
    ...options,
  });
  t.after(async () => {
    mirror.close();
    await fake.close();
  });
  return { fake, mirror, events };
}

/** A server whose behaviour a test decides, for what the fake mirror does not do. */
async function serverThat(t, handle) {
  const server = http.createServer(handle);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  return server.address().port;
}

test("a request carries the token and returns the mirror's JSON", async (t) => {
  const { mirror } = await withFake(t);
  const status = await mirror.get("/api/v1/status");
  assert.equal(status.timeZone, "America/Los_Angeles");
  assert.deepEqual(mirror.health(), { reachable: true, version: "2.3.0", detail: "" });
});

test("a body is sent as JSON with its length", async (t) => {
  const { mirror, fake } = await withFake(t);
  const answer = await mirror.call("POST", "/api/v1/assistant/say", { body: { text: "Grüße", kind: "notice" } });
  assert.deepEqual(answer, { shown: true });
  assert.equal(fake.state.said[0].text, "Grüße");
});

test("a dropped connection is tried once more", async (t) => {
  const { mirror, fake } = await withFake(t);
  fake.state.dropNext = 1;
  assert.equal((await mirror.get("/api/v1/status")).appVersion, "2.3.0");
});

test("two dropped connections are reported as unreachable, and noticed in health", async (t) => {
  const { mirror, fake, events } = await withFake(t);
  await mirror.get("/api/v1/status");
  fake.state.dropNext = 2;
  await assert.rejects(mirror.get("/api/v1/status"), (error) => error instanceof MirrorUnreachable && /cannot be reached/.test(error.message));
  assert.equal(mirror.health().reachable, false);
  assert.match(mirror.health().detail, /cannot be reached/);
  await mirror.get("/api/v1/status");
  assert.equal(mirror.health().reachable, true);
  assert.deepEqual(events.map((entry) => entry.event), ["mirror.reachable", "mirror.unreachable", "mirror.reachable"]);
});

test("nothing listening is unreachable", async (t) => {
  const port = await serverThat(t, () => {});
  const mirror = createMirror({ host: "127.0.0.1", port: port === 1 ? 2 : 1, token: "test-mirror-token", connectTimeoutMs: 500 });
  await assert.rejects(mirror.get("/api/v1/status"), MirrorUnreachable);
});

test("an answer that never comes is given up on, and not asked for twice", async (t) => {
  let requests = 0;
  const port = await serverThat(t, () => {
    requests += 1;
  });
  const mirror = createMirror({ host: "127.0.0.1", port, token: "test-mirror-token", responseTimeoutMs: 150 });
  t.after(() => mirror.close());
  await assert.rejects(mirror.get("/api/v1/status"), MirrorUnreachable);
  assert.equal(requests, 1);
});

test("a refusal carries the mirror's own words and field", async (t) => {
  const { mirror } = await withFake(t);
  await assert.rejects(
    mirror.call("PUT", "/api/v1/board/items/abc", { body: { kind: "reminder", title: "No time" } }),
    (error) => error instanceof MirrorRefused && error.status === 400 && error.field === "due" && /A reminder needs due/.test(error.message),
  );
  const answer = await mirror.request("GET", "/api/v1/nothing-here");
  assert.equal(answer.status, 404);
});

test("a refused token says to pair again", async (t) => {
  const { fake } = await withFake(t);
  const mirror = createMirror({ host: fake.host, port: fake.port, token: "a-wrong-test-token" });
  t.after(() => mirror.close());
  await assert.rejects(mirror.get("/api/v1/status"), (error) => error instanceof MirrorRefused && error.status === 401);
  assert.equal(mirror.health().reachable, false);
  assert.match(mirror.health().detail, /refused the token\. Pair again/);
});

test("without a host or token nothing is sent", async () => {
  const mirror = createMirror({ host: "", port: 8787, token: "" });
  assert.match(mirror.health().detail, /Not paired/);
  await assert.rejects(mirror.get("/api/v1/status"), (error) => error instanceof MirrorUnreachable && /Not paired/.test(error.message));
});

test("a picture comes back as bytes", async (t) => {
  const { mirror } = await withFake(t);
  const answer = await mirror.request("GET", "/api/v1/screenshot");
  assert.equal(answer.status, 200);
  assert.equal(answer.contentType, "image/jpeg");
  assert.equal(answer.buffer[0], 0xff);
  assert.equal(answer.body, null);
});

test("a refusal of a request with a body closes that connection", async (t) => {
  // Mirror Home's server would take a body it did not read for the next request.
  const sockets = new Set();
  const port = await serverThat(t, (request, response) => {
    sockets.add(request.socket);
    request.resume();
    request.on("end", () => {
      response.writeHead(request.method === "PUT" ? 400 : 200, { "Content-Type": "application/json" });
      response.end("{}");
    });
  });
  const mirror = createMirror({ host: "127.0.0.1", port, token: "test-mirror-token" });
  t.after(() => mirror.close());
  await mirror.request("GET", "/one");
  await mirror.request("GET", "/two");
  assert.equal(sockets.size, 1, "requests share a connection");
  await mirror.request("PUT", "/three", { body: { any: "thing" } });
  await mirror.request("GET", "/four");
  assert.equal(sockets.size, 2, "after the refusal a new connection is used");
});

test("pairing exchanges a code for a token and passes on a refusal", async (t) => {
  const port = await serverThat(t, (request, response) => {
    let text = "";
    request.on("data", (chunk) => (text += chunk));
    request.on("end", () => {
      const body = JSON.parse(text);
      const ok = request.url === "/api/v1/pair" && body.code === "123456" && body.name === "Mirror companion";
      response.writeHead(ok ? 200 : 401, { "Content-Type": "application/json" });
      response.end(
        JSON.stringify(
          ok
            ? { token: "test-token-from-pairing", clientId: "c1", clientName: body.name }
            : { error: "That code is not right. Check the code and try again.", reason: "wrong-code" },
        ),
      );
    });
  });
  assert.equal(await pairWithMirror({ host: "127.0.0.1", port, code: "123456", name: "Mirror companion" }), "test-token-from-pairing");
  await assert.rejects(pairWithMirror({ host: "127.0.0.1", port, code: "000000", name: "Mirror companion" }), /That code is not right/);
});

test("the fake mirror takes rows with a caption, as Mirror Home does, and refuses rows that break its limits", async (t) => {
  const { fake, mirror } = await withFake(t);
  const say = (body) => mirror.request("POST", "/api/v1/assistant/say", { body });
  const details = [{ label: "Reminder", text: "Now, 9:00 PM" }, { label: "", text: "x".repeat(90) }];
  assert.deepEqual((await say({ text: "Start the dishwasher", kind: "notice", seconds: 20, details })).body, { shown: true });
  assert.deepEqual((await say({ text: "No rows", details: [] })).body, { shown: true });
  assert.deepEqual((await say({ text: "As before" })).body, { shown: true });
  assert.deepEqual(fake.state.said, [
    { text: "Start the dishwasher", kind: "notice", seconds: 20, shown: true, details },
    { text: "No rows", kind: "reply", seconds: null, shown: true, details: [] },
    { text: "As before", kind: "reply", seconds: null, shown: true },
  ]);
  const row = { label: "To do", text: "Buy milk" };
  const refused = [
    [row, row, row, row, row, row],
    [{ label: "x".repeat(15), text: "Buy milk" }],
    [{ label: "To do", text: "x".repeat(91) }],
    [{ label: "To do", text: "" }],
    [{ label: "To do" }],
    [{ label: "To do", text: "two\nlines" }],
    [{ label: 5, text: "Buy milk" }],
    ["Buy milk"],
    { label: "To do", text: "Buy milk" },
  ];
  for (const rows of refused) {
    const answer = await say({ text: "A card", details: rows });
    assert.equal(answer.status, 400, JSON.stringify(rows));
    assert.match(answer.body.error, /details takes up to 5 rows/);
  }
  assert.equal(fake.state.said.length, 3);
  await assert.rejects(mirror.call("POST", "/api/v1/assistant/say", { body: { text: "A card", details: refused[0] } }), MirrorRefused);
});
