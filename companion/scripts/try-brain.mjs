// Tries the real model against a fake mirror: says a set of requests to a
// companion that runs in this process, and prints what was heard, which
// tools ran, the reply and the timings, with a check of each outcome.
// It needs a sign-in for the provider it is given, as the companion does (the
// README's "Choosing a model"), and nothing else: speech-to-text is replaced
// by a stand-in, and no real mirror is used.
//
//   node scripts/try-brain.mjs [--provider NAME] [--model NAME] [--effort LEVEL] [--endpoint ADDRESS] [--api NAME]
//                              [--offset MINUTES] [--zone NAME] [--only WORD] [--calls] [--verbose]
//
// Without --provider and --model it tries the model the companion's own config names.
// --endpoint and --api are for a server of one's own, as "endpoint" in the config is.
// --only runs the requests that contain one of the words, given with commas between them, or that belong to a group
// of that name (settings, moments); --calls adds one line per call to the model; --verbose prints the companion's whole log.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { parseArgs } from "node:util";
import { loadConfig, parseConfig } from "../src/config.js";
import { createLog } from "../src/log.js";
import { serve } from "../src/serve.js";
import { localIso } from "../src/time.js";
import { startFakeMirror } from "../tests/fakes/mirror.js";
import { fakeStt, speech } from "../tests/fakes/stt.js";

const { values: options } = parseArgs({
  options: {
    provider: { type: "string" },
    model: { type: "string" },
    endpoint: { type: "string" },
    api: { type: "string", default: "openai-completions" },
    offset: { type: "string", default: "-420" },
    zone: { type: "string", default: "America/Los_Angeles" },
    only: { type: "string" },
    effort: { type: "string", default: "low" },
    calls: { type: "boolean", default: false },
    verbose: { type: "boolean", default: false },
  },
});
// What is not named here is what the companion on this machine uses, its endpoint included.
let own = {};
try {
  own = loadConfig();
} catch {
  // No config here: then both must be named.
}
options.provider ??= own.provider;
options.model ??= own.model;
if (!options.provider || !options.model) {
  console.error('Name the model to try: --provider NAME --model NAME ("node src/cli.js providers" and "models NAME" list them).');
  process.exit(2);
}
const ownEndpoint = !options.endpoint && options.provider === own.provider ? own.endpoint : null;
const offset = Number(options.offset);
const SECRET = "try-brain-secret-not-a-real-one";

const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "companion-try-"));
const mirror = await startFakeMirror({ timeZone: options.zone, utcOffsetMinutes: offset });
const config = parseConfig({
  secret: SECRET,
  mirror: { host: mirror.host, port: mirror.port, token: mirror.token },
  provider: options.provider,
  model: options.model,
  reasoningEffort: options.effort,
  ...(options.endpoint ? { endpoint: { baseUrl: options.endpoint, api: options.api, images: true } } : ownEndpoint ? { endpoint: ownEndpoint } : {}),
  // No quiet hours, so that the greeting and the tending run can be tried at any time of day.
  proactive: { greet: true, reminders: false, tend: true, quietHours: null },
  stateDir,
});
config.listen = { host: "127.0.0.1", port: 0 };
const logLines = [];
const log = createLog({
  write: (line) => {
    logLines.push(line);
    if (options.verbose) console.log(`      ${line}`);
  },
  secrets: [SECRET, mirror.token],
});
const clock = { now: () => Date.now(), setTimeout: (run, ms) => setTimeout(run, ms), clearTimeout: (handle) => clearTimeout(handle) };
const running = await serve(config, { log, clock, stt: fakeStt(clock) });
const base = `http://127.0.0.1:${running.port}`;
const headers = { Authorization: `Bearer ${SECRET}` };

async function health() {
  return (await fetch(`${base}/v1/health`, { headers })).json();
}

let utterance = 0;
/** Says the words to the companion as the mirror would, and returns its answer. */
async function say(words, addressed = "name") {
  utterance += 1;
  const response = await fetch(`${base}/v1/utterance`, {
    method: "POST",
    headers: { ...headers, "Content-Type": "audio/wav", "X-Mirror-Addressed": addressed, "X-Mirror-Utterance": `try-${utterance}` },
    body: speech(words),
  });
  return response.json();
}

/** Sends a greeting as Mirror Home does when it recognised one by itself: no recording, and no model behind it. */
async function shortcut(name, words) {
  const response = await fetch(`${base}/v1/ask`, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({ text: words, source: "shortcut", shortcut: name }),
  });
  return response.json();
}

const widget = (id) => mirror.widget(id);
const film = () => mirror.state.films.find((candidate) => candidate.id === mirror.state.activeFilm).name;
const item = (pattern) => mirror.state.board.items.find((candidate) => pattern.test(candidate.title));
const local = (ms) => localIso(ms, offset);
/** Everything an answer puts on the glass: its line or headline, and the rows of a card. */
const shown = (answer) => [answer.reply, ...answer.details.map((row) => `${row.label} ${row.text}`)].join(" | ");
const isCard = (answer) => answer.details.length > 0;
/** Prints the rows of a card under its headline, the labels in capitals as the glass sets them. */
function printRows(answer) {
  const width = Math.max(0, ...answer.details.map((row) => row.label.length));
  for (const row of answer.details) console.log(`          ${row.label.toUpperCase().padEnd(width)}  ${row.text}`);
}
const openItems = () => mirror.state.board.items.filter((candidate) => !candidate.done && candidate.kind !== "note");
/** Whether what the glass shows speaks of the item: one of the longer words of its title is there. */
const names = (answer, entry) => entry.title.toLowerCase().split(/\W+/).some((word) => word.length >= 4 && shown(answer).toLowerCase().includes(word));
/** Puts an item on the fake mirror's board directly, as another program would. */
const post = (id, entry) => mirror.state.board.put(id, entry, "try-brain").item;
/** The moments of a kind that are on the fake mirror's glass now. */
const moments = (kind) => mirror.state.moments.filter((moment) => moment.until > Date.now() && (!kind || moment.kind === kind));
const describeMoment = (moment) =>
  `${moment.kind}${moment.title ? ` "${moment.title}"` : ""}` +
  (moment.text ? ` "${moment.text.replace(/\n/g, " / ")}"` : "") +
  (moment.rows ? ` [${moment.rows.map((row) => row.text).join(" | ")}]` : "") +
  (moment.values ? ` ${moment.chart} [${moment.values.map((entry) => `${entry.label} ${entry.value}`).join(", ")}]` : "") +
  (moment.shapes ? ` of ${moment.shapes.length} shapes (${[...new Set(moment.shapes.map((shape) => shape.shape))].join(", ")})` : "") +
  (moment.endsAt ? ` running out in ${Math.round((moment.endsAt - Date.now()) / 1000)} s` : "") +
  `, ${moment.size}${moment.height ? ` ${moment.height}` : ""}${moment.side ? ` ${moment.side}` : ""}, for ${Math.round((moment.until - moment.createdAt) / 1000)} s` +
  (moment.color ? `, ${moment.color}` : "") + (moment.motion !== "none" ? `, ${moment.motion}` : "");
const momentsNote = () => moments().map(describeMoment).join("; ") || "no moment";

/** A complaint when a tool had to be called more than once: every further call is seconds the person waits. */
const once = (answer, tool) => {
  const calls = answer.acted.filter((name) => name === tool).length;
  return calls === 1 ? "" : `${tool} was called ${calls} times`;
};
/** Every widget's place and whether it shows, to tell whether a request moved what it was not asked to. */
const boxes = () => JSON.stringify(mirror.state.layout.widgets.map((entry) => [entry.id, entry.visible, entry.x, entry.y, entry.w, entry.h]));
const changedWidgets = (before) => {
  const was = new Map(JSON.parse(before).map((entry) => [entry[0], JSON.stringify(entry)]));
  return JSON.parse(boxes()).filter((entry) => was.get(entry[0]) !== JSON.stringify(entry)).map((entry) => `${entry[0]} ${entry[1] ? "shown" : "hidden"} at ${entry.slice(2).join(",")}`).join("; ");
};
const dayAfter = (days, time) => `${local(Date.now() + days * 86_400_000).slice(0, 10)}T${time}`;

/**
 * The requests, in order. `check` looks at the answer and at the fake mirror
 * and returns what is wrong, or nothing.
 */
const steps = [
  {
    words: "Mirror, change the background to the flowers one.",
    check: (answer) => !answer.acted.includes("set_background") || !/flowers/.test(film()) ? `the film is ${film()}` : "",
  },
  {
    words: "Mirror, remind me to take out the trash at seven tomorrow.",
    check: () => {
      const reminder = item(/trash/i);
      if (!reminder?.due) return "no reminder about the trash is on the board";
      const due = local(reminder.due);
      // Said after midnight, "tomorrow" may fairly mean the morning that is coming.
      const days = Number(local(Date.now()).slice(11, 13)) < 4 ? [0, 1] : [1];
      return days.some((day) => due.startsWith(dayAfter(day, "07:00"))) ? "" : `it is due ${due}`;
    },
    note: () => `due ${item(/trash/i)?.due ? local(item(/trash/i).due) : "nothing"} on the mirror's clock`,
  },
  {
    words: "Mirror, move the clock to the bottom.",
    check: (answer) => {
      const clockWidget = widget("clock");
      if (!answer.acted.includes("arrange_widgets")) return "arrange_widgets did not run";
      return clockWidget.y + clockWidget.h >= 850 ? "" : `the clock is at y ${clockWidget.y}, h ${clockWidget.h}`;
    },
    note: () => `clock x ${widget("clock").x}, y ${widget("clock").y}, w ${widget("clock").w}, h ${widget("clock").h}`,
  },
  {
    words: "Mirror, what's on my list?",
    check: (answer) => (/trash/i.test(shown(answer)) ? "" : "the answer does not mention the trash"),
  },
  {
    words: "The mirror in the hall needs cleaning before the guests arrive.",
    check: (answer) => (answer.ignored && answer.reason === "not-addressed" && answer.reply === "" ? "" : "it was not ignored"),
  },
  {
    // What speech-to-text made of the same sentence from across the room.
    words: "Mirror and all needs cleaning before the guests arrive.",
    check: (answer) => (answer.ignored ? "" : "it was not ignored"),
  },
  {
    // A request whose leading name speech-to-text dropped.
    words: "Show me my reminders.",
    check: (answer) => (!answer.ignored && answer.reply.length > 0 ? "" : "it was ignored"),
    note: () => `board widget ${widget("board").visible ? "shown" : "hidden"}`,
  },
  {
    words: "Mirror, make the clock bigger and hide the weather.",
    before: () => ({ h: widget("clock").h }),
    check: (answer, before) =>
      widget("clock").h > before.h && widget("weather").visible === false ? "" : `clock h ${widget("clock").h}, weather visible ${widget("weather").visible}`,
    note: () => `clock x ${widget("clock").x}, y ${widget("clock").y}, w ${widget("clock").w}, h ${widget("clock").h}`,
  },
  {
    words: "Mirror, change the background to something palmer.",
    check: (answer) => (answer.acted.includes("set_background") && /still-water/.test(film()) ? "" : `the film is ${film()}`),
    note: () => `film ${film()}`,
  },
  {
    words: "Mirror, set a timer for ten minutes.",
    check: () => {
      const timer = mirror.state.board.items.find((candidate) => candidate.kind === "reminder" && !/trash/i.test(candidate.title));
      if (!timer) return "no timer is on the board";
      const minutes = (timer.due - Date.now()) / 60_000;
      return minutes > 8.5 && minutes < 10.5 ? "" : `it is due in ${minutes.toFixed(1)} minutes`;
    },
  },
  {
    words: "Mirror, add milk and eggs to my to do list.",
    check: () => (item(/milk/i) && item(/eggs/i) ? "" : "milk and eggs are not both on the board"),
    note: () => `board: ${mirror.state.board.items.map((candidate) => `${candidate.kind} "${candidate.title}"`).join(", ")}`,
  },
  {
    words: "Mirror, I bought the milk.",
    check: () => (item(/milk/i)?.done || !item(/^milk$/i) ? "" : "the milk is not marked done"),
  },
  {
    words: "Mirror, what is the weather like today?",
    // One line or a card, as the model sees fit, but from the state and with no other tool.
    check: (answer) =>
      answer.acted.every((name) => name === "present") && /rain|16|12|overcast/i.test(shown(answer)) ? "" : "the answer does not give the weather from the state",
  },
  {
    words: "Mirror, remind me to call the dentist at three this afternoon.",
    check: () => {
      const reminder = item(/dentist/i);
      if (!reminder?.due) return "no reminder about the dentist is on the board";
      const due = local(reminder.due);
      // Said after three, the afternoon that is meant can only be tomorrow's.
      const day = Number(local(Date.now()).slice(11, 13)) >= 15 ? 1 : 0;
      return due.startsWith(dayAfter(day, "15:00")) ? "" : `it is due ${due}`;
    },
    note: () => `due ${item(/dentist/i)?.due ? local(item(/dentist/i).due) : "nothing"}`,
  },
  {
    words: "Mirror, set an alarm.",
    check: (answer) => (answer.listen && answer.reply.endsWith("?") ? "" : "it did not ask when"),
  },
  {
    words: "Half past six tomorrow morning.",
    addressed: "follow-up",
    check: () => {
      const alarm = mirror.state.board.items.find((candidate) => candidate.due && local(candidate.due).startsWith(dayAfter(1, "06:30")));
      return alarm ? "" : "no item is due tomorrow at 06:30";
    },
    note: () => `board: ${mirror.state.board.items.map((candidate) => `${candidate.kind} "${candidate.title}"${candidate.due ? ` due ${local(candidate.due)}` : ""}`).join("; ")}`,
  },
  {
    words: "Mirror, put the weather below the clock and make the date smaller.",
    before: () => ({ h: widget("date").h }),
    check: (answer, before) => {
      if (!answer.acted.includes("arrange_widgets")) return "arrange_widgets did not run";
      if (!widget("weather").visible) return "the weather is hidden";
      if (widget("weather").y < widget("clock").y + widget("clock").h - 20) return `the weather is at y ${widget("weather").y}, the clock ends at ${widget("clock").y + widget("clock").h}`;
      return widget("date").h < before.h ? "" : `the date is still h ${widget("date").h}`;
    },
    note: () => ["clock", "date", "weather"].map((id) => `${id} x ${widget(id).x}, y ${widget(id).y}, w ${widget(id).w}, h ${widget(id).h}`).join("; "),
  },
  {
    words: "Mirror, dim the screen a bit.",
    check: (answer) => (answer.acted.includes("set_brightness") && mirror.state.automation.wakeBrightness < 180 ? "" : `brightness ${mirror.state.automation.wakeBrightness}`),
  },
  {
    words: "Mirror, remember that I like the flowers film in the evening.",
    check: (answer) => (answer.acted.includes("remember") ? "" : "remember did not run"),
  },
  {
    words: "Mirror, move the clock two meters to the left.",
    check: (answer) => (answer.reply.length > 0 && answer.reply.length <= 200 ? "" : "there is no usable reply"),
    note: () => `clock x ${widget("clock").x}`,
  },
  {
    words: "Mirror, how do I look?",
    check: (answer) => (answer.reply.length > 0 ? "" : "there is no reply"),
  },
  {
    words: "Mirror, go to sleep.",
    check: (answer) => (answer.acted.includes("set_power") && mirror.state.automation.sleeping ? "" : "the display is not asleep"),
  },
  // The steps for cards and briefings.
  {
    words: "Mirror, what is on my list?",
    // A list of three or more reads badly as one line; run by itself, the board is filled first.
    before: () => {
      for (const title of ["Buy stamps", "Call the plumber", "Water the plants"].slice(openItems().length)) mirror.state.board.create({ kind: "todo", title }, "try-brain");
      return { open: openItems() };
    },
    check: (answer, before) => {
      if (!isCard(answer) || !answer.acted.includes("present")) return "the list did not come as a card";
      const named = before.open.filter((entry) => names(answer, entry)).length;
      return named >= Math.min(3, before.open.length) ? "" : `the card names ${named} of the ${before.open.length} open items`;
    },
  },
  {
    words: "Mirror, add bread to my list.",
    check: (answer) => {
      if (!item(/bread/i)) return "bread is not on the board";
      return isCard(answer) || answer.acted.includes("present") ? "a confirmation came as a card" : "";
    },
  },
  {
    words: "Mirror, which films do you have?",
    check: (answer) => (["flowers", "seasons", "water"].every((word) => shown(answer).toLowerCase().includes(word)) ? "" : "not all three films are named"),
  },
  {
    words: "Mirror, what did I miss?",
    before: () => post("stretch01", { kind: "reminder", title: "Stretch", due: Date.now() - 2 * 3_600_000 }),
    check: (answer) => {
      if (!answer.acted.includes("briefing")) return "briefing did not run";
      const missed = answer.details.find((row) => row.label === "Missed");
      return missed && /Stretch, 2 hours ago/.test(missed.text) ? "" : "the card has no row for the missed reminder";
    },
  },
  {
    words: "Mirror, dismiss those.",
    check: (answer) => (answer.acted.includes("board_update") && mirror.state.board.find("stretch01")?.done ? "" : "the missed reminder is not marked done"),
    note: () => `Stretch is ${mirror.state.board.find("stretch01")?.done ? "done" : "not done"}`,
  },
  {
    // A greeting in words the mirror's own shortcuts do not catch, so that it reaches the model.
    words: "Good morning, mirror.",
    check: (answer) =>
      answer.acted.includes("briefing") && answer.reply === "Good morning" && isCard(answer) ? "" : "it was not answered with the morning briefing",
  },
  // The mirror's standing settings, asked for the way people say them.
  {
    group: "settings",
    words: "Mirror, use military time.",
    check: (answer) => (answer.acted.includes("set_clock") && mirror.state.clock24Hour ? "" : `the clock is ${mirror.state.clock24Hour ? "24-hour" : "12-hour"}`),
  },
  {
    group: "settings",
    words: "Mirror, turn off at eleven at night and come back on at seven.",
    check: () => {
      const rules = mirror.state.automation;
      return rules.enabled && rules.wakeTime === "07:00" && rules.sleepTime === "23:00" ? "" : `awake ${rules.enabled ? `${rules.wakeTime} to ${rules.sleepTime}` : "always"}`;
    },
    note: () => `awake ${mirror.state.automation.wakeTime} to ${mirror.state.automation.sleepTime}`,
  },
  {
    group: "settings",
    words: "Mirror, when do you turn off at night?",
    // Answered from the state, in the form the clock has by now: 23:00.
    check: (answer) => (/23:00|11(:00)? ?PM/i.test(shown(answer)) && !answer.acted.some((name) => name.startsWith("set_")) ? "" : "the answer does not give the hour from the state"),
  },
  {
    group: "settings",
    words: "Mirror, actually just stay on all night.",
    check: () => (mirror.state.automation.enabled === false ? "" : "the awake hours are still on"),
  },
  {
    group: "settings",
    words: "Mirror, wait ten minutes before you go dark when nobody is around.",
    check: () => {
      const rules = mirror.state.automation;
      return rules.motionEnabled && rules.motionTimeoutSeconds === 600 ? "" : `motion ${rules.motionEnabled}, after ${rules.motionTimeoutSeconds} seconds`;
    },
  },
  {
    group: "settings",
    words: "Mirror, don't go to sleep when the room is empty.",
    check: () => (mirror.state.automation.motionEnabled === false ? "" : "it still sleeps when nobody is there"),
  },
  {
    group: "settings",
    words: "Mirror, show the weather for Portland, Maine.",
    check: (answer) => {
      if (mirror.state.weatherConfig.latitude !== 43.65737) return `the weather is for ${mirror.state.weatherConfig.locationName}`;
      return /Maine/.test(answer.reply) ? "" : "the reply does not say which Portland it took";
    },
    note: () => `weather for ${mirror.state.weatherConfig.locationName}`,
  },
  {
    group: "settings",
    words: "Mirror, I'd rather have Fahrenheit.",
    check: () => (mirror.state.weatherConfig.units === "us" ? "" : `the units are ${mirror.state.weatherConfig.units}`),
  },
  {
    group: "settings",
    words: "Mirror, change the weather to Springfield, Ohio.",
    // No such town is known there: it asks which one, and changes nothing.
    check: (answer) => (mirror.state.weatherConfig.latitude === 43.65737 && answer.reply.length > 0 ? "" : `the weather is for ${mirror.state.weatherConfig.locationName}`),
  },
  {
    group: "settings",
    words: "Mirror, play the flowers film from six in the morning and the still water one from seven in the evening.",
    check: () => {
      const timetable = mirror.state.schedule;
      const short = timetable.slots.map((slot) => `${slot.start} ${slot.videoId.slice(0, 8)}`).sort().join(", ");
      return timetable.enabled && short === "06:00 9c1f44e7, 19:00 e03b77d1" ? "" : `the timetable is ${timetable.enabled ? "on" : "off"}: ${short || "empty"}`;
    },
    note: () => `timetable ${mirror.state.schedule.slots.map((slot) => `${slot.start} ${slot.videoId.slice(0, 8)}`).join(", ")}`,
  },
  {
    group: "settings",
    words: "Mirror, also play the four seasons one from noon.",
    // One time more means the present ones with it.
    check: () => {
      const short = mirror.state.schedule.slots.map((slot) => `${slot.start} ${slot.videoId.slice(0, 8)}`).sort().join(", ");
      return short === "06:00 9c1f44e7, 12:00 546e5d02, 19:00 e03b77d1" ? "" : `the timetable is ${short || "empty"}`;
    },
  },
  {
    group: "settings",
    words: "Mirror, stop changing the film by the clock.",
    before: () => ({ times: mirror.state.schedule.slots.length }),
    // Stopped, not thrown away: its times are kept.
    check: (answer, before) =>
      !mirror.state.schedule.enabled && mirror.state.schedule.slots.length === before.times
        ? ""
        : `the timetable is ${mirror.state.schedule.enabled ? "on" : "off"} with ${mirror.state.schedule.slots.length} of ${before.times} times`,
  },
  {
    group: "settings",
    words: "Mirror, make the background a deep blue.",
    check: () => {
      const background = mirror.state.layout.background;
      const [red, , blue] = [1, 3, 5].map((at) => parseInt(background.primary.slice(at, at + 2), 16));
      return ["solid", "gradient"].includes(background.mode) && blue > red ? "" : `the background is ${background.mode} ${background.primary}`;
    },
    note: () => `background ${mirror.state.layout.background.mode} ${mirror.state.layout.background.primary}`,
  },
  {
    group: "settings",
    words: "Mirror, make the background a gradient from deep navy to purple.",
    check: (answer) => (mirror.state.layout.background.mode === "gradient" ? once(answer, "set_background") : `the background is ${mirror.state.layout.background.mode}`),
    note: () => `gradient ${mirror.state.layout.background.primary} to ${mirror.state.layout.background.secondary}`,
  },
  {
    group: "settings",
    words: "Mirror, show me the next photo.",
    before: () => {
      mirror.state.photos = ["IMG_4001.jpg", "IMG_4002.jpg", "IMG_4003.jpg"].map((name) => ({ name, sizeBytes: 204800 }));
      return { widgets: boxes() };
    },
    // The photo behind everything is meant; the widgets are left as they are.
    check: (answer, before) => {
      if (mirror.state.layout.background.mode !== "photo") return `the background is ${mirror.state.layout.background.mode}`;
      return boxes() === before.widgets ? once(answer, "set_background") : `the widgets were changed too: ${changedWidgets(before.widgets)}`;
    },
  },
  {
    group: "settings",
    words: "Mirror, it's hard to read against the picture, darken it a bit.",
    check: () => (mirror.state.layout.background.dim > 0 && mirror.state.layout.background.mode === "photo" ? "" : `dim ${mirror.state.layout.background.dim}, mode ${mirror.state.layout.background.mode}`),
    note: () => `darkened by ${mirror.state.layout.background.dim}%`,
  },
  {
    group: "settings",
    // With a timetable on, the film is the timetable's: going back to it must not choose one by hand.
    words: "Mirror, go back to the film, without the darkening.",
    before: () => {
      mirror.state.schedule.enabled = true;
      return { film: mirror.state.activeFilm, widgets: boxes() };
    },
    check: (answer, before) => {
      const background = mirror.state.layout.background;
      if (background.mode !== "video" || background.dim !== 0) return `the background is ${background.mode}, darkened by ${background.dim}%`;
      if (mirror.state.hold || mirror.state.activeFilm !== before.film) return "a film was chosen by hand over the timetable";
      return boxes() === before.widgets ? once(answer, "set_background") : `the widgets were changed too: ${changedWidgets(before.widgets)}`;
    },
  },
  {
    group: "settings",
    words: "Mirror, make the clock and the text a warm amber.",
    check: () => {
      const color = mirror.state.layout.textColor;
      const [red, , blue] = [1, 3, 5].map((at) => parseInt(color.slice(at, at + 2), 16));
      return color !== "#f5f2ec" && red > blue + 40 ? "" : `the text is ${color}`;
    },
    note: () => `text ${mirror.state.layout.textColor}, accent ${mirror.state.layout.accentColor}`,
  },
  {
    group: "settings",
    words: "Mirror, put the text back to normal.",
    check: () => (mirror.state.layout.textColor === "#f5f2ec" ? "" : `the text is ${mirror.state.layout.textColor}`),
  },
  {
    group: "settings",
    words: "Mirror, I want to call you Hallway from now on.",
    // The name on the glass can change; the word that wakes it cannot, and the reply should not promise otherwise.
    check: (answer) => (mirror.state.displayName === "Hallway" && /mirror/i.test(answer.reply) ? "" : `the name is ${mirror.state.displayName}; the reply does not say that "Mirror" still wakes it`),
  },
  {
    group: "settings",
    words: "Mirror, stop greeting me every time I walk up.",
    check: (answer) => (answer.acted.includes("habits") && running.habits.get().greet === false ? "" : "it still greets"),
  },
  {
    group: "settings",
    words: "Mirror, don't show me anything by yourself after nine at night until seven in the morning.",
    check: () => (JSON.stringify(running.habits.get().quietHours) === '["21:00","07:00"]' ? "" : `quiet hours ${JSON.stringify(running.habits.get().quietHours)}`),
  },
  {
    group: "settings",
    words: "Mirror, what do you do on your own?",
    check: (answer) => (answer.acted.includes("habits") && shown(answer).length > 0 ? "" : "it did not look its habits up"),
  },
  {
    group: "settings",
    words: "Mirror, change the wifi password.",
    check: (answer) => (/phone|controls|can't|cannot/i.test(answer.reply) && !answer.acted.some((name) => name.startsWith("set_")) ? "" : "it did not say that this is not its to change"),
  },
  // Moments: what the mirror puts on the glass for a while, beside its line.
  {
    group: "moments",
    words: "Mirror, set a timer for five minutes.",
    before: () => {
      mirror.state.moments = [];
    },
    // Both: the reminder that is announced when due, and a countdown that can be watched.
    check: (answer) => {
      const [running] = moments("countdown");
      if (!running) return "no countdown is on the glass";
      const minutes = (running.endsAt - Date.now()) / 60_000;
      if (minutes < 4.6 || minutes > 5.1) return `the countdown runs out in ${minutes.toFixed(1)} minutes`;
      const reminder = mirror.state.board.items.find((candidate) => candidate.kind === "reminder" && Math.abs(candidate.due - running.endsAt) < 20_000);
      if (!reminder) return "no reminder on the board falls due when the countdown ends";
      const more = answer.acted.filter((name) => !["show_moment", "board_add"].includes(name));
      return more.length > 0 ? `it also called ${more.join(", ")}` : "";
    },
    note: momentsNote,
  },
  {
    group: "moments",
    words: "Mirror, what time is it?",
    before: () => ({ showing: moments().length }),
    // A plain answer needs nothing beside it.
    check: (answer, before) => (moments().length === before.showing && !answer.acted.includes("show_moment") ? "" : "a moment was shown for a plain answer"),
  },
  {
    group: "moments",
    words: "Mirror, stop the timer.",
    before: () => ({ ends: moments("countdown")[0]?.endsAt }),
    check: (answer, before) => {
      if (moments("countdown").length > 0) return "the countdown is still on the glass";
      const reminder = mirror.state.board.items.find((candidate) => candidate.kind === "reminder" && !candidate.done && Math.abs(candidate.due - before.ends) < 20_000);
      return reminder ? "its reminder is still on the board" : "";
    },
  },
  {
    group: "moments",
    words: "Mirror, write happy birthday Sam really big.",
    check: () => {
      const [words] = moments("text");
      if (!words || !/birthday/i.test(words.text)) return "no words about a birthday are on the glass";
      return words.size === "large" ? "" : `they are ${words.size}`;
    },
    note: momentsNote,
  },
  {
    group: "moments",
    words: "Mirror, show me how to make pour-over coffee, step by step.",
    check: () => (moments("list")[0]?.rows.length >= 3 ? "" : "no list of steps is on the glass"),
    note: momentsNote,
  },
  {
    group: "moments",
    words: "Mirror, show me how the temperature goes over the next hours.",
    check: () => {
      const [chart] = moments("chart");
      if (!chart || chart.values.length < 3) return "no chart of the hours ahead is on the glass";
      // The numbers of the state, not made up: the stand-in's hours go from 12 to 16 degrees Celsius,
      // which earlier requests of a whole run may have turned into Fahrenheit.
      const [least, most] = mirror.state.weatherConfig.units === "us" ? [53, 62] : [12, 17];
      return chart.values.every((entry) => entry.value >= least && entry.value <= most) ? "" : "the values are not those of the state";
    },
    note: momentsNote,
  },
  {
    group: "moments",
    words: "Mirror, draw me a heart.",
    check: () => {
      const [drawing] = moments("drawing");
      if (!(drawing?.shapes.length >= 1)) return "no drawing is on the glass";
      // The glass of 2015 has no glyph for a pictograph.
      return /[^\x20-\x7e\u00a0-\u024f]/.test(drawing.title ?? "") ? `its title is "${drawing.title}"` : "";
    },
    note: momentsNote,
  },
  {
    group: "moments",
    words: "Mirror, make it red and put it at the top.",
    before: () => ({ id: moments("drawing")[0]?.id }),
    check: (answer, before) => {
      const drawings = moments("drawing");
      if (drawings.length !== 1 || drawings[0].id !== before.id) return "the drawing was not replaced under its id";
      const [red, green] = [1, 3].map((at) => parseInt((drawings[0].color ?? drawings[0].shapes[0].stroke ?? drawings[0].shapes[0].fill ?? "#000000").slice(at, at + 2), 16));
      if (!(red > 150 && red > green + 60)) return "it is not red";
      return drawings[0].height === "top" ? "" : `it is at ${drawings[0].height ?? "no named height"}`;
    },
    note: momentsNote,
  },
  {
    group: "moments",
    words: "Mirror, take all of those down again.",
    before: () => ({ widgets: boxes() }),
    // What it put there goes; the widgets and the board were not asked about.
    check: (answer, before) => {
      if (moments().length > 0 || !answer.acted.includes("end_moment")) return `still showing: ${momentsNote()}`;
      return boxes() === before.widgets ? "" : `the widgets were changed too: ${changedWidgets(before.widgets)}`;
    },
  },
  {
    group: "settings",
    words: "Mirror, we moved to Denver, fix the clock.",
    check: (answer) => (answer.acted.includes("set_clock") && mirror.state.timeZone === "America/Denver" ? "" : `the zone is ${mirror.state.timeZone}`),
    note: () => `zone ${mirror.state.timeZone}, offset ${mirror.state.utcOffsetMinutes} minutes`,
  },
];

console.log(`Model ${options.model} of ${options.provider}, reasoning effort ${options.effort}. The fake mirror's zone is ${options.zone} (UTC offset ${offset} minutes); its clock reads ${local(Date.now())}.`);
process.stdout.write("Waiting for the model");
for (let waited = 0; !(await health()).brain.ready; waited++) {
  if (waited >= 90) {
    console.log(`\nThe model did not become ready: ${(await health()).brain.detail}`);
    await running.stop();
    process.exit(1);
  }
  process.stdout.write(".");
  await new Promise((resolve) => setTimeout(resolve, 1000));
}
console.log(" ready.\n");

const failures = [];
const totals = [];
for (const step of steps) {
  const wanted = (word) => step.group === word || step.words.toLowerCase().includes(word);
  if (options.only && !options.only.split(",").some((word) => wanted(word.trim().toLowerCase()))) continue;
  if (mirror.state.automation.sleeping) mirror.state.automation.sleeping = false;
  const before = step.before?.();
  const written = mirror.writes().length;
  const answer = await say(step.words, step.addressed);
  const problem = step.check(answer, before);
  totals.push(answer.ms.agent);
  console.log(`> ${step.words}${step.addressed ? `  (${step.addressed})` : ""}`);
  console.log(`  heard:  ${answer.heard}`);
  console.log(`  tools:  ${answer.acted.join(", ") || "none"}`);
  console.log(`  reply:  ${answer.ignored ? `(ignored: ${answer.reason})` : answer.reply}${answer.listen ? "  [listens for an answer]" : ""}`);
  printRows(answer);
  console.log(`  time:   agent ${answer.ms.agent} ms, total ${answer.ms.total} ms; reply ${answer.reply.length} characters`);
  if (options.calls) {
    for (const call of logLines.splice(0).map((line) => JSON.parse(line)).filter((entry) => entry.event === "brain.model_call" || entry.event === "tool")) {
      console.log(
        call.event === "tool"
          ? `          tool ${call.tool}: ${call.ms} ms`
          : `          model: ${call.ms} ms, first token after ${call.firstTokenMs} ms; tokens in ${call.inputTokens} (cached ${call.cachedTokens}), out ${call.outputTokens}, reasoning ${call.reasoningTokens}`,
      );
    }
  }
  // What the request changed on the mirror, so that a change nobody asked for is seen.
  for (const write of mirror.writes().slice(written)) {
    if (write.path === "/api/v1/assistant/say") continue;
    console.log(`  wrote:  ${write.method} ${write.path}${write.body ? ` ${JSON.stringify(write.body).slice(0, 160)}` : ""}`);
  }
  if (step.note) console.log(`  mirror: ${step.note()}`);
  if (answer.error) console.log(`  error:  ${answer.error}`);
  console.log(`  check:  ${problem ? `NOT AS EXPECTED: ${problem}` : "as expected"}\n`);
  if (problem) failures.push(step.words);
}

// The settings steps leave the clock, the rules and the habits changed; what follows is tried as it was before them.
Object.assign(mirror.state, { timeZone: options.zone, utcOffsetMinutes: offset, clock24Hour: false, displayName: "Mirror" });
Object.assign(mirror.state.automation, { enabled: true, wakeTime: "06:30", sleepTime: "23:00", motionEnabled: true, sleeping: false });
mirror.state.schedule = { enabled: false, slots: [] };
mirror.state.moments = [];
mirror.state.layout.background.mode = "video";
running.habits.change({ greet: true, tend: true, quietHours: null });

// What is answered without the model: the greetings the mirror recognises by itself.
if (!options.only) {
  for (const [name, words] of [["good-morning", "good morning"], ["good-night", "good night"]]) {
    const answer = await shortcut(name, words);
    console.log(`> (the shortcut ${name})`);
    console.log(`  reply:  ${answer.reply}`);
    printRows(answer);
    console.log(`  time:   total ${answer.ms.total} ms, shown for ${answer.seconds} seconds\n`);
  }
}

// The two runs the companion makes by itself, against the real model.
if (!options.only) {
  mirror.state.automation.sleeping = false;
  const startedGreeting = Date.now();
  const greeting = await running.proactive.greet({ asleepSeconds: 5400 });
  console.log(`> (someone walks up after 90 minutes)`);
  console.log(`  outcome: ${greeting}; caption: ${mirror.state.said.findLast((caption) => caption.kind === "notice")?.text ?? "(none)"}`);
  console.log(`  time:    ${Date.now() - startedGreeting} ms\n`);
  const startedTending = Date.now();
  const filmBefore = film();
  const tending = await running.proactive.tend();
  console.log(`> (the hourly tending run)`);
  console.log(`  outcome: ${tending}; film ${filmBefore === film() ? "unchanged" : `changed to ${film()}`}; clock y ${widget("clock").y}`);
  console.log(`  time:    ${Date.now() - startedTending} ms\n`);
}

const sorted = [...totals].sort((a, b) => a - b);
console.log(
  `${totals.length - failures.length} of ${totals.length} as expected. Agent time: median ${sorted[Math.floor(sorted.length / 2)] ?? 0} ms, longest ${sorted.at(-1) ?? 0} ms.`,
);
if (failures.length > 0) console.log(`Not as expected:\n${failures.map((words) => `  ${words}`).join("\n")}`);
const said = mirror.state.said.filter((caption) => caption.kind === "reply");
if (said.length > 0) console.log(`Captions sent with the say tool during requests: ${said.map((caption) => `"${caption.text}"`).join(", ")}`);

await running.stop();
await mirror.close();
fs.rmSync(stateDir, { recursive: true, force: true });
process.exit(failures.length > 0 ? 1 : 0);
