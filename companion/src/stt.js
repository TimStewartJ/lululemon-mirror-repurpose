import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { systemClock } from "./clock.js";

const WORKER = fileURLToPath(new URL("./stt_worker.py", import.meta.url));
const FIRST_RETRY_MS = 1000;
const LONGEST_RETRY_MS = 60_000;

/**
 * Speech-to-text as the rest of the companion sees it.
 *
 * @typedef {Object} Stt
 * @property {() => void} start
 * @property {() => Promise<void>} stop
 * @property {() => { ready: boolean, model: string, device: string, detail: string }} health
 * @property {(pcm: Buffer, options?: { timeoutMs?: number }) => Promise<{ text: string, ms: number }>} transcribe
 *   Takes 16 kHz mono 16-bit samples. Rejects with a sentence about what went wrong.
 */

/**
 * Where the NVIDIA libraries installed by pip live, for LD_LIBRARY_PATH. The
 * GPU build of the speech library looks for them there and nowhere else.
 */
export function gpuLibraryPath(python) {
  const lib = path.join(path.dirname(path.dirname(python)), "lib");
  const found = [];
  try {
    for (const version of fs.readdirSync(lib).filter((name) => name.startsWith("python3"))) {
      const nvidia = path.join(lib, version, "site-packages", "nvidia");
      for (const part of fs.readdirSync(nvidia)) {
        const folder = path.join(nvidia, part, "lib");
        if (fs.existsSync(folder)) found.push(folder);
      }
    }
  } catch {
    // No such folders: there is no GPU installation, and the worker uses the CPU.
  }
  return found.join(path.delimiter);
}

/**
 * Runs the Python worker and keeps it running: requests wait while the model
 * loads, and a worker that exits is started again after a growing pause.
 *
 * @param {Object} options
 * @param {string} options.python The Python that has faster-whisper installed.
 * @param {string} options.model
 * @param {"auto"|"cuda"|"cpu"} options.device
 * @param {string} options.modelsDir Where the speech model is kept.
 * @param {string} [options.script] The worker to run; tests supply their own.
 * @param {(event: string, fields?: object) => void} [options.log]
 * @param {import("./clock.js").Clock} [options.clock]
 * @returns {Stt}
 */
export function createStt({ python, model, device, modelsDir, script = WORKER, log = () => {}, clock = systemClock }) {
  let child = null;
  let ready = false;
  let stopped = true;
  let usedDevice = device === "auto" ? "" : device;
  let detail = "Not started.";
  let retryMs = FIRST_RETRY_MS;
  let retryTimer = null;
  let nextId = 1;
  /** @type {{ id: string, pcm: Buffer, resolve: Function, reject: Function, timer: unknown, sent: boolean }[]} */
  const waiting = [];

  function launch() {
    retryTimer = null;
    if (stopped) return;
    ready = false;
    detail = "The speech model is loading.";
    const env = { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" };
    const libraries = gpuLibraryPath(python);
    if (libraries) env.LD_LIBRARY_PATH = [libraries, process.env.LD_LIBRARY_PATH].filter(Boolean).join(path.delimiter);
    const started = spawn(python, [script, model, device, modelsDir], { env, stdio: ["pipe", "pipe", "pipe"] });
    child = started;
    let fatal = "";
    lines(started.stdout, (line) => {
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        log("stt.output", { line: line.slice(0, 300) });
        return;
      }
      if (message.event === "ready") {
        ready = true;
        usedDevice = message.device;
        detail = message.detail || "";
        retryMs = FIRST_RETRY_MS;
        log("stt.ready", { model, device: usedDevice, loadMs: message.loadMs, detail });
        pump();
      } else if (message.event === "fatal") {
        fatal = message.detail;
      } else if (message.id !== undefined) {
        answer(message);
      }
    });
    lines(started.stderr, (line) => log("stt.stderr", { line: line.slice(0, 300) }));
    started.stdin.on("error", () => {});
    started.on("error", (error) => {
      fatal =
        error.code === "ENOENT"
          ? `Python was not found at ${python}. Run deploy/install.sh, or set stt.python in the config.`
          : `The speech-to-text worker could not be started: ${error.message}`;
      ended(started, fatal);
    });
    started.on("exit", (code) => ended(started, fatal || `The speech-to-text worker stopped (exit code ${code}).`));
  }

  function ended(which, why) {
    if (child !== which) return;
    child = null;
    ready = false;
    if (stopped) return;
    detail = `${/[.!?]$/.test(why) ? why : `${why}.`} It is started again in ${Math.round(retryMs / 1000)} s.`;
    log("stt.stopped", { detail: why, retryMs });
    for (const request of waiting.splice(0)) {
      clock.clearTimeout(request.timer);
      request.reject(new Error(why));
    }
    retryTimer = clock.setTimeout(launch, retryMs);
    retryMs = Math.min(retryMs * 2, LONGEST_RETRY_MS);
  }

  /** Sends the next request if the worker is free; it takes one at a time. */
  function pump() {
    if (!ready || !child || waiting.length === 0 || waiting[0].sent) return;
    const request = waiting[0];
    request.sent = true;
    child.stdin.write(JSON.stringify({ id: request.id, pcm: request.pcm.toString("base64") }) + "\n");
  }

  function answer(message) {
    if (waiting.length === 0 || waiting[0].id !== message.id) return;
    const request = waiting.shift();
    clock.clearTimeout(request.timer);
    if (message.device) usedDevice = message.device;
    if (message.error) request.reject(new Error(`Speech-to-text failed: ${message.error}`));
    else request.resolve({ text: String(message.text ?? ""), ms: Number(message.ms) || 0 });
    pump();
  }

  return {
    start() {
      if (!stopped) return;
      stopped = false;
      launch();
    },

    async stop() {
      stopped = true;
      clock.clearTimeout(retryTimer);
      for (const request of waiting.splice(0)) {
        clock.clearTimeout(request.timer);
        request.reject(new Error("The companion is shutting down."));
      }
      const running = child;
      child = null;
      ready = false;
      detail = "Stopped.";
      if (!running) return;
      await new Promise((resolve) => {
        running.once("exit", resolve);
        running.once("error", resolve);
        // The worker ends by itself when its input closes.
        running.stdin.end();
        setTimeout(() => running.kill(), 2000).unref();
      });
    },

    health: () => ({ ready, model, device: usedDevice, detail }),

    transcribe(pcm, { timeoutMs = 20_000 } = {}) {
      return new Promise((resolve, reject) => {
        if (!child) {
          reject(new Error(stopped ? "Speech-to-text is not running." : detail));
          return;
        }
        const request = { id: String(nextId++), pcm, resolve, reject, timer: null, sent: false };
        request.timer = clock.setTimeout(() => {
          const index = waiting.indexOf(request);
          if (index < 0) return;
          waiting.splice(index, 1);
          reject(new Error("Speech-to-text took too long."));
          // A worker that sits on a request is stuck; a new one is started.
          if (request.sent && child) {
            log("stt.stuck", { timeoutMs });
            child.kill();
          }
        }, timeoutMs);
        waiting.push(request);
        pump();
      });
    },
  };
}

/** Calls back for every complete line a stream delivers. */
function lines(stream, handle) {
  let rest = "";
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => {
    rest += chunk;
    let end;
    while ((end = rest.indexOf("\n")) >= 0) {
      const line = rest.slice(0, end).trim();
      rest = rest.slice(end + 1);
      if (line) handle(line);
    }
  });
}
