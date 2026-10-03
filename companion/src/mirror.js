import http from "node:http";

/** The mirror could not be reached, or is not set up yet. */
export class MirrorUnreachable extends Error {
  constructor(message) {
    super(message);
    this.name = "MirrorUnreachable";
  }
}

/** The mirror answered, and its answer was a refusal. */
export class MirrorRefused extends Error {
  /**
   * @param {number} status
   * @param {string} message The mirror's own sentence about what was wrong.
   * @param {string} [field]
   */
  constructor(status, message, field) {
    super(message);
    this.name = "MirrorRefused";
    this.status = status;
    this.field = field;
  }
}

/**
 * One HTTP exchange with a deadline for connecting and another for the answer.
 *
 * @returns {Promise<{ status: number, headers: http.IncomingHttpHeaders, buffer: Buffer }>}
 */
export function send({ agent, host, port, method, path, headers, payload, connectTimeoutMs, responseTimeoutMs }) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer;
    let connection = null;
    const request = http.request({ agent, host, port, method, path, headers }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("error", (error) => finish(error));
      response.on("end", () => {
        const ok = response.statusCode >= 200 && response.statusCode < 300;
        // Mirror Home's web server leaves a body it did not read on the
        // connection, where it would be taken for the start of the next
        // request. A refusal may have skipped the body, so that connection
        // is not used again.
        if (!ok && payload) connection?.destroy();
        finish(null, { status: response.statusCode, headers: response.headers, buffer: Buffer.concat(chunks) });
      });
    });
    request.on("error", (error) => finish(error));
    request.on("socket", (socket) => {
      connection = socket;
      if (socket.connecting) socket.once("connect", connected);
      else connected();
    });
    timer = setTimeout(() => expire("connect"), connectTimeoutMs);
    request.end(payload);

    function connected() {
      clearTimeout(timer);
      if (!settled) timer = setTimeout(() => expire("answer"), responseTimeoutMs);
    }
    function expire(phase) {
      const error = new Error(
        phase === "connect" ? "no connection within the time allowed" : "no answer within the time allowed",
      );
      error.phase = phase;
      request.destroy(error);
      finish(error);
    }
    function finish(error, result) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(result);
    }
  });
}

/**
 * The client for Mirror Home's control API. Its Wi-Fi radio sleeps between
 * requests, so the first connection after a pause can be slow or fail once:
 * connecting gets four seconds and one more try.
 *
 * @param {Object} options
 * @param {string} options.host
 * @param {number} [options.port]
 * @param {string} options.token
 * @param {number} [options.connectTimeoutMs]
 * @param {number} [options.responseTimeoutMs]
 * @param {(event: string, fields?: object) => void} [options.log]
 */
export function createMirror({
  host,
  port = 8787,
  token,
  connectTimeoutMs = 4000,
  responseTimeoutMs = 8000,
  log = () => {},
}) {
  // Mirror Home closes a connection that has been idle for five seconds.
  // Idle ones are dropped here after three, so that a request is not sent
  // down a connection that is just being closed.
  const agent = new http.Agent({ keepAlive: true, maxSockets: 6, timeout: 3000 });
  const health = { reachable: false, version: "", detail: "Not checked yet." };
  if (!host || !token) {
    health.detail = "Not paired with a mirror. Run: node src/cli.js pair --host MIRROR_ADDRESS --code CODE";
  }

  /**
   * Sends one request. Any answer from the mirror resolves, whatever its
   * status; only a failure to get an answer rejects.
   *
   * @param {string} method
   * @param {string} path
   * @param {{ body?: unknown }} [options]
   * @returns {Promise<{ status: number, body: any, buffer: Buffer, contentType: string }>}
   */
  async function request(method, path, { body } = {}) {
    if (!host || !token) throw new MirrorUnreachable(health.detail);
    const payload = body === undefined ? undefined : Buffer.from(JSON.stringify(body), "utf8");
    const headers = { Authorization: `Bearer ${token}`, Accept: "application/json" };
    if (payload) {
      headers["Content-Type"] = "application/json; charset=utf-8";
      headers["Content-Length"] = String(payload.length);
    }
    const started = Date.now();
    let answer;
    for (let attempt = 1; ; attempt++) {
      try {
        answer = await send({ agent, host, port, method, path, headers, payload, connectTimeoutMs, responseTimeoutMs });
        break;
      } catch (error) {
        // An answer that never came is not asked for twice: the mirror may
        // be busy with the first request still.
        if (attempt === 1 && error.phase !== "answer") continue;
        const detail = `The mirror at ${host}:${port} cannot be reached (${error.code || error.message}).`;
        if (health.reachable) log("mirror.unreachable", { method, path, detail });
        health.reachable = false;
        health.detail = detail;
        throw new MirrorUnreachable(detail);
      }
    }
    const ms = Date.now() - started;
    const contentType = String(answer.headers["content-type"] || "");
    let parsed = null;
    if (contentType.includes("json")) {
      try {
        parsed = JSON.parse(answer.buffer.toString("utf8"));
      } catch {
        parsed = null;
      }
    }
    if (answer.status === 401) {
      health.reachable = false;
      health.detail = "The mirror refused the token. Pair again: node src/cli.js pair --host MIRROR_ADDRESS --code CODE";
    } else {
      if (!health.reachable) log("mirror.reachable", { ms });
      health.reachable = true;
      health.detail = "";
      if (path === "/api/v1/status" && parsed?.appVersion) health.version = String(parsed.appVersion);
    }
    if (ms > 1500) log("mirror.slow", { method, path, status: answer.status, ms });
    return { status: answer.status, body: parsed, buffer: answer.buffer, contentType };
  }

  /**
   * Sends one request and returns the JSON of a successful answer.
   *
   * @throws {MirrorRefused} when the mirror answers with a refusal
   * @throws {MirrorUnreachable} when it does not answer
   */
  async function call(method, path, options) {
    const answer = await request(method, path, options);
    if (answer.status >= 200 && answer.status < 300) return answer.body ?? {};
    const message =
      answer.status === 401
        ? "The mirror no longer accepts the companion's token; its owner has to pair it again."
        : answer.body?.error || `The mirror answered ${answer.status}.`;
    throw new MirrorRefused(answer.status, message, answer.body?.field);
  }

  return {
    request,
    call,
    get: (path) => call("GET", path),
    /** What the last contact with the mirror showed; never asks the mirror. */
    health: () => ({ ...health }),
    close: () => agent.destroy(),
  };
}

/** @typedef {ReturnType<typeof createMirror>} Mirror */

/**
 * Exchanges a pairing code shown by the mirror for a token.
 *
 * @returns {Promise<string>} the token
 */
export async function pairWithMirror({ host, port = 8787, code, name }) {
  const payload = Buffer.from(JSON.stringify({ code, name }), "utf8");
  let answer;
  try {
    answer = await send({
      host,
      port,
      method: "POST",
      path: "/api/v1/pair",
      headers: { "Content-Type": "application/json; charset=utf-8", "Content-Length": String(payload.length) },
      payload,
      connectTimeoutMs: 6000,
      responseTimeoutMs: 10000,
    });
  } catch (error) {
    throw new Error(
      `The mirror at ${host}:${port} cannot be reached (${error.code || error.message}). Check the address and that the mirror is on.`,
    );
  }
  let body = {};
  try {
    body = JSON.parse(answer.buffer.toString("utf8"));
  } catch {
    // An answer that is not JSON is reported by its status below.
  }
  if (answer.status !== 200 || !body.token) {
    throw new Error(body.error || `The mirror answered ${answer.status} to the pairing request.`);
  }
  return String(body.token);
}
