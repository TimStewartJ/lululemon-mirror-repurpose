import fs from "node:fs";
import path from "node:path";

const KEPT = 200;

/**
 * The record of what was said to the mirror and what the companion did, for
 * its owner and for the tending run, which must leave recent requests alone.
 * Kept in memory and as JSON lines in the state directory, so that it
 * survives a restart.
 *
 * @typedef {Object} ActivityEntry
 * @property {number} at
 * @property {"voice"|"controls"|"presence"|"reminder"|"tend"|"test"} source
 * @property {string} heard
 * @property {string} reply
 * @property {string[]} acted
 * @property {boolean} ignored
 * @property {string} reason
 * @property {number} ms
 * @property {string} [error]
 */

/**
 * @param {string} stateDir
 * @param {(event: string, fields?: object) => void} [log]
 */
export function createActivity(stateDir, log = () => {}) {
  const file = path.join(stateDir, "activity.jsonl");
  /** @type {ActivityEntry[]} oldest first */
  let entries = [];
  let linesInFile = 0;
  try {
    const lines = fs.readFileSync(file, "utf8").split("\n").filter(Boolean);
    linesInFile = lines.length;
    for (const line of lines.slice(-KEPT)) {
      try {
        entries.push(JSON.parse(line));
      } catch {
        // A line cut short by a crash is dropped.
      }
    }
  } catch (error) {
    if (error.code !== "ENOENT") log("activity.unreadable", { detail: error.message });
  }

  function persist(entry) {
    try {
      fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
      if (linesInFile >= KEPT * 2) {
        fs.writeFileSync(file, entries.map((kept) => JSON.stringify(kept)).join("\n") + "\n", { mode: 0o600 });
        linesInFile = entries.length;
      } else {
        fs.appendFileSync(file, JSON.stringify(entry) + "\n", { mode: 0o600 });
        linesInFile += 1;
      }
    } catch (error) {
      log("activity.unsaved", { detail: error.message });
    }
  }

  return {
    /** @param {Partial<ActivityEntry> & { at: number, source: ActivityEntry["source"] }} entry */
    add(entry) {
      const full = {
        at: entry.at,
        source: entry.source,
        heard: entry.heard ?? "",
        reply: entry.reply ?? "",
        acted: entry.acted ?? [],
        ignored: entry.ignored ?? false,
        reason: entry.reason ?? "",
        ms: entry.ms ?? 0,
      };
      if (entry.error) full.error = entry.error;
      entries.push(full);
      if (entries.length > KEPT) entries = entries.slice(-KEPT);
      persist(full);
      return full;
    },

    /** The most recent entries, newest first. */
    recent(limit = 20) {
      return entries.slice(-limit).reverse();
    },

    /** Entries at or after the given moment, oldest first. */
    since(at) {
      return entries.filter((entry) => entry.at >= at);
    },

    /** The last thing a person said or typed, for the health report. */
    lastExchange() {
      for (let index = entries.length - 1; index >= 0; index--) {
        const entry = entries[index];
        if (entry.source === "voice" || entry.source === "controls" || entry.source === "test") return entry;
      }
      return null;
    },
  };
}
