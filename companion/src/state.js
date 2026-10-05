import { COORDINATES, describeWidget } from "./layout.js";
import { MirrorUnreachable } from "./mirror.js";
import { formatOffset, localIso, localTime, weekday } from "./time.js";
import { describeCharacter, describePlace } from "./tools/character.js";

const MAX_BOARD_ITEMS = 25;
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * What a fetch of the mirror's state yields: the compact snapshot the model
 * reads, and the few raw facts the rest of the companion needs.
 *
 * @typedef {Object} MirrorState
 * @property {object} snapshot
 * @property {number} now The mirror's own clock, in milliseconds.
 * @property {number} offsetMinutes The mirror's offset from UTC.
 * @property {boolean} sleeping
 * @property {number} boardVersion
 * @property {object[]} items Every item on the board, as the mirror lists them.
 * @property {boolean} boardRead False when the board could not be read, so that `items` says nothing.
 * @property {boolean} clock24Hour Whether the mirror's clock shows 15:00 and not 3:00 PM.
 * @property {object|null} weather The weather as the mirror's status has it: its state, whether it is stale, and its data.
 * @property {{ days: number } | null} restart Set when the mirror asks to be switched off and on, with the days it has been up.
 */

/**
 * Asks the mirror for everything the snapshot needs, all at once.
 *
 * @param {import("./mirror.js").Mirror} mirror
 * @param {{ now: () => number }} clock
 * @param {{ brief?: boolean }} [options] With `brief`, only the status and the board are read: all that a
 *   briefing is built from, and three requests fewer for an answer that has to be fast. The snapshot then
 *   lacks the layout, the films, the character and the place of the answers.
 * @returns {Promise<MirrorState>}
 * @throws {MirrorUnreachable} when the mirror's status cannot be read
 */
export async function fetchState(mirror, clock, { brief = false } = {}) {
  const asked = clock.now();
  const [status, layout, board, films, assistant] = await Promise.allSettled([
    mirror.get("/api/v1/status"),
    brief ? null : mirror.get("/api/v1/dashboard/layout"),
    mirror.get("/api/v1/board/items?limit=100"),
    brief ? null : mirror.get("/api/v1/background-videos"),
    brief ? null : mirror.get("/api/v1/assistant"),
  ]);
  if (status.status === "rejected") {
    if (status.reason instanceof MirrorUnreachable) throw status.reason;
    throw new MirrorUnreachable(status.reason?.message || "The mirror's status could not be read.");
  }
  // Due times are judged by the mirror's clock, so "now" is taken from it
  // where it says; the two clocks may differ by some seconds.
  const now = Number(board.value?.now) || clock.now() + Math.round((clock.now() - asked) / 2);
  return buildState({
    status: status.value,
    layout: layout.value ?? null,
    board: board.value ?? null,
    films: films.value ?? null,
    assistant: assistant.value ?? null,
    now,
  });
}

/** Builds the state from the mirror's answers; kept apart from the fetching so it can be tested by itself. */
export function buildState({ status, layout, board, films, assistant = null, now }) {
  const offsetMinutes = Number(status.utcOffsetMinutes) || 0;
  const automation = status.automation ?? {};
  const widgets = Array.isArray(layout?.widgets) ? layout.widgets : [];
  const items = Array.isArray(board?.items) ? board.items : [];
  const snapshot = {
    name: status.displayName || undefined,
    now: describeNow(status, now, offsetMinutes),
    display: describeDisplay(automation),
    layoutUnits: COORDINATES,
    widgets: widgets.map(describeWidget),
    background: describeBackground(layout, films),
    board: describeBoard(items, widgets, offsetMinutes, board === null),
    weather: describeWeather(status.weather, now, offsetMinutes),
    voice: status.voice?.state ?? "unknown",
  };
  // Only a Mirror Home that has characters is asked about them.
  const character = describeCharacter(assistant);
  if (character) snapshot.character = character;
  const answersAt = describePlace(assistant);
  if (answersAt) snapshot.answersAt = answersAt;
  if (!layout) snapshot.widgets = "The layout could not be read.";
  return {
    snapshot,
    now,
    offsetMinutes,
    sleeping: Boolean(automation.sleeping),
    boardVersion: Number(board?.version ?? status.boardVersion ?? 0),
    items,
    boardRead: board !== null,
    clock24Hour: Boolean(status.clock24Hour),
    weather: status.weather ?? null,
    // A mirror that runs short of memory says so in its status; only a person can help it.
    restart: status.restart?.advised ? { days: Math.round((Number(status.deviceUptimeSeconds) || 0) / 86_400) } : null,
  };
}

function describeNow(status, now, offsetMinutes) {
  const seen = {
    local: localIso(now, offsetMinutes),
    weekday: weekday(now, offsetMinutes),
    zone: status.timeZone || "unknown",
    utcOffset: formatOffset(offsetMinutes),
    clock: status.clock24Hour ? "24-hour" : "12-hour",
  };
  const change = status.nextUtcOffsetChange;
  if (change && Number(change.at) - now < 2 * WEEK_MS) {
    const after = Number(change.utcOffsetMinutes);
    seen.offsetChange = `From ${localIso(Number(change.at), after)} the offset is ${formatOffset(after)}.`;
  }
  return seen;
}

function describeDisplay(automation) {
  const seen = { power: automation.sleeping ? "asleep" : "awake" };
  if (automation.sleeping) seen.asleepBecause = automation.sleepReason || "unknown";
  if (automation.manualOverride) seen.heldFourHours = true;
  seen.wakeBrightness = automation.wakeBrightness;
  return { ...seen, ...describeRules(automation) };
}

/** When the display is dark by itself: its awake hours, and whether it sleeps when it sees nobody. */
export function describeRules(automation) {
  const seen = {};
  if (automation.ambientEnabled && automation.ambientLightAvailable) seen.brightnessFollowsRoomLight = true;
  seen.awakeHours = automation.enabled ? `${automation.wakeTime} to ${automation.sleepTime}` : "always";
  seen.sleepsWhenNobodyIsThere = automation.motionEnabled ? `after ${describeStay(automation.motionTimeoutSeconds)}` : "no";
  if (automation.motionEnabled) seen.movementSensitivity = automation.motionSensitivity;
  return seen;
}

/** 300 seconds are "5 minutes"; 45 are "45 seconds". */
function describeStay(seconds) {
  if (seconds % 60 !== 0) return `${seconds} seconds`;
  return seconds === 60 ? "1 minute" : `${seconds / 60} minutes`;
}

function describeBackground(layout, films) {
  const seen = { mode: layout?.background?.mode ?? "unknown" };
  if (seen.mode === "photo") seen.photo = layout.background.photo;
  if (!films) return seen;
  seen.films = (films.videos ?? []).map((video) => {
    const film = { id: shortId(video.id), name: video.name };
    if (video.showing && seen.mode === "video") film.showing = true;
    return film;
  });
  const schedule = films.schedule;
  const timetable = (schedule?.slots ?? []).map((slot) => `${slot.start} ${shortId(slot.videoId)}`).join(", ");
  if (schedule?.active) {
    seen.filmSchedule = timetable;
    if (schedule.hold) seen.filmHeldUntil = schedule.hold.untilTime;
  } else if (timetable) {
    // A timetable that is kept but not followed, so that it can be turned on again or added to.
    seen.filmScheduleSwitchedOff = timetable;
  }
  return seen;
}

/** The first eight characters of a film's id, which is a SHA-256 and far too long to read. */
export function shortId(id) {
  return String(id ?? "").slice(0, 8);
}

function describeBoard(items, widgets, offsetMinutes, unreadable) {
  if (unreadable) return { note: "The board could not be read." };
  const seen = {
    onTheGlass: widgets.some((widget) => widget.type === "board" && widget.visible),
    items: items.slice(0, MAX_BOARD_ITEMS).map((item) => describeItem(item, offsetMinutes)),
  };
  if (items.length > MAX_BOARD_ITEMS) seen.more = items.length - MAX_BOARD_ITEMS;
  return seen;
}

/** A board item as the model sees it, with its due time on the mirror's wall clock. */
export function describeItem(item, offsetMinutes) {
  const seen = { id: item.id, kind: item.kind, title: item.title };
  if (item.body) seen.body = item.body.length > 80 ? item.body.slice(0, 77) + "..." : item.body;
  if (typeof item.due === "number") seen.due = localIso(item.due, offsetMinutes);
  seen.state = item.state;
  if (item.priority && item.priority !== "normal") seen.priority = item.priority;
  if (item.done && typeof item.doneAt === "number") seen.doneAt = localIso(item.doneAt, offsetMinutes);
  return seen;
}

function describeWeather(weather, now, offsetMinutes) {
  if (!weather || weather.state === "unconfigured") return { note: "No weather is set up on the mirror." };
  const data = weather.data;
  if (!data) return { note: `The mirror has no weather yet (${weather.state}).` };
  const unit = data.units?.temperature ?? "";
  const today = localIso(now, offsetMinutes).slice(0, 10);
  const tomorrow = localIso(now + 24 * 60 * 60 * 1000, offsetMinutes).slice(0, 10);
  const seen = { place: data.locationName || undefined, unit };
  if (weather.stale) seen.note = "This weather is old; the mirror could not refresh it.";
  seen.now = {
    temp: Math.round(data.current?.temperature),
    feelsLike: Math.round(data.current?.apparentTemperature),
    condition: data.current?.condition,
  };
  for (const day of data.daily ?? []) {
    // A day's entry is stamped at its start; noon is safely inside it.
    const date = localIso(Number(day.time) + 12 * 60 * 60 * 1000, offsetMinutes).slice(0, 10);
    const key = date === today ? "today" : date === tomorrow ? "tomorrow" : null;
    if (!key) continue;
    seen[key] = {
      high: Math.round(day.high),
      low: Math.round(day.low),
      rainChance: day.precipitationProbability,
      condition: day.condition,
    };
    if (key === "today") seen.today.sunset = localTime(Number(day.sunset), offsetMinutes);
  }
  seen.nextHours = (data.hourly ?? [])
    .filter((hour) => Number(hour.time) > now)
    .slice(0, 6)
    .map((hour) => ({
      at: localTime(Number(hour.time), offsetMinutes),
      temp: Math.round(hour.temperature),
      rainChance: hour.precipitationProbability,
      condition: hour.condition,
    }));
  return seen;
}
