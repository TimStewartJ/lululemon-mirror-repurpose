import fs from "node:fs";
import { describeError } from "./log.js";
import { parseClockTime } from "./time.js";
import { updateConfigFile } from "./config.js";

const SWITCHES = ["greet", "morningBriefing", "reminders", "tend"];

/**
 * What the companion does unasked, as settings that can be changed while
 * it runs: whether it greets, gives the morning's briefing, shows reminders
 * when they fall due and tidies the display, and the hours in which it does
 * none of that. They start as the config file has them, and a change is
 * written back there, so that it outlasts a restart.
 *
 * @param {Object} parts
 * @param {{ greet: boolean, morningBriefing?: boolean, reminders: boolean, tend: boolean, tendMinutes: number, quietHours: [string, string] | null }} parts.settings
 *   The settings themselves, as the part that acts on them holds them: they are changed in place.
 * @param {string|null} [parts.file] The config file to write changes to; none for a companion that keeps them in memory only.
 * @param {(event: string, fields?: object) => void} [parts.log]
 */
export function createHabits({ settings, file = null, log = () => {} }) {
  const listeners = [];

  const current = () => ({
    greet: Boolean(settings.greet),
    morningBriefing: Boolean(settings.morningBriefing),
    reminders: Boolean(settings.reminders),
    tend: Boolean(settings.tend),
    tendMinutes: settings.tendMinutes,
    quietHours: settings.quietHours ? [...settings.quietHours] : null,
  });

  return {
    /** The settings themselves, for the part that acts on them: it reads them each time it is about to act. */
    settings,

    /** The habits as they are now; a copy. */
    get: current,

    /**
     * Changes some of them. Nothing is changed if any part is not usable.
     *
     * @param {Partial<ReturnType<typeof current>>} changes
     * @returns {{ habits: ReturnType<typeof current>, changed: string[], saved: boolean }}
     *   `saved` is false when the change holds only until the companion restarts.
     * @throws {Error} with a sentence about what is wrong
     */
    change(changes) {
      const next = {};
      for (const name of SWITCHES) {
        if (changes[name] === undefined) continue;
        if (typeof changes[name] !== "boolean") throw new Error(`${name} is on or off.`);
        next[name] = changes[name];
      }
      if (changes.tendMinutes !== undefined) {
        const minutes = Number(changes.tendMinutes);
        if (!Number.isFinite(minutes) || minutes < 5 || minutes > 24 * 60) {
          throw new Error("The display is tidied every 5 minutes at the most often and once a day at the least.");
        }
        next.tendMinutes = minutes;
      }
      if (changes.quietHours !== undefined) {
        const hours = changes.quietHours;
        if (hours !== null) {
          const usable = Array.isArray(hours) && hours.length === 2 && hours.every((time) => parseClockTime(time) !== null);
          if (!usable) throw new Error("Quiet hours are two times of day such as 22:30 and 06:30, or none.");
          if (hours[0] === hours[1]) throw new Error("Quiet hours that begin and end at the same minute would last all day or not at all.");
        }
        next.quietHours = hours === null ? null : [hours[0], hours[1]];
      }
      const before = current();
      const changed = Object.keys(next).filter((name) => JSON.stringify(next[name]) !== JSON.stringify(before[name]));
      if (changed.length === 0) return { habits: before, changed, saved: true };
      Object.assign(settings, next);
      let saved = false;
      if (file && fs.existsSync(file)) {
        try {
          updateConfigFile(file, (raw) => {
            raw.proactive = { ...(raw.proactive ?? {}), ...Object.fromEntries(changed.map((name) => [name, next[name]])) };
          });
          saved = true;
        } catch (error) {
          log("habits.not_saved", { detail: describeError(error) });
        }
      }
      log("habits.changed", { changed, saved });
      for (const listener of listeners) listener(current());
      return { habits: current(), changed, saved };
    },

    /** Calls the listener after every change, with the habits as they then are. */
    onChange(listener) {
      listeners.push(listener);
    },
  };
}

/** @typedef {ReturnType<typeof createHabits>} Habits */

/** The habits in the words the model reads and the tool answers with. */
export function describeHabits(habits) {
  return {
    greetsWhoWalksUp: habits.greet ? "on" : "off",
    morningBriefing: habits.morningBriefing ? "on" : "off",
    showsRemindersWhenDue: habits.reminders ? "on" : "off",
    tidiesTheDisplay: habits.tend ? `every ${habits.tendMinutes} minutes` : "off",
    quietHours: habits.quietHours ? `${habits.quietHours[0]} to ${habits.quietHours[1]}` : "none",
  };
}
