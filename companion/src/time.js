/**
 * Times in the mirror's zone. The mirror reports its offset from UTC in
 * minutes; nothing here looks at the zone of the machine the companion runs on.
 */

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const ISO = /^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:[.,]\d{1,9})?)?\s?([Zz]|[+-]\d{2}(?::?\d{2})?)?$/;

/** -420 becomes "-07:00". */
export function formatOffset(minutes) {
  const sign = minutes < 0 ? "-" : "+";
  const absolute = Math.abs(minutes);
  return `${sign}${pad(Math.floor(absolute / 60))}:${pad(absolute % 60)}`;
}

/** The instant as the mirror's wall clock reads it: 2026-10-03T07:12:05-07:00. */
export function localIso(ms, offsetMinutes) {
  const shifted = new Date(ms + offsetMinutes * 60_000);
  return shifted.toISOString().slice(0, 19) + formatOffset(offsetMinutes);
}

/** The time of day on the mirror's wall clock: 07:12. */
export function localTime(ms, offsetMinutes) {
  return localIso(ms, offsetMinutes).slice(11, 16);
}

export function weekday(ms, offsetMinutes) {
  return WEEKDAYS[new Date(ms + offsetMinutes * 60_000).getUTCDay()];
}

export function minuteOfDay(ms, offsetMinutes) {
  const shifted = new Date(ms + offsetMinutes * 60_000);
  return shifted.getUTCHours() * 60 + shifted.getUTCMinutes();
}

/** "22:30" becomes 1350; anything else becomes null. */
export function parseClockTime(text) {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(String(text));
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

/**
 * Whether the mirror's wall clock is inside the quiet hours. They may run
 * past midnight, as 22:30 to 06:30 does.
 *
 * @param {number} ms
 * @param {number} offsetMinutes
 * @param {[string, string] | null | undefined} quietHours
 */
export function inQuietHours(ms, offsetMinutes, quietHours) {
  if (!quietHours) return false;
  const start = parseClockTime(quietHours[0]);
  const end = parseClockTime(quietHours[1]);
  if (start === null || end === null || start === end) return false;
  const now = minuteOfDay(ms, offsetMinutes);
  return start < end ? now >= start && now < end : now >= start || now < end;
}

/**
 * Reads an ISO 8601 time that carries its offset from UTC, the same forms the
 * mirror's board accepts.
 *
 * @param {unknown} text
 * @returns {{ ms: number } | { problem: "format" | "offset" | "impossible" }}
 */
export function parseIsoWithOffset(text) {
  const match = typeof text === "string" ? ISO.exec(text.trim()) : null;
  if (!match) return { problem: "format" };
  const [, year, month, day, hour, minute, second = "0", zone] = match;
  if (!zone) return { problem: "offset" };
  const parts = [year, month, day, hour, minute, second].map(Number);
  const utc = Date.UTC(parts[0], parts[1] - 1, parts[2], parts[3], parts[4], parts[5]);
  const check = new Date(utc);
  const real =
    check.getUTCFullYear() === parts[0] &&
    check.getUTCMonth() === parts[1] - 1 &&
    check.getUTCDate() === parts[2] &&
    parts[3] <= 23 &&
    parts[4] <= 59 &&
    parts[5] <= 59;
  if (!real) return { problem: "impossible" };
  let offsetMinutes = 0;
  if (zone.length > 1) {
    const digits = zone.slice(1).replace(":", "");
    const hours = Number(digits.slice(0, 2));
    const rest = digits.length > 2 ? Number(digits.slice(2)) : 0;
    if (hours > 14 || rest > 59) return { problem: "impossible" };
    offsetMinutes = (hours * 60 + rest) * (zone[0] === "-" ? -1 : 1);
  }
  return { ms: utc - offsetMinutes * 60_000 };
}

function pad(number) {
  return String(number).padStart(2, "0");
}
