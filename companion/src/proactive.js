import { BrainError } from "./brain.js";
import { describeError } from "./log.js";
import { greetingMessage, greetingSystem, tendingMessage, tendingSystem } from "./prompt.js";
import { oneLine } from "./reply.js";
import { fetchState } from "./state.js";
import { inQuietHours, localTime } from "./time.js";
import { newTurn, toolsFor } from "./tools.js";

const WATCH_EVERY_MS = 30_000;
const GREET_AT_MOST_EVERY_MS = 45 * 60_000;
const GREET_AFTER_ASLEEP_SECONDS = 10 * 60;
const GREET_WITHIN_MS = 15_000;
const TEND_TIMEOUT_MS = 30_000;
const TEND_LOOKS_BACK_MS = 12 * 60 * 60_000;
// A reminder found already this long overdue is old news, for example
// after the companion was off for a while; it is not announced.
const STALE_REMINDER_MS = 5 * 60_000;
// Which of a person's tools make a part of the display theirs, so that a
// tending run is not even given the tool that could change it.
const TOUCHES = {
  set_background: ["set_background"],
  arrange_widgets: ["arrange_widgets"],
  board_remove: ["board_add", "board_update", "board_remove"],
};

/**
 * What the companion does of its own accord: greet someone who walks up,
 * show a caption when a reminder falls due, and tend the display now and then.
 *
 * @param {Object} parts
 * @param {{ greet: boolean, reminders: boolean, tend: boolean, tendMinutes: number, quietHours: [string, string] | null }} parts.settings
 * @param {import("./brain.js").Brain} parts.brain
 * @param {import("./mirror.js").Mirror} parts.mirror
 * @param {import("./tools.js").Tool[]} parts.tools
 * @param {ReturnType<import("./memory.js").createMemory>} parts.memory
 * @param {ReturnType<import("./activity.js").createActivity>} parts.activity
 * @param {ReturnType<import("./queue.js").createQueue>} parts.queue
 * @param {(event: string, fields?: object) => void} parts.log
 * @param {import("./clock.js").Clock} parts.clock
 */
export function createProactive({ settings, brain, mirror, tools, memory, activity, queue, log, clock }) {
  let watchTimer = null;
  let tendTimer = null;
  let stopped = true;
  let lastGreetingAt = null;
  /** Board items with a due time that are not done: id to { due, title }. */
  let pending = new Map();
  let boardVersion = null;
  /** The difference between the mirror's clock and this machine's, in milliseconds. */
  let skew = 0;
  /** Reminders already announced or passed over, as "id:due". */
  const announced = new Set();

  const quiet = (now, offsetMinutes) => inQuietHours(now, offsetMinutes, settings.quietHours);

  /** Reads the board when it has changed, and remembers what is due when. */
  async function refreshBoard(version) {
    if (version === boardVersion) return;
    const board = await mirror.get("/api/v1/board/items?limit=100");
    if (typeof board.now === "number") skew = board.now - clock.now();
    const first = boardVersion === null;
    pending = new Map();
    for (const item of board.items ?? []) {
      if (item.kind === "note" || item.done || typeof item.due !== "number") continue;
      pending.set(item.id, { due: item.due, title: item.title });
      // What is overdue when the companion first looks was due before it
      // was watching, and the board shows it as overdue anyway.
      if (first && item.due <= clock.now() + skew) announced.add(`${item.id}:${item.due}`);
    }
    for (const key of announced) {
      if (!pending.has(key.slice(0, key.lastIndexOf(":")))) announced.delete(key);
    }
    boardVersion = board.version ?? version;
  }

  /**
   * One look at the mirror: is it there, and has a reminder fallen due? No
   * model is involved.
   */
  async function checkReminders() {
    let status;
    try {
      status = await mirror.get("/api/v1/status");
    } catch {
      return;
    }
    if (!settings.reminders) return;
    try {
      await refreshBoard(status.boardVersion);
    } catch (error) {
      log("reminders.board_unread", { detail: describeError(error) });
      return;
    }
    const now = clock.now() + skew;
    const offsetMinutes = Number(status.utcOffsetMinutes) || 0;
    for (const [id, item] of pending) {
      const key = `${id}:${item.due}`;
      if (item.due > now || announced.has(key)) continue;
      announced.add(key);
      if (now - item.due > STALE_REMINDER_MS) continue;
      if (quiet(now, offsetMinutes)) {
        log("reminder.quiet", { id, title: item.title });
        continue;
      }
      const text = oneLine(`Reminder: ${item.title}`);
      const entry = { at: clock.now(), source: "reminder", reply: text, acted: ["say"] };
      try {
        const answer = await mirror.call("POST", "/api/v1/assistant/say", { body: { text, kind: "notice", seconds: 15 } });
        if (!answer.shown) entry.reason = answer.reason || "not-shown";
      } catch (error) {
        entry.error = describeError(error);
      }
      activity.add(entry);
      log("reminder", { id, title: item.title, shown: !entry.reason && !entry.error, reason: entry.reason, error: entry.error });
    }
  }

  /**
   * Someone came in front of the mirror and the display woke.
   *
   * @param {{ asleepSeconds?: number }} event
   * @returns {Promise<string>} what came of it, for the log and the tests
   */
  async function greet(event) {
    const received = clock.now();
    if (!settings.greet) return "off";
    if (!(Number(event.asleepSeconds) >= GREET_AFTER_ASLEEP_SECONDS)) return "asleep-too-short";
    if (lastGreetingAt !== null && received - lastGreetingAt < GREET_AT_MOST_EVERY_MS) return "greeted-recently";
    const outcome = await queue.proactive(async (signal) => {
      let state;
      try {
        state = await fetchState(mirror, clock);
      } catch (error) {
        return `mirror-unreachable: ${describeError(error)}`;
      }
      if (quiet(state.now, state.offsetMinutes)) return "quiet-hours";
      const left = received + GREET_WITHIN_MS - clock.now();
      if (left < 3000) return "too-late";
      lastGreetingAt = received;
      const turn = newTurn("greeting", state, clock.now());
      turn.deadline = received + GREET_WITHIN_MS;
      const entry = { at: received, source: "presence" };
      try {
        await brain.run({
          session: "proactive",
          system: greetingSystem(memory.lines()),
          prompt: greetingMessage({ asleepSeconds: event.asleepSeconds, snapshot: state.snapshot }),
          tools: toolsFor("greeting", tools),
          turn,
          timeoutMs: left,
          signal,
        });
      } catch (error) {
        entry.error = `${error instanceof BrainError ? error.kind : "failed"}: ${describeError(error)}`;
      }
      turn.closed = true;
      entry.reply = turn.said || "";
      entry.acted = turn.acted;
      entry.ms = clock.now() - received;
      activity.add(entry);
      return entry.error ? `failed: ${entry.error}` : turn.said ? "greeted" : "silent";
    });
    return outcome.skipped ? "busy" : outcome.value;
  }

  /**
   * Looks over the display and may improve one small thing.
   *
   * @returns {Promise<string>} what came of it
   */
  async function tend() {
    if (!settings.tend) return "off";
    const outcome = await queue.proactive(async (signal) => {
      const started = clock.now();
      let state;
      try {
        state = await fetchState(mirror, clock);
      } catch (error) {
        return `mirror-unreachable: ${describeError(error)}`;
      }
      if (state.sleeping) return "asleep";
      if (quiet(state.now, state.offsetMinutes)) return "quiet-hours";
      const requests = activity
        .since(started - TEND_LOOKS_BACK_MS)
        .filter((entry) => ["voice", "controls", "test"].includes(entry.source) && !entry.ignored)
        .map((entry) => ({
          time: localTime(entry.at + skew, state.offsetMinutes),
          heard: entry.heard,
          acted: entry.acted,
        }));
      // The model is told to leave recent requests alone, and is also not
      // handed the tools that would undo them.
      const used = new Set(requests.flatMap((request) => request.acted));
      const allowed = toolsFor("tend", tools).filter((tool) => !(TOUCHES[tool.name] ?? []).some((name) => used.has(name)));
      if (!allowed.some((tool) => tool.changes)) return "all-recently-asked-for";
      const turn = newTurn("tend", state, clock.now());
      const entry = { at: started, source: "tend" };
      try {
        const run = await brain.run({
          session: "proactive",
          system: tendingSystem(memory.lines()),
          prompt: tendingMessage({ requests, snapshot: state.snapshot }),
          tools: allowed,
          turn,
          timeoutMs: TEND_TIMEOUT_MS,
          signal,
        });
        entry.reply = oneLine(run.text);
      } catch (error) {
        entry.error = `${error instanceof BrainError ? error.kind : "failed"}: ${describeError(error)}`;
      }
      turn.closed = true;
      entry.acted = turn.acted;
      entry.ms = clock.now() - started;
      // An hourly "nothing to do" would crowd people's requests out of the list.
      if (turn.changes > 0 || entry.error) activity.add(entry);
      log("tend", { acted: turn.acted, changed: turn.changes, reply: entry.reply, error: entry.error });
      return entry.error ? `failed: ${entry.error}` : turn.changes > 0 ? "changed" : "nothing";
    });
    return outcome.skipped ? "busy" : outcome.value;
  }

  function watch() {
    watchTimer = null;
    if (stopped) return;
    checkReminders()
      .catch((error) => log("reminders.failed", { detail: describeError(error) }))
      .finally(() => {
        if (!stopped) watchTimer = clock.setTimeout(watch, WATCH_EVERY_MS);
      });
  }

  function tendLater() {
    if (stopped || !settings.tend) return;
    tendTimer = clock.setTimeout(() => {
      tendTimer = null;
      tend()
        .catch((error) => log("tend.failed", { detail: describeError(error) }))
        .finally(tendLater);
    }, settings.tendMinutes * 60_000);
  }

  return {
    start() {
      if (!stopped) return;
      stopped = false;
      watch();
      tendLater();
    },

    stop() {
      stopped = true;
      clock.clearTimeout(watchTimer);
      clock.clearTimeout(tendTimer);
    },

    /**
     * Takes an event from the mirror. Returns at once; the work goes on by itself.
     *
     * @param {{ type: string, asleepSeconds?: number }} event
     */
    event(event) {
      if (event.type === "presence") {
        greet(event).then(
          (outcome) => log("greeting", { outcome, asleepSeconds: event.asleepSeconds }),
          (error) => log("greeting.failed", { detail: describeError(error) }),
        );
      } else if (event.type === "started") {
        log("mirror.started", {});
        // Mirror Home has restarted, so what was known of its board may be out of date.
        boardVersion = boardVersion === null ? null : -1;
      }
    },

    checkReminders,
    greet,
    tend,
  };
}
