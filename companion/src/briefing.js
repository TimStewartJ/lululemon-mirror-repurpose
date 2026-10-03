import { CARD, fitRows, oneLine } from "./reply.js";
import { clockHour, clockTime, localDay, minuteOfDay, shortDate, weekday } from "./time.js";

/**
 * The briefing: a headline and a few rows that say how things stand, built
 * from the mirror's state by code. No model is asked, so it is ready as fast
 * as the mirror answers, and it says the same thing every time. It reads the
 * state and changes nothing.
 */

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;
/** The greetings Mirror Home recognises by itself and sends without a recording. */
export const SHORTCUTS = ["good-morning", "good-afternoon", "good-evening", "good-night", "home"];
export const BRIEFING_KINDS = [...SHORTCUTS, "catch-up"];
const HEADLINES = {
  "good-morning": "Good morning",
  "good-afternoon": "Good afternoon",
  "good-evening": "Good evening",
  "good-night": "Good night",
  home: "Welcome home",
  "catch-up": "Here's where things stand",
};
const AWAY_HEADLINE = "While you were away";
const ITEMS_NAMED = 3;
// A title may be 120 characters. Three of them have to share a row of 90.
const TITLE_ROOM = 40;
// What is missed for less than this is told by how long ago, and later by its day and time.
const RELATIVE_FOR_MS = 6 * HOUR_MS;
const RAIN_WORTH_A_WORD = 30;
const RAIN_LIKELY = 50;
const NEXT_WITHIN_MS = 3 * HOUR_MS;
const LAST_BRIEFING_KEPT_MS = 3 * MINUTE_MS;

/** The plain greeting of a briefing, for when there is nothing else to show. */
export function briefingHeadline(kind) {
  return HEADLINES[kind] ?? HEADLINES["catch-up"];
}

/**
 * @typedef {Object} Briefing
 * @property {string} reply The headline.
 * @property {{ label: string, text: string }[]} details The rows under it.
 * @property {number} seconds How long the glass should show it.
 * @property {string[]} missed The ids of the items that are past their time and not done.
 */

/**
 * Builds a briefing from the mirror's state.
 *
 * @param {import("./state.js").MirrorState} state
 * @param {"good-morning"|"good-afternoon"|"good-evening"|"good-night"|"home"|"catch-up"} kind
 * @returns {Briefing}
 */
export function buildBriefing(state, kind) {
  const board = sortBoard(state);
  const weather = readWeather(state);
  const time = (ms) => clockTime(ms, state.offsetMinutes, state.clock24Hour);
  const due = (items) => joined(items.map((item) => `${short(item.title)} ${time(item.due)}`));
  const dueWithDay = (items) => joined(items.map((item) => `${short(item.title)} ${when(item.due, state)}`));
  const missed = joined(board.missed.map((item) => `${short(item.title)}, ${ago(item.due, state)}`));
  const todos = joined(board.todos.map((item) => short(item.title)));
  const rows = [];
  const add = (label, text) => {
    if (text) rows.push({ label, text });
  };
  const idle = board.read && board.coming.length === 0 && board.missed.length === 0 && board.todos.length === 0;

  if (kind === "good-morning") {
    add("Weather", weather && fit(nowAndToday(weather, state)));
    add("Today", due(board.today));
    add("Missed", missed);
    add("To do", todos);
    if (idle) add("Today", "Nothing on your list.");
  } else if (kind === "good-afternoon") {
    add("Weather", weather && fit(nowAndRestOfToday(weather, state)));
    add("Later", due(board.today));
    add("Missed", missed);
    add("To do", todos);
  } else if (kind === "good-evening") {
    add("Weather", weather && fit(nowAndTomorrow(weather)));
    add("Tonight", due(board.today));
    add("Missed", missed);
    add("Tomorrow", due(board.tomorrow));
  } else if (kind === "good-night") {
    const stillOpen = joined([...board.missed, ...board.todos].map((item) => short(item.title)));
    add("Tomorrow", weather && fit(tomorrowOnly(weather, state)));
    add("First up", due(board.tomorrow));
    add("Still open", stillOpen);
    if (board.read && board.tomorrow.length === 0 && !stillOpen) add("Tomorrow", "Nothing planned.");
  } else if (kind === "home") {
    add("Weather", weather && fit([sentence(nowPart(weather))]));
    add("Later", due(board.today));
    add("Missed", missed);
    add("To do", todos);
  } else {
    add("Now", weather && fit(forTheHour(weather, state)));
    add("Next", dueWithDay([...board.today, ...board.tomorrow]));
    add("Missed", missed);
    add("To do", todos);
    // Asked what was missed, an empty answer would look like no answer.
    if (idle) add("Next", "Nothing on your list.");
  }

  const details = fitRows(rows);
  return {
    reply: briefingHeadline(kind),
    details,
    // The display goes dark after a good night, so that one is kept short.
    seconds: Math.min(kind === "good-night" ? 10 : 20, 8 + 2 * details.length),
    missed: board.missed.map((item) => item.id),
  };
}

/**
 * The card for someone who comes back to reminders that fell due while
 * nobody could be told: which they were and, if something is due soon, what.
 *
 * @param {import("./state.js").MirrorState} state
 * @param {Iterable<string>} ids The reminders that were not told.
 * @returns {Briefing | null} null when none of them is still open
 */
export function buildAwayCard(state, ids) {
  const untold = new Set(ids);
  const board = sortBoard(state);
  const missed = board.missed.filter((item) => untold.has(item.id));
  if (missed.length === 0) return null;
  const soon = board.coming.filter((item) => item.due - state.now <= NEXT_WITHIN_MS);
  const details = fitRows([
    { label: "Missed", text: joined(missed.map((item) => `${short(item.title)}, ${ago(item.due, state)}`)) },
    { label: "Next", text: joined(soon.map((item) => `${short(item.title)} ${when(item.due, state)}`)) },
  ]);
  return { reply: AWAY_HEADLINE, details, seconds: 8 + 2 * details.length, missed: missed.map((item) => item.id) };
}

/**
 * The small card for a reminder that falls due while someone can read it.
 *
 * @param {string} title
 * @param {number} due
 * @param {{ offsetMinutes: number, clock24Hour: boolean }} mirrorClock
 * @returns {{ text: string, details: { label: string, text: string }[] }}
 */
export function reminderCard(title, due, { offsetMinutes, clock24Hour }) {
  const text = oneLine(title);
  return {
    text: text.charAt(0).toUpperCase() + text.slice(1),
    details: [{ label: "Reminder", text: `Now, ${clockTime(due, offsetMinutes, clock24Hour)}` }],
  };
}

/**
 * Keeps the last briefing for a few minutes, so that "dismiss those" said
 * after it can be told what "those" were.
 *
 * @param {{ now: () => number }} clock
 */
export function createBriefingMemory(clock) {
  /** @type {{ at: number, reply: string, details: { label: string, text: string }[], missed: { id: string, title: string }[] } | null} */
  let last = null;
  return {
    /**
     * @param {Briefing} briefing
     * @param {import("./state.js").MirrorState} state The state it was built from, for the titles of the missed items.
     */
    note(briefing, state) {
      const titles = new Map(state.items.map((item) => [item.id, item.title]));
      last = {
        at: clock.now(),
        reply: briefing.reply,
        details: briefing.details,
        missed: briefing.missed.filter((id) => titles.has(id)).map((id) => ({ id, title: titles.get(id) })),
      };
    },

    /** The last briefing, or null when there was none in the last three minutes. */
    recall() {
      if (last && clock.now() - last.at >= LAST_BRIEFING_KEPT_MS) last = null;
      return last;
    },

    forget() {
      last = null;
    },
  };
}

/**
 * Sorts the board into what a briefing speaks of. Only reminders and to-dos
 * that are not done count; a note is neither due nor to be done.
 */
function sortBoard(state) {
  const read = state.boardRead !== false;
  const open = (read ? state.items : []).filter((item) => !item.done && (item.kind === "reminder" || item.kind === "todo"));
  const timed = open.filter((item) => typeof item.due === "number");
  const today = localDay(state.now, state.offsetMinutes);
  const dayOf = (item) => localDay(item.due, state.offsetMinutes);
  // The latest miss is the one most worth reading, so it comes first.
  const missed = timed.filter((item) => item.due <= state.now).sort((a, b) => b.due - a.due);
  const coming = timed.filter((item) => item.due > state.now).sort((a, b) => a.due - b.due);
  return {
    read,
    missed,
    coming,
    today: coming.filter((item) => dayOf(item) === today),
    tomorrow: coming.filter((item) => dayOf(item) === today + 1),
    // In the order the mirror lists them: the pressing ones first, then the oldest.
    todos: open.filter((item) => item.kind === "todo" && typeof item.due !== "number"),
  };
}

function short(title) {
  return oneLine(title, TITLE_ROOM);
}

/**
 * Joins up to three entries with a dot between them and counts the rest:
 * "Dentist 3:00 PM · Start dishwasher 9:00 PM +2 more". Fewer are named when
 * three would not fit the row.
 */
function joined(entries) {
  for (let named = Math.min(ITEMS_NAMED, entries.length); named >= 1; named--) {
    const more = entries.length - named;
    const text = entries.slice(0, named).join(" · ") + (more > 0 ? ` +${more} more` : "");
    if (text.length <= CARD.text || named === 1) return text;
  }
  return "";
}

/** "today", "yesterday", "tomorrow", a weekday within the week, or a date. */
function dayWord(ms, state) {
  const days = localDay(ms, state.offsetMinutes) - localDay(state.now, state.offsetMinutes);
  if (days === 0) return "today";
  if (days === -1) return "yesterday";
  if (days === 1) return "tomorrow";
  return Math.abs(days) < 7 ? weekday(ms, state.offsetMinutes) : shortDate(ms, state.offsetMinutes);
}

/** When something is due: its time, with the day in front when that is not today. */
function when(ms, state) {
  const time = clockTime(ms, state.offsetMinutes, state.clock24Hour);
  const day = dayWord(ms, state);
  return day === "today" ? time : `${day} ${time}`;
}

/** How long ago something was due: "2 hours ago", or "yesterday 9:00 PM" when that is long ago. */
function ago(due, state) {
  const passed = state.now - due;
  if (passed >= RELATIVE_FOR_MS) return `${dayWord(due, state)} ${clockTime(due, state.offsetMinutes, state.clock24Hour)}`;
  const minutes = Math.floor(passed / MINUTE_MS);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.round(minutes / 60);
  return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
}

/**
 * Takes from the mirror's weather what a briefing says. Weather the mirror
 * could not refresh is left out altogether: an old forecast told as news is
 * worse than none.
 */
function readWeather(state) {
  const weather = state.weather;
  const data = weather?.data;
  if (!data || weather.stale || weather.state === "unconfigured") return null;
  const today = localDay(state.now, state.offsetMinutes);
  const days = new Map();
  for (const day of data.daily ?? []) {
    // A day's entry is stamped at its start; noon is safely inside it.
    days.set(localDay(Number(day.time) + 12 * HOUR_MS, state.offsetMinutes), {
      high: degrees(day.high),
      low: degrees(day.low),
      chance: number(day.precipitationProbability),
      condition: words(day.condition),
    });
  }
  return {
    temperature: degrees(data.current?.temperature),
    condition: words(data.current?.condition),
    today: days.get(today) ?? null,
    tomorrow: days.get(today + 1) ?? null,
    hours: (data.hourly ?? [])
      .map((hour) => ({ time: Number(hour.time), chance: number(hour.precipitationProbability) }))
      .filter((hour) => Number.isFinite(hour.time) && hour.chance !== null)
      .sort((a, b) => a.time - b.time),
  };
}

function number(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** A temperature as the glass writes it, "62°", in whatever unit the mirror's weather uses. */
function degrees(value) {
  return number(value) === null ? null : `${Math.round(value) || 0}°`;
}

function words(value) {
  return typeof value === "string" && value.trim() ? oneLine(value, 40) : null;
}

const capital = (text) => text.charAt(0).toUpperCase() + text.slice(1);
const small = (text) => text.charAt(0).toLowerCase() + text.slice(1);

/** Makes a sentence of the parts that are known, or nothing when none is. */
function sentence(...parts) {
  const known = parts.filter(Boolean);
  return known.length > 0 ? `${capital(known.join(", "))}.` : "";
}

/** As many of the sentences, from the first, as one row holds. */
function fit(sentences) {
  let text = "";
  for (const next of sentences.filter(Boolean)) {
    const longer = text ? `${text} ${next}` : next;
    if (longer.length > CARD.text) break;
    text = longer;
  }
  return text;
}

/** "Clear, 62° now" */
function nowPart(weather) {
  const parts = [weather.condition, weather.temperature].filter(Boolean);
  return parts.length > 0 ? `${parts.join(", ")} now` : "";
}

/** The start of the mirror's calendar day that begins `daysAhead` days after today. */
function dayStart(state, daysAhead) {
  return (localDay(state.now, state.offsetMinutes) + daysAhead) * DAY_MS - state.offsetMinutes * MINUTE_MS;
}

/**
 * What to say of rain between two moments: "no rain", "rain likely by 3 PM",
 * or "70% chance of rain". The hours of the forecast say when; where they
 * do not reach to the end, the day's own figure says how likely.
 *
 * @param {object} weather
 * @param {object|null} day The day's entry the moments lie in.
 * @param {number} from
 * @param {number} until
 * @param {import("./state.js").MirrorState} state
 */
function rain(weather, day, from, until, state) {
  const hours = weather.hours.filter((hour) => hour.time > from && hour.time < until);
  const reaches = hours.length > 0 && hours.at(-1).time >= until - HOUR_MS;
  const chance = reaches ? Math.max(...hours.map((hour) => hour.chance)) : day?.chance ?? null;
  if (chance === null) return "";
  const falling = /snow/i.test(day?.condition ?? "") ? "snow" : "rain";
  if (chance < RAIN_WORTH_A_WORD) return `no ${falling}`;
  const likely = hours.find((hour) => hour.chance >= RAIN_LIKELY);
  if (likely) return `${falling} likely by ${clockHour(likely.time, state.offsetMinutes, state.clock24Hour)}`;
  return `${Math.round(chance)}% chance of ${falling}`;
}

/** A day's low is reached near dawn, so the low of the coming night stands in tomorrow's entry. */
function lowTonight(weather) {
  const low = weather.tomorrow?.low ?? weather.today?.low;
  return low ? `low ${low} tonight` : "";
}

/** "Clear, 62° now. High 91°, no rain." */
function nowAndToday(weather, state) {
  const high = weather.today?.high ? `high ${weather.today.high}` : "";
  return [sentence(nowPart(weather)), sentence(high, rain(weather, weather.today, state.now, dayStart(state, 1), state))];
}

/** "Overcast, 14° now. Low 9° tonight, rain likely by 5 PM." */
function nowAndRestOfToday(weather, state) {
  return [sentence(nowPart(weather)), sentence(lowTonight(weather), rain(weather, weather.today, state.now, dayStart(state, 1), state))];
}

/** "Clear, 70° now, low 58° tonight. Tomorrow mostly clear, high 85°." */
function nowAndTomorrow(weather) {
  const tomorrow = weather.tomorrow;
  const ahead = [tomorrow?.condition ? small(tomorrow.condition) : "", tomorrow?.high ? `high ${tomorrow.high}` : ""].filter(Boolean);
  return [sentence(nowPart(weather), lowTonight(weather)), ahead.length > 0 ? `Tomorrow ${ahead.join(", ")}.` : ""];
}

/** "Mostly clear, high 85°, low 58°, no rain." */
function tomorrowOnly(weather, state) {
  const tomorrow = weather.tomorrow;
  if (!tomorrow) return [];
  return [
    sentence(
      tomorrow.condition,
      tomorrow.high ? `high ${tomorrow.high}` : "",
      tomorrow.low ? `low ${tomorrow.low}` : "",
      rain(weather, tomorrow, dayStart(state, 1), dayStart(state, 2), state),
    ),
  ];
}

/** The weather that matters at this hour: the day ahead in the morning, the night in the afternoon, tomorrow in the evening. */
function forTheHour(weather, state) {
  const hour = minuteOfDay(state.now, state.offsetMinutes) / 60;
  if (hour < 12) return nowAndToday(weather, state);
  if (hour < 17) return nowAndRestOfToday(weather, state);
  return nowAndTomorrow(weather);
}
