// What several tools need: finding a film by what someone called it, saving the display's rules, reading a colour.

/** A film's name without its ending and with spaces for its dashes: "luminous flowers spatial 180s". */
function plainName(name) {
  return String(name)
    .toLowerCase()
    .replace(/\.[a-z0-9]+$/, "")
    .replace(/[^\p{L}\p{Nd}]+/gu, " ")
    .trim();
}

/**
 * Finds a film by its id, the start of its id, its name, or words that only
 * one film's name holds ("flowers").
 *
 * @param {{ id: string, name: string }[]} videos
 * @param {string} wanted
 * @returns {{ id: string, name: string } | null} null when none or more than one fits
 */
export function findFilm(videos, wanted) {
  const text = wanted.trim().toLowerCase();
  const exact = videos.filter(
    (video) =>
      video.id === text ||
      (text.length >= 6 && video.id.startsWith(text)) ||
      String(video.name).toLowerCase() === text ||
      plainName(video.name) === plainName(text),
  );
  if (exact.length === 1) return exact[0];
  if (exact.length > 1) return null;
  const words = plainName(text);
  if (words.length < 3) return null;
  const holding = videos.filter((video) => ` ${plainName(video.name)} `.includes(` ${words} `));
  return holding.length === 1 ? holding[0] : null;
}

/**
 * Saves the display's rules. The mirror takes them as a whole, so they are
 * sent back as read with the changes laid over them. Saving them ends a
 * sleep or a wake someone asked for, so that is asked for again.
 *
 * @param {import("../mirror.js").Mirror} mirror
 * @param {object} before The mirror's answer to GET /api/v1/automation.
 * @param {object} changes The settings that differ.
 * @returns {Promise<object>} the rules as the mirror now has them
 */
export async function saveAutomation(mirror, before, changes) {
  const after = await mirror.call("PUT", "/api/v1/automation", {
    body: {
      enabled: before.enabled,
      wakeTime: before.wakeTime,
      sleepTime: before.sleepTime,
      wakeBrightness: before.wakeBrightness,
      ambientEnabled: before.ambientEnabled,
      ambientMinimum: before.ambientMinimum,
      ambientMaximum: before.ambientMaximum,
      motionEnabled: before.motionEnabled,
      motionTimeoutSeconds: before.motionTimeoutSeconds,
      motionSensitivity: before.motionSensitivity,
      ...changes,
    },
  });
  if (before.manualOverride) {
    await mirror.call("POST", `/api/v1/automation/${before.sleeping ? "sleep" : "wake"}`);
  }
  return after;
}

/** "#1A2b3c", "1a2b3c" or "#abc" becomes "#1a2b3c"; anything else becomes null. */
export function parseColor(text) {
  const match = /^#?([0-9a-f]{6}|[0-9a-f]{3})$/i.exec(String(text).trim());
  if (!match) return null;
  const digits = match[1].toLowerCase();
  return "#" + (digits.length === 3 ? [...digits].map((digit) => digit + digit).join("") : digits);
}

/** Whether a colour is so dark that text in it can hardly be seen on the glass, which is black where nothing is drawn. */
export function tooDarkToRead(color) {
  const [red, green, blue] = [1, 3, 5].map((at) => parseInt(color.slice(at, at + 2), 16));
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue < 60;
}
