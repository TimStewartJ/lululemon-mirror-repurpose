import { BrainError } from "./brain.js";
import { buildAwayCard, buildBriefing, createBriefingMemory, reminderCard } from "./briefing.js";
import { describeError } from "./log.js";
import { greetingMessage, greetingSystem, tendingMessage, tendingSystem } from "./prompt.js";
import { oneLine } from "./reply.js";
import { fetchState } from "./state.js";
import { inQuietHours, localDay, localTime, minuteOfDay } from "./time.js";
import { newTurn, toolsFor } from "./tools.js";

const WATCH_EVERY_MS = 30_000;
const GREET_AT_MOST_EVERY_MS = 45 * 60_000;
const GREET_AFTER_ASLEEP_SECONDS = 10 * 60;
const GREET_WITHIN_MS = 15_000;
// The morning briefing greets the first person of the day between these hours of the mirror's clock.
const MORNING_FROM_MINUTE = 5 * 60;
const MORNING_UNTIL_MINUTE = 11 * 60;
// Someone who only stepped aside for a moment has not been away.
const CATCH_UP_AFTER_ASLEEP_SECONDS = 60;
const REMINDER_SHOWN_SECONDS = 20;
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
 * with the morning's briefing or with what they missed where there is
 * either, show a card when a reminder falls due, and tend the display now
 * and then.
 *
 * @param {Object} parts
 * @param {{ greet: boolean, morningBriefing?: boolean, reminders: boolean, tend: boolean, tendMinutes: number, quietHours: [string, string] | null }} parts.settings
 * @param {import("./brain.js").Brain} parts.brain
 * @param {import("./mirror.js").Mirror} parts.mirror
 * @param {import("./tools.js").Tool[]} parts.tools
 * @param {ReturnType<import("./memory.js").createMemory>} parts.memory
 * @param {ReturnType<import("./activity.js").createActivity>} parts.activity
 * @param {ReturnType<import("./queue.js").createQueue>} parts.queue
 * @param {ReturnType<import("./briefing.js").createBriefingMemory>} [parts.briefings] The last briefing shown, shared
 *   with the part that answers people, so that "dismiss those" works after a card shown here.
 * @param {(event: string, fields?: object) => void} parts.log
 * @param {import("./clock.js").Clock} parts.clock
 */
export function createProactive({ settings, brain, mirror, tools, memory, activity, queue, log, clock, briefings = createBriefingMemory(clock) }) {
  let watchTimer = null;
  let tendTimer = null;
  let stopped = true;
  let lastGreetingAt = null;
  /** The mirror's calendar day on which the morning briefing was last shown. */
  let briefedDay = null;
  /** How the mirror's clock reads, from the last status seen: { offsetMinutes, clock24Hour }. */
  let mirrorClock = null;
  /**
   * The ids of reminders that fell due while nobody could be told: the
   * display was dark, it was a quiet hour, or the card could not be shown.
   * Kept in memory only; after a restart the briefing still names them,
   * since it reads the board.
   */
  const untold = new Set();
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
    // What was done or removed meanwhile no longer needs telling.
    for (const id of untold) {
      if (!pending.has(id)) untold.delete(id);
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
    mirrorClock = { offsetMinutes, clock24Hour: Boolean(status.clock24Hour) };
    for (const [id, item] of pending) {
      const key = `${id}:${item.due}`;
      if (item.due > now || announced.has(key)) continue;
      announced.add(key);
      // Whoever comes next is told of a reminder that nobody was told of in its minute.
      if (now - item.due > STALE_REMINDER_MS) {
        untold.add(id);
        continue;
      }
      if (quiet(now, offsetMinutes)) {
        untold.add(id);
        log("reminder.quiet", { id, title: item.title });
        continue;
      }
      const card = reminderCard(item.title, item.due, mirrorClock);
      const entry = { at: clock.now(), source: "reminder", reply: card.text, details: card.details, acted: ["say"] };
      try {
        // An older Mirror Home shows the text alone, so the text is the reminder itself.
        const answer = await mirror.call("POST", "/api/v1/assistant/say", {
          body: { text: card.text, kind: "notice", seconds: REMINDER_SHOWN_SECONDS, details: card.details },
        });
        if (!answer.shown) entry.reason = answer.reason || "not-shown";
      } catch (error) {
        entry.error = describeError(error);
      }
      if (entry.reason || entry.error) untold.add(id);
      activity.add(entry);
      log("reminder", { id, title: item.title, shown: !entry.reason && !entry.error, reason: entry.reason, error: entry.error });
    }
  }

  /** Why someone who walked up is not greeted by the model, or null when they may be. */
  function noGreeting(event, received) {
    if (!settings.greet) return "off";
    if (!(Number(event.asleepSeconds) >= GREET_AFTER_ASLEEP_SECONDS)) return "asleep-too-short";
    if (lastGreetingAt !== null && received - lastGreetingAt < GREET_AT_MOST_EVERY_MS) return "greeted-recently";
    return null;
  }

  /** The greeting the model writes, or decides not to write. */
  async function modelGreeting(event, received, state, signal) {
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
  }

  /** Reads the mirror for something to show by itself; a string says why nothing is shown. */
  async function readForShowing() {
    let state;
    try {
      state = await fetchState(mirror, clock);
    } catch (error) {
      return `mirror-unreachable: ${describeError(error)}`;
    }
    return quiet(state.now, state.offsetMinutes) ? "quiet-hours" : state;
  }

  /**
   * Whether it is surely not the moment for the morning briefing, judged
   * without asking the mirror, from how its clock read at the last look.
   */
  function surelyNoMorning(received) {
    if (!mirrorClock) return false;
    const now = received + skew;
    const minute = minuteOfDay(now, mirrorClock.offsetMinutes);
    return minute < MORNING_FROM_MINUTE || minute >= MORNING_UNTIL_MINUTE || localDay(now, mirrorClock.offsetMinutes) === briefedDay;
  }

  /**
   * Shows a card that code built, and records it.
   *
   * @returns {Promise<boolean>} whether the glass showed it
   */
  async function showCard(received, card, state) {
    const entry = { at: received, source: "presence", reply: card.reply, details: card.details, acted: ["say"] };
    try {
      const answer = await mirror.call("POST", "/api/v1/assistant/say", {
        body: { text: card.reply, kind: "notice", seconds: card.seconds, details: card.details },
      });
      if (!answer.shown) entry.reason = answer.reason || "not-shown";
    } catch (error) {
      entry.error = describeError(error);
    }
    entry.ms = clock.now() - received;
    activity.add(entry);
    if (entry.reason || entry.error) return false;
    briefings.note(card, state);
    return true;
  }

  /**
   * Someone came in front of the mirror and the display woke. At most one
   * thing is shown: the morning's briefing to the first person of the day,
   * or else what fell due while nobody could be told, or else the model's
   * greeting.
   *
   * @param {{ asleepSeconds?: number }} event
   * @returns {Promise<string>} what came of it, for the log and the tests
   */
  async function presence(event) {
    const received = clock.now();
    const asleep = Number(event.asleepSeconds);
    const mayBrief = Boolean(settings.morningBriefing) && asleep >= GREET_AFTER_ASLEEP_SECONDS && !surelyNoMorning(received);
    const mayCatchUp = untold.size > 0 && asleep >= CATCH_UP_AFTER_ASLEEP_SECONDS;
    if (!mayBrief && !mayCatchUp) return greet(event);
    const outcome = await queue.proactive(async (signal) => {
      const state = await readForShowing();
      if (typeof state === "string") return state;
      const minute = minuteOfDay(state.now, state.offsetMinutes);
      const today = localDay(state.now, state.offsetMinutes);
      if (mayBrief && minute >= MORNING_FROM_MINUTE && minute < MORNING_UNTIL_MINUTE && today !== briefedDay) {
        const briefing = buildBriefing(state, "good-morning");
        // With nothing to tell, the greeting is left to the model, which may also stay silent.
        if (briefing.details.length > 0) {
          if (!(await showCard(received, briefing, state))) return "not-shown";
          briefedDay = today;
          // It is the greeting of this hour, and its rows name what was missed.
          lastGreetingAt = received;
          untold.clear();
          return "briefed";
        }
      }
      if (mayCatchUp) {
        const card = buildAwayCard(state, untold);
        if (card) {
          if (!(await showCard(received, card, state))) return "not-shown";
          untold.clear();
          return "caught-up";
        }
      }
      return noGreeting(event, received) ?? modelGreeting(event, received, state, signal);
    });
    return outcome.skipped ? "busy" : outcome.value;
  }

  /**
   * Greets someone who walked up with a line the model writes, if it finds one worth reading.
   *
   * @param {{ asleepSeconds?: number }} event
   * @returns {Promise<string>} what came of it, for the log and the tests
   */
  async function greet(event) {
    const received = clock.now();
    const refusal = noGreeting(event, received);
    if (refusal) return refusal;
    const outcome = await queue.proactive(async (signal) => {
      const state = await readForShowing();
      return typeof state === "string" ? state : modelGreeting(event, received, state, signal);
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
        presence(event).then(
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
    presence,
    greet,
    tend,
  };
}
