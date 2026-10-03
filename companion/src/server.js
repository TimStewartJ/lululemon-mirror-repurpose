import crypto from "node:crypto";
import http from "node:http";
import { describeError } from "./log.js";
import { readWav } from "./wav.js";

export const MAX_UTTERANCE_BYTES = 1_500_000;
const MAX_JSON_BYTES = 64 * 1024;
const FLOOD_BYTES = 8 * 1024 * 1024;
const ADDRESSED = ["name", "window", "follow-up"];

/** An answer that is not 200, with the sentence that explains it. */
class Refusal extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/**
 * The companion's HTTP side: what Mirror Home calls.
 *
 * @param {Object} parts
 * @param {string} parts.secret The bearer secret shared with the mirror.
 * @param {ReturnType<import("./assistant.js").createAssistant>} parts.assistant
 * @param {{ event: (event: object) => void }} parts.proactive
 * @param {ReturnType<import("./activity.js").createActivity>} parts.activity
 * @param {ReturnType<import("./queue.js").createQueue>} parts.queue
 * @param {() => object} parts.health Builds the health report from what is already known.
 * @param {(event: string, fields?: object) => void} parts.log
 * @param {import("./clock.js").Clock} parts.clock
 * @returns {http.Server}
 */
export function createServer({ secret, assistant, proactive, activity, queue, health, log, clock }) {
  const expected = crypto.createHash("sha256").update(`Bearer ${secret}`).digest();
  let counter = 0;

  /** Compares in constant time; hashing first makes the lengths equal. */
  function authorized(request) {
    const given = crypto.createHash("sha256").update(String(request.headers.authorization ?? "")).digest();
    return crypto.timingSafeEqual(given, expected);
  }

  async function route(request, url) {
    const path = url.pathname;
    if (path === "/v1/health") {
      requireMethod(request, "GET");
      return [200, health()];
    }
    if (path === "/v1/activity") {
      requireMethod(request, "GET");
      const limit = url.searchParams.get("limit") ?? "20";
      if (!/^\d{1,3}$/.test(limit) || Number(limit) < 1 || Number(limit) > 200) {
        throw new Refusal(400, "limit must be a whole number from 1 to 200.");
      }
      return [200, { entries: activity.recent(Number(limit)) }];
    }
    if (path === "/v1/event") {
      requireMethod(request, "POST");
      const event = await readJson(request);
      if (typeof event.type !== "string" || event.type.length === 0 || event.type.length > 40) {
        throw new Refusal(400, "An event needs a type, such as \"presence\".");
      }
      proactive.event(event);
      return [202, { accepted: true }];
    }
    if (path === "/v1/ask") {
      requireMethod(request, "POST");
      const body = await readJson(request);
      const text = typeof body.text === "string" ? body.text.trim() : "";
      if (text.length < 1 || text.length > 500) {
        throw new Refusal(400, "text must be 1 to 500 characters.");
      }
      if (body.source !== undefined && typeof body.source !== "string") {
        throw new Refusal(400, "source must be text, such as \"controls\".");
      }
      const leave = enter();
      try {
        counter += 1;
        const id = `ask-${clock.now().toString(36)}-${counter}`;
        return [200, await assistant.ask({ id, text, source: body.source === "test" ? "test" : "controls" })];
      } finally {
        leave();
      }
    }
    if (path === "/v1/utterance") {
      requireMethod(request, "POST");
      const addressed = String(request.headers["x-mirror-addressed"] ?? "name").toLowerCase();
      if (!ADDRESSED.includes(addressed)) {
        throw new Refusal(400, "X-Mirror-Addressed must be name, window or follow-up.");
      }
      counter += 1;
      const id = String(request.headers["x-mirror-utterance"] ?? `u-${clock.now().toString(36)}-${counter}`);
      if (!/^[A-Za-z0-9-]{1,64}$/.test(id)) {
        throw new Refusal(400, "X-Mirror-Utterance must be 1 to 64 letters, digits or dashes.");
      }
      const wav = await readBody(request, MAX_UTTERANCE_BYTES, "The recording is larger than 1,500,000 bytes.");
      const sound = readWav(wav);
      if (sound.problem) throw new Refusal(400, sound.problem);
      const leave = enter();
      try {
        return [200, await assistant.utterance({ id, wav, addressed, ...sound })];
      } finally {
        leave();
      }
    }
    throw new Refusal(404, "There is no such route.");
  }

  function enter() {
    const leave = queue.enter();
    if (!leave) throw new Refusal(429, "Busy");
    return leave;
  }

  return http.createServer((request, response) => {
    const started = clock.now();
    const url = new URL(request.url ?? "/", "http://companion");
    Promise.resolve()
      .then(() => {
        if (!authorized(request)) throw new Refusal(401, "Unauthorized");
        return route(request, url);
      })
      .then(
        ([status, body]) => send(response, status, body),
        (error) => {
          if (error instanceof Refusal) {
            // A body that was not read to its end would be taken for the next
            // request, so that connection is closed after the answer.
            if (!request.complete) response.setHeader("Connection", "close");
            send(response, error.status, { error: error.message });
            if (error.status !== 401) log("request.refused", { path: url.pathname, status: error.status, detail: error.message });
            return;
          }
          log("request.failed", { path: url.pathname, detail: describeError(error) });
          send(response, 500, { error: "The companion could not handle the request." });
        },
      )
      .finally(() => {
        const ms = clock.now() - started;
        if (url.pathname !== "/v1/health") log("request", { method: request.method, path: url.pathname, status: response.statusCode, ms });
      });
  });
}

function requireMethod(request, method) {
  if (request.method !== method) throw new Refusal(405, `This route takes ${method} only.`);
}

function send(response, status, body) {
  if (response.headersSent || response.destroyed) return;
  const payload = Buffer.from(JSON.stringify(body), "utf8");
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": payload.length,
    "Cache-Control": "no-store",
  });
  response.end(payload);
}

/**
 * Reads a request body up to a limit; more than that is a 413. A body that is
 * somewhat too large is still read to its end and thrown away, so that the
 * sender gets the answer and not a broken connection. A flood is cut off.
 */
function readBody(request, limit, tooLarge) {
  return new Promise((resolve, reject) => {
    const flood = limit + FLOOD_BYTES;
    if (Number(request.headers["content-length"]) > flood) {
      reject(new Refusal(413, tooLarge));
      return;
    }
    let chunks = [];
    let size = 0;
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size <= limit) {
        chunks.push(chunk);
        return;
      }
      chunks = [];
      if (size > flood) {
        request.pause();
        reject(new Refusal(413, tooLarge));
      }
    });
    request.on("end", () => {
      if (size > limit) reject(new Refusal(413, tooLarge));
      else resolve(Buffer.concat(chunks));
    });
    request.on("error", () => reject(new Refusal(400, "The request body could not be read.")));
    request.on("aborted", () => reject(new Refusal(400, "The request was cut off.")));
  });
}

async function readJson(request) {
  const body = await readBody(request, MAX_JSON_BYTES, "The request body is larger than 64 KB.");
  let value;
  try {
    value = JSON.parse(body.toString("utf8"));
  } catch {
    throw new Refusal(400, "The request body must be JSON.");
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Refusal(400, "The request body must be a JSON object.");
  }
  return value;
}
