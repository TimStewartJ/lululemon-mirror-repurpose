// Tries the real model against a fake mirror: says a set of requests to a
// companion that runs in this process, and prints what was heard, which
// tools ran, the reply and the timings, with a check of each outcome.
// It needs the Copilot CLI login of the account it runs under, and nothing
// else: speech-to-text is replaced by a stand-in, and no real mirror is used.
//
//   node scripts/try-brain.mjs [--model NAME] [--effort LEVEL] [--offset MINUTES] [--zone NAME]
//                              [--only WORD] [--calls] [--verbose]
//
// --only runs the requests that contain one of the words, given with commas between them; --calls adds one line per
// call to the model; --verbose prints the companion's whole log.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { parseArgs } from "node:util";
import { parseConfig } from "../src/config.js";
import { createLog } from "../src/log.js";
import { serve } from "../src/serve.js";
import { localIso } from "../src/time.js";
import { startFakeMirror } from "../tests/fakes/mirror.js";
import { fakeStt, speech } from "../tests/fakes/stt.js";

const { values: options } = parseArgs({
  options: {
    model: { type: "string", default: "gpt-6-luna" },
    offset: { type: "string", default: "-420" },
    zone: { type: "string", default: "America/Los_Angeles" },
    only: { type: "string" },
    effort: { type: "string", default: "low" },
    calls: { type: "boolean", default: false },
    verbose: { type: "boolean", default: false },
  },
});
const offset = Number(options.offset);
const SECRET = "try-brain-secret-not-a-real-one";

const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "companion-try-"));
const mirror = await startFakeMirror({ timeZone: options.zone, utcOffsetMinutes: offset });
const config = parseConfig({
  secret: SECRET,
  mirror: { host: mirror.host, port: mirror.port, token: mirror.token },
  model: options.model,
  reasoningEffort: options.effort,
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

const widget = (id) => mirror.widget(id);
const film = () => mirror.state.films.find((candidate) => candidate.id === mirror.state.activeFilm).name;
const item = (pattern) => mirror.state.board.items.find((candidate) => pattern.test(candidate.title));
const local = (ms) => localIso(ms, offset);
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
    check: (answer) => (/trash/i.test(answer.reply) ? "" : "the reply does not mention the trash"),
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
    check: (answer) => (answer.acted.length === 0 && /rain|16|12|overcast/i.test(answer.reply) ? "" : "the reply does not give the weather from the state"),
  },
  {
    words: "Mirror, remind me to call the dentist at three this afternoon.",
    check: () => {
      const reminder = item(/dentist/i);
      if (!reminder?.due) return "no reminder about the dentist is on the board";
      const due = local(reminder.due);
      return due.startsWith(dayAfter(0, "15:00")) ? "" : `it is due ${due}`;
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
];

console.log(`Model ${options.model}, reasoning effort ${options.effort}. The fake mirror's zone is ${options.zone} (UTC offset ${offset} minutes); its clock reads ${local(Date.now())}.`);
process.stdout.write("Waiting for GitHub Copilot");
for (let waited = 0; !(await health()).brain.ready; waited++) {
  if (waited >= 90) {
    console.log(`\nGitHub Copilot did not become ready: ${(await health()).brain.detail}`);
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
  if (options.only && !options.only.split(",").some((word) => step.words.toLowerCase().includes(word.trim().toLowerCase()))) continue;
  if (mirror.state.automation.sleeping) mirror.state.automation.sleeping = false;
  const before = step.before?.();
  const answer = await say(step.words, step.addressed);
  const problem = step.check(answer, before);
  totals.push(answer.ms.agent);
  console.log(`> ${step.words}${step.addressed ? `  (${step.addressed})` : ""}`);
  console.log(`  heard:  ${answer.heard}`);
  console.log(`  tools:  ${answer.acted.join(", ") || "none"}`);
  console.log(`  reply:  ${answer.ignored ? `(ignored: ${answer.reason})` : answer.reply}${answer.listen ? "  [listens for an answer]" : ""}`);
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
  if (step.note) console.log(`  mirror: ${step.note()}`);
  if (answer.error) console.log(`  error:  ${answer.error}`);
  console.log(`  check:  ${problem ? `NOT AS EXPECTED: ${problem}` : "as expected"}\n`);
  if (problem) failures.push(step.words);
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
