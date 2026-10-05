import { z } from "zod";
import { describeHabits } from "../habits.js";
import { MirrorRefused } from "../mirror.js";
import { describeRules, shortId } from "../state.js";
import { clockTime, formatOffset, inQuietHours, parseClockTime } from "../time.js";
import { findFilm, parseColor, saveAutomation, tooDarkToRead } from "./common.js";
import { saveLayout } from "./layout.js";

// The mirror's standing settings: its clock, when it is dark by itself, the
// weather's place, the films' timetable, the colour of its text, its name,
// and what the companion does unasked. They are in the phone controls as
// well; here a person changes them by saying so.

const TEXT_COLOR = "#f5f2ec";
const ACCENT_COLOR = "#c2ced3";
const SHORTEST_STAY_SECONDS = 30;
const LONGEST_STAY_SECONDS = 3600;
// The mirror asks a service on the internet where a place lies, once for each way of reading what was said.
// On the mirror that took 2 to 4 seconds; the usual 8 would call a slow day "unreachable".
const PLACE_SEARCH_MS = 15_000;

/** "7:05" and "07:05" become "07:05"; anything that is no time of day becomes null. */
function timeOfDay(text) {
  const match = /^(\d{1,2})[:.](\d{2})$/.exec(String(text).trim());
  if (!match) return null;
  const time = `${match[1].padStart(2, "0")}:${match[2]}`;
  return parseClockTime(time) === null ? null : time;
}

/**
 * A time zone as the mirror wants it: its IANA name as that is properly
 * written, and its offset from UTC at this moment, which the mirror falls
 * back on for a zone its own table does not hold.
 *
 * @returns {{ name: string, offsetMinutes: number } | null} null when there is no such zone
 */
export function resolveZone(name, ms) {
  let format;
  try {
    format = new Intl.DateTimeFormat("en-US", { timeZone: String(name).trim().replace(/ /g, "_"), timeZoneName: "longOffset" });
  } catch {
    return null;
  }
  const written = format.formatToParts(new Date(ms)).find((part) => part.type === "timeZoneName")?.value ?? "";
  const match = /^GMT(?:([+-])(\d{1,2})(?::(\d{2}))?)?$/.exec(written);
  if (!match) return null;
  const minutes = match[1] ? Number(match[2]) * 60 + Number(match[3] ?? 0) : 0;
  return { name: format.resolvedOptions().timeZone, offsetMinutes: match[1] === "-" ? -minutes : minutes };
}

/** @returns {import("../tools.js").Tool[]} */
export function settingsTools({ mirror, clock, habits }) {
  /** The mirror's own clock at this moment, from the state the turn began with; null when that is not known. */
  function mirrorNow(turn) {
    return turn.state ? { ms: turn.state.now + (clock.now() - turn.stateAt), offsetMinutes: turn.state.offsetMinutes } : null;
  }

  return [
    {
      name: "set_clock",
      description:
        "Sets how the mirror's clock reads. format: \"12-hour\" (3:00 PM) or \"24-hour\" (15:00). " +
        "timeZone: where the mirror hangs, as an IANA name such as America/New_York or Europe/Berlin; " +
        "work it out from the city or the zone the person names (\"Eastern time\" is America/New_York). " +
        "The mirror follows that zone's daylight saving by itself. Give format, timeZone or both.",
      schema: z.object({
        format: z.enum(["12-hour", "24-hour"]).optional(),
        timeZone: z.string().min(1).max(64).optional(),
      }),
      changes: true,
      async handler({ format, timeZone }, turn) {
        if (format === undefined && timeZone === undefined) return { error: "Give format, timeZone or both." };
        const before = await mirror.get("/api/v1/preferences");
        const body = { timeZone: before.timeZone, utcOffsetMinutes: before.utcOffsetMinutes, clock24Hour: Boolean(before.clock24Hour) };
        if (timeZone !== undefined) {
          const zone = resolveZone(timeZone, clock.now());
          if (!zone) {
            return { error: `There is no time zone called "${timeZone}". Use an IANA name such as America/New_York or Europe/Berlin.` };
          }
          // The same zone is sent as it was read, so that the mirror leaves its clock alone.
          if (zone.name !== before.timeZone) Object.assign(body, { timeZone: zone.name, utcOffsetMinutes: zone.offsetMinutes });
        }
        if (format !== undefined) body.clock24Hour = format === "24-hour";
        const describe = (clockNow) => ({
          clock: clockNow.clock24Hour ? "24-hour" : "12-hour",
          zone: clockNow.timeZone,
          utcOffset: formatOffset(clockNow.utcOffsetMinutes),
          timeNow: clockTime(turn.state ? mirrorNow(turn).ms : clock.now(), clockNow.utcOffsetMinutes, Boolean(clockNow.clock24Hour)),
        });
        if (body.timeZone === before.timeZone && body.clock24Hour === Boolean(before.clock24Hour)) {
          return { ...describe(before), changed: false, note: "The clock was already like that." };
        }
        const after = await mirror.call("PUT", "/api/v1/preferences", { body });
        const answer = { ...describe(after), changed: true };
        if (after.timeZone !== before.timeZone) {
          answer.note =
            "Reminders keep their moment; the awake hours and the films' timetable now go by the new clock. " +
            "The weather's place is a setting of its own.";
        }
        return answer;
      },
    },
    {
      name: "set_display_rules",
      description:
        "Sets when the display is dark by itself; display in the state shows the rules now. " +
        "awakeFrom and awakeUntil: the hours it is lit each day, as 24-hour HH:MM (\"wake at seven, off at eleven\" is 07:00 and 23:00); " +
        "either alone keeps the other. alwaysAwake: true for no such hours at all. " +
        "sleepWhenNobodyIsThere: whether it goes dark when it has seen nobody for a while and lights up when someone comes; " +
        "afterMinutes: how long that while is (0.5 to 60); " +
        "sensitivity: how small a movement counts as someone, 1 (only large movements) to 10 (the slightest); " +
        "the present one is display.movementSensitivity, so for \"more sensitive\" add 2. " +
        "followRoomLight: whether the brightness follows the room's light. Give only what is to change. " +
        "To make it dark or lit right now, use set_power instead.",
      schema: z.object({
        awakeFrom: z.string().optional(),
        awakeUntil: z.string().optional(),
        alwaysAwake: z.boolean().optional(),
        sleepWhenNobodyIsThere: z.boolean().optional(),
        afterMinutes: z.number().min(0.5).max(60).optional(),
        sensitivity: z.number().int().min(1).max(10).optional(),
        followRoomLight: z.boolean().optional(),
      }),
      changes: true,
      async handler({ awakeFrom, awakeUntil, alwaysAwake, sleepWhenNobodyIsThere, afterMinutes, sensitivity, followRoomLight }, turn) {
        const hours = awakeFrom !== undefined || awakeUntil !== undefined;
        if (!hours && [alwaysAwake, sleepWhenNobodyIsThere, afterMinutes, sensitivity, followRoomLight].every((value) => value === undefined)) {
          return { error: "Give what is to change: awake hours, alwaysAwake, sleepWhenNobodyIsThere, afterMinutes, sensitivity or followRoomLight." };
        }
        if (hours && alwaysAwake === true) return { error: "Give awake hours or alwaysAwake, not both." };
        const before = await mirror.get("/api/v1/automation");
        const changes = {};
        if (hours) {
          const from = awakeFrom === undefined ? before.wakeTime : timeOfDay(awakeFrom);
          const until = awakeUntil === undefined ? before.sleepTime : timeOfDay(awakeUntil);
          if (!from || !until) return { error: "Awake hours are times of day as 24-hour HH:MM, such as 07:00 and 23:00." };
          if (from === until) return { error: `Awake from ${from} until ${until} is no time at all. Give two different times, or alwaysAwake.` };
          Object.assign(changes, { enabled: true, wakeTime: from, sleepTime: until });
        } else if (alwaysAwake !== undefined) {
          changes.enabled = !alwaysAwake;
        }
        const watches = before.motion?.available !== false;
        if (sleepWhenNobodyIsThere !== undefined) changes.motionEnabled = sleepWhenNobodyIsThere;
        // A time or a sensitivity for it is asked of a mirror that is to do it.
        else if ((afterMinutes !== undefined || sensitivity !== undefined) && !before.motionEnabled) changes.motionEnabled = true;
        if (changes.motionEnabled && !watches) {
          return { error: "This mirror cannot see whether someone is there: its camera is not available. Nothing was changed." };
        }
        if (afterMinutes !== undefined) {
          changes.motionTimeoutSeconds = Math.max(SHORTEST_STAY_SECONDS, Math.min(LONGEST_STAY_SECONDS, Math.round(afterMinutes * 60)));
        }
        if (sensitivity !== undefined) changes.motionSensitivity = sensitivity;
        if (followRoomLight !== undefined) {
          if (followRoomLight && !before.ambientLightAvailable) {
            return { error: "This mirror has no light sensor, so its brightness cannot follow the room. Use set_brightness for a level." };
          }
          changes.ambientEnabled = followRoomLight;
        }
        const differs = Object.keys(changes).filter((name) => changes[name] !== before[name]);
        if (differs.length === 0) return { ...describeRules(before), changed: false, note: "The display's rules were already like that." };
        const after = { ...before, ...changes };
        await saveAutomation(mirror, before, changes);
        const answer = { ...describeRules(after), changed: true };
        const now = mirrorNow(turn);
        // Read as quiet hours, the awake hours tell whether this moment is inside them.
        const hoursChanged = differs.some((name) => ["enabled", "wakeTime", "sleepTime"].includes(name));
        if (hoursChanged && after.enabled && now && !before.manualOverride && !inQuietHours(now.ms, now.offsetMinutes, [after.wakeTime, after.sleepTime])) {
          answer.note = "It is outside those hours now, so the display goes dark in a moment.";
        }
        return answer;
      },
    },
    {
      name: "set_weather",
      description:
        "Sets what the weather on the glass is for. place: a town, best with its state or country (\"Portland, Maine\"); " +
        "the mirror looks it up and takes the best known match. units: fahrenheit or celsius. on: false to do without weather, true to have it again. " +
        "Give only what is to change. weather.place in the state is the place now. " +
        "The answer names the place taken and other places of that name: say which one you took.",
      schema: z.object({
        place: z.string().min(2).max(80).optional(),
        units: z.enum(["fahrenheit", "celsius"]).optional(),
        on: z.boolean().optional(),
      }),
      changes: true,
      async handler({ place, units, on }, turn) {
        if (place === undefined && units === undefined && on === undefined) return { error: "Give place, units or on." };
        const before = await mirror.get("/api/v1/weather");
        const was = before.config ?? {};
        const config = {
          enabled: Boolean(was.enabled),
          locationName: was.locationName ?? "",
          units: was.units === "metric" ? "metric" : "us",
          latitude: was.latitude ?? null,
          longitude: was.longitude ?? null,
        };
        if (units !== undefined) config.units = units === "celsius" ? "metric" : "us";
        if (on !== undefined) config.enabled = on;
        const answer = {};
        // A mirror whose weather was off has forgotten where its place lies, so the place is looked up again.
        const lookUp = place ?? (config.enabled && typeof config.latitude !== "number" ? config.locationName : undefined);
        if (config.enabled && lookUp === "") return { error: "No place is set for the weather. Ask which town it should be for." };
        if (lookUp !== undefined && on !== false) {
          let found;
          try {
            found = await mirror.get(`/api/v1/weather/locations?q=${encodeURIComponent(lookUp.trim())}`, { responseTimeoutMs: PLACE_SEARCH_MS });
          } catch (error) {
            if (!(error instanceof MirrorRefused)) throw error;
            return { error: "The mirror could not look the place up just now; it needs the internet for that. Nothing was changed." };
          }
          const results = found.results ?? [];
          if (results.length === 0) {
            const elsewhere = (found.elsewhere ?? []).map((other) => other.label);
            return {
              error:
                elsewhere.length > 0
                  ? `No "${lookUp}" was found there. Places of that name: ${elsewhere.join("; ")}. Nothing was changed. Ask which one is meant.`
                  : `No place called "${lookUp}" was found. Nothing was changed. Ask for the town with its state or country.`,
            };
          }
          const [taken, ...others] = results;
          Object.assign(config, { enabled: true, locationName: taken.label, latitude: taken.latitude, longitude: taken.longitude });
          if (others.length > 0) answer.otherPlacesOfThatName = others.map((other) => other.label);
          // The clock is not moved along with the weather, and the person may want it moved.
          const clockZone = turn.state?.snapshot?.now?.zone;
          if (taken.timezone && clockZone && clockZone !== "unknown" && taken.timezone !== clockZone) {
            answer.note = `That place is in the time zone ${taken.timezone}; the mirror's clock stays on ${clockZone}. Change the clock only if asked.`;
          }
        }
        const same =
          config.enabled === Boolean(was.enabled) && config.units === was.units &&
          config.locationName === (was.locationName ?? "") && config.latitude === (was.latitude ?? null) && config.longitude === (was.longitude ?? null);
        const described = {
          weather: config.enabled ? "on" : "off",
          place: config.locationName || "none",
          units: config.units === "metric" ? "celsius" : "fahrenheit",
        };
        if (same) return { ...described, changed: false, note: "The weather was already like that." };
        await mirror.call("PUT", "/api/v1/weather", { body: config });
        if (config.enabled) {
          answer.forecast = "The mirror is fetching it now; the weather in the state is still the old one. Do not quote it.";
        }
        return { ...described, changed: true, ...answer };
      },
    },
    {
      name: "set_film_schedule",
      description:
        "Sets which film plays at which time of day, by itself, every day. slots is the whole timetable: up to 8 of " +
        "{at: 24-hour HH:MM, film: an id or name from the state}; a film plays from its time until the next one's, around the clock. " +
        "Giving slots replaces the timetable and turns it on, so to add one time give the present ones with it. " +
        "on: false stops the timetable and keeps it; on: true starts it again. " +
        "resume: true ends a film that was chosen by hand and returns to the timetable. " +
        "background.filmSchedule in the state is the timetable when it is on, background.filmScheduleSwitchedOff when it is off.",
      schema: z.object({
        slots: z.array(z.object({ at: z.string(), film: z.string().min(1) })).max(8).optional(),
        on: z.boolean().optional(),
        resume: z.boolean().optional(),
      }),
      changes: true,
      async handler({ slots, on, resume }) {
        if (slots === undefined && on === undefined && resume === undefined) return { error: "Give slots, on or resume." };
        const catalog = await mirror.get("/api/v1/background-videos");
        const videos = catalog.videos ?? [];
        const listed = videos.map((video) => `${shortId(video.id)} (${video.name})`).join(", ");
        const kept = (catalog.schedule?.slots ?? []).map((slot) => ({ start: slot.start, videoId: slot.videoId }));
        let after = catalog;
        if (slots !== undefined || on !== undefined) {
          let wanted = kept;
          if (slots !== undefined) {
            wanted = [];
            for (const slot of slots) {
              const start = timeOfDay(slot.at);
              if (!start) return { error: `"${slot.at}" is no time of day. Give 24-hour HH:MM, such as 06:30 or 19:00. Nothing was changed.` };
              const film = findFilm(videos, slot.film);
              if (!film) return { error: `No film matches "${slot.film}". The films are: ${listed || "none"}. Nothing was changed.` };
              if (wanted.some((other) => other.start === start)) return { error: `Two films cannot both start at ${start}. Nothing was changed.` };
              wanted.push({ start, videoId: film.id });
            }
          }
          const enabled = on ?? wanted.length > 0;
          if (enabled && wanted.length === 0) {
            return { error: "There is no timetable to turn on. Give slots: which film from which time of day." };
          }
          after = await mirror.call("PUT", "/api/v1/background-videos/schedule", { body: { enabled, slots: wanted } });
        }
        if (resume) {
          if (!after.schedule?.active) return { error: "No film schedule is on, so there is nothing to return to." };
          after = await mirror.call("POST", "/api/v1/background-videos/schedule/resume");
        }
        const name = (id) => videos.find((video) => video.id === id)?.name ?? shortId(id);
        const schedule = after.schedule ?? {};
        const answer = {
          filmSchedule: schedule.active ? "on" : "off",
          timetable: (schedule.slots ?? []).map((slot) => ({ at: slot.start, film: name(slot.videoId) })),
        };
        if (schedule.active && after.effectiveId) answer.showingNow = name(after.effectiveId);
        if (schedule.hold) answer.note = `A film chosen by hand shows until ${schedule.hold.untilTime}; resume: true returns to the timetable.`;
        return answer;
      },
    },
    {
      name: "set_text_color",
      description:
        "Sets the colour of what is written on the glass. text: the clock, the date, notes and the board. " +
        "accent: the weather and the other small widgets. Each is #rrggbb, or \"default\" for the mirror's own soft white. " +
        "Give text, accent or both; for everything in one colour give both. Pale colours read best: this glass shows dark ones as mirror.",
      schema: z.object({
        text: z.string().min(1).optional(),
        accent: z.string().min(1).optional(),
      }),
      changes: true,
      async handler({ text, accent }) {
        if (text === undefined && accent === undefined) return { error: "Give text, accent or both." };
        const read = (value, usual) => (value === undefined ? undefined : /^(default|reset|normal)$/i.test(value.trim()) ? usual : parseColor(value));
        const wanted = { textColor: read(text, TEXT_COLOR), accentColor: read(accent, ACCENT_COLOR) };
        if (wanted.textColor === null || wanted.accentColor === null) {
          return { error: "A colour is written as #rrggbb, for example #ffd9a0, or \"default\". Nothing was changed." };
        }
        const layout = await mirror.get("/api/v1/dashboard/layout");
        const before = { textColor: layout.textColor, accentColor: layout.accentColor };
        for (const name of ["textColor", "accentColor"]) if (wanted[name]) layout[name] = wanted[name];
        const now = { text: layout.textColor, accent: layout.accentColor };
        if (layout.textColor === before.textColor && layout.accentColor === before.accentColor) {
          return { ...now, changed: false, note: "The text already had that colour." };
        }
        await saveLayout(mirror, layout);
        const answer = { ...now, changed: true };
        if ([wanted.textColor, wanted.accentColor].some((color) => color && tooDarkToRead(color))) {
          answer.note = "That is a dark colour, and on this glass dark text can hardly be seen. Say so.";
        }
        return answer;
      },
    },
    {
      name: "set_name",
      description:
        "Gives the mirror the name it shows on the glass (in the name widget) and in the phone controls: name in the state. " +
        "It does not change the word people call you by: that stays \"Mirror\". Say so if the person seems to expect otherwise.",
      schema: z.object({ name: z.string().min(1).max(64) }),
      changes: true,
      async handler({ name }) {
        const wanted = name.trim().replace(/\s+/g, " ");
        if (!wanted) return { error: "A name needs at least one letter." };
        const was = (await mirror.get("/api/v1/status")).displayName ?? "";
        if (wanted === was) return { name: was, changed: false, note: "That is the name already." };
        try {
          await mirror.call("POST", "/api/v1/control/name", { body: { name: wanted } });
        } catch (error) {
          if (!(error instanceof MirrorRefused) || error.status !== 503) throw error;
          return { error: "The mirror's own system did not take the new name. Nothing was changed." };
        }
        return { name: wanted, was, changed: true };
      },
    },
    {
      name: "habits",
      description:
        "Reads or changes what you do without being asked. greet: a line for someone who walks up after the display was dark a while. " +
        "morningBriefing: the day's card for the first person of the morning. reminders: a card the minute a reminder falls due. " +
        "tidy: now and then one small improvement of the display, such as a film that suits the time of day; tidyEveryMinutes: how often (5 to 1440). " +
        "quietHours: the hours in which you do none of this, as \"22:30-06:30\", or \"none\". " +
        "Give only what is to change. With no arguments it changes nothing and tells how they are set.",
      schema: z.object({
        greet: z.boolean().optional(),
        morningBriefing: z.boolean().optional(),
        reminders: z.boolean().optional(),
        tidy: z.boolean().optional(),
        tidyEveryMinutes: z.number().min(5).max(1440).optional(),
        quietHours: z.string().optional(),
      }),
      changes: true,
      async handler({ greet, morningBriefing, reminders, tidy, tidyEveryMinutes, quietHours }) {
        const changes = { greet, morningBriefing, reminders, tend: tidy, tendMinutes: tidyEveryMinutes };
        if (quietHours !== undefined) {
          const text = quietHours.trim().toLowerCase();
          const match = /^(\d{1,2}[:.]\d{2})\s*(?:-|–|—|to|until)\s*(\d{1,2}[:.]\d{2})$/.exec(text);
          const hours = match ? [timeOfDay(match[1]), timeOfDay(match[2])] : null;
          if (["none", "off", "no", "never"].includes(text)) changes.quietHours = null;
          else if (hours && hours[0] && hours[1]) changes.quietHours = hours;
          else return { error: "Quiet hours are written as two 24-hour times, \"22:30-06:30\", or \"none\". Nothing was changed." };
        }
        // A way of tidying more often is asked of a mirror that is to tidy.
        if (tidyEveryMinutes !== undefined && tidy === undefined) changes.tend = true;
        if (Object.values(changes).every((value) => value === undefined)) return { habits: describeHabits(habits.get()) };
        let result;
        try {
          result = habits.change(changes);
        } catch (error) {
          return { error: `${error.message} Nothing was changed.` };
        }
        const answer = { habits: describeHabits(result.habits), changed: result.changed.length > 0 };
        if (!answer.changed) answer.note = "They were already like that.";
        else if (!result.saved) answer.note = "This holds until the companion is next restarted: it could not be written to its settings.";
        return answer;
      },
    },
  ];
}
