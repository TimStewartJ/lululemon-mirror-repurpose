import { COORDINATES, describeWidget } from "./layout.js";
import { MirrorUnreachable } from "./mirror.js";
import { formatOffset, localIso, localTime, weekday } from "./time.js";

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
 */

/**
 * Asks the mirror for everything the snapshot needs, all at once.
 *
 * @param {import("./mirror.js").Mirror} mirror
 * @param {{ now: () => number }} clock
 * @returns {Promise<MirrorState>}
 * @throws {MirrorUnreachable} when the mirror's status cannot be read
 */
export async function fetchState(mirror, clock) {
  const asked = clock.now();
  const [status, layout, board, films] = await Promise.allSettled([
    mirror.get("/api/v1/status"),
    mirror.get("/api/v1/dashboard/layout"),
    mirror.get("/api/v1/board/items?limit=100"),
    mirror.get("/api/v1/background-videos"),
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
    now,
  });
}

/** Builds the state from the mirror's answers; kept apart from the fetching so it can be tested by itself. */
export function buildState({ status, layout, board, films, now }) {
  const offsetMinutes = Number(status.utcOffsetMinutes) || 0;
  const automation = status.automation ?? {};
  const widgets = Array.isArray(layout?.widgets) ? layout.widgets : [];
  const items = Array.isArray(board?.items) ? board.items : [];
  const snapshot = {
    now: describeNow(status, now, offsetMinutes),
    display: describeDisplay(automation),
    layoutUnits: COORDINATES,
    widgets: widgets.map(describeWidget),
    background: describeBackground(layout, films),
    board: describeBoard(items, widgets, offsetMinutes, board === null),
    weather: describeWeather(status.weather, now, offsetMinutes),
    voice: status.voice?.state ?? "unknown",
  };
  if (!layout) snapshot.widgets = "The layout could not be read.";
  return {
    snapshot,
    now,
    offsetMinutes,
    sleeping: Boolean(automation.sleeping),
    boardVersion: Number(board?.version ?? status.boardVersion ?? 0),
    items,
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
  if (automation.ambientEnabled && automation.ambientLightAvailable) seen.brightnessFollowsRoomLight = true;
  seen.awakeHours = automation.enabled ? `${automation.wakeTime} to ${automation.sleepTime}` : "always";
  seen.sleepsWhenNobodyIsThere = automation.motionEnabled
    ? `after ${automation.motionTimeoutSeconds} seconds`
    : "no";
  return seen;
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
  if (schedule?.active) {
    seen.filmSchedule = (schedule.slots ?? []).map((slot) => `${slot.start} ${shortId(slot.videoId)}`).join(", ");
    if (schedule.hold) seen.filmHeldUntil = schedule.hold.untilTime;
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
