// Tries the real speech-to-text worker on a folder of recordings and prints
// each transcript with the time it took. If the folder holds a sentences.json
// that says what each recording says (the part of a file's name before its
// first dash is the key), the transcripts are compared with it.
//
//   node scripts/try-stt.mjs DIR [--python PATH] [--model NAME] [--device auto|cuda|cpu] [--models DIR]
//
// Without the options, the companion's config says which Python, model and
// device to use.

import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import { withoutName } from "../src/assistant.js";
import { defaultPython, expandHome, loadConfig } from "../src/config.js";
import { createStt } from "../src/stt.js";
import { readWav } from "../src/wav.js";

const { values: options, positionals } = parseArgs({
  options: {
    python: { type: "string" },
    model: { type: "string" },
    device: { type: "string" },
    models: { type: "string" },
  },
  allowPositionals: true,
});
if (positionals.length !== 1) {
  console.log("Give the folder with the recordings: node scripts/try-stt.mjs DIR");
  process.exit(2);
}
const folder = positionals[0];

let config = null;
try {
  config = loadConfig();
} catch (error) {
  if (!options.python && !options.models) {
    console.log(`${error.message}\nOr say where things are: --python PATH --models DIR`);
    process.exit(1);
  }
}
const stateDir = config?.stateDir ?? expandHome("~/.local/state/mirror-companion");
const settings = {
  python: options.python ? expandHome(options.python) : config?.stt.python ?? defaultPython(stateDir),
  model: options.model ?? config?.stt.model ?? "small.en",
  device: options.device ?? config?.stt.device ?? "auto",
  modelsDir: options.models ? expandHome(options.models) : path.join(stateDir, "models"),
};

const files = fs.readdirSync(folder).filter((name) => name.toLowerCase().endsWith(".wav")).sort();
if (files.length === 0) {
  console.log(`There are no WAV files in ${folder}.`);
  process.exit(1);
}
let expected = {};
try {
  expected = JSON.parse(fs.readFileSync(path.join(folder, "sentences.json"), "utf8"));
} catch {
  // Without the file the transcripts are printed and not judged.
}

/** Lower case, no punctuation, digits as words where the clips use them: what counts when comparing. */
function plain(text) {
  return text
    .toLowerCase()
    .replace(/\b7(:00)?\b/g, "seven")
    .replace(/-/g, " ")
    .replace(/[^a-z0-9 ]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

const events = [];
const stt = createStt({ ...settings, log: (event, fields) => events.push({ event, ...fields }) });
const started = Date.now();
stt.start();
process.stdout.write(`Loading ${settings.model} (device ${settings.device}) with ${settings.python}`);
while (!stt.health().ready) {
  const stopped = events.find((entry) => entry.event === "stt.stopped");
  if (stopped) {
    console.log(`\nThe worker did not start: ${stopped.detail}`);
    for (const entry of events.filter((candidate) => candidate.event === "stt.stderr").slice(-5)) console.log(`  ${entry.line}`);
    await stt.stop();
    process.exit(1);
  }
  process.stdout.write(".");
  await new Promise((resolve) => setTimeout(resolve, 500));
}
const health = stt.health();
console.log(` ready in ${Date.now() - started} ms on ${health.device}.${health.detail ? ` ${health.detail}` : ""}\n`);

const times = [];
const score = { near: { right: 0, of: 0 }, far: { right: 0, of: 0 } };
for (const name of files) {
  const sound = readWav(fs.readFileSync(path.join(folder, name)));
  if (sound.problem) {
    console.log(`${name}: ${sound.problem}`);
    continue;
  }
  const began = Date.now();
  let text;
  try {
    text = (await stt.transcribe(sound.pcm, { timeoutMs: 120_000 })).text;
  } catch (error) {
    console.log(`${name}: ${error.message}`);
    continue;
  }
  const ms = Date.now() - began;
  times.push(ms);
  const wanted = expected[name.split("-")[0]];
  let verdict = "";
  if (wanted) {
    const kind = name.includes("-far") ? "far" : "near";
    score[kind].of += 1;
    // The companion takes the mirror's name off the start, so it is not counted here either.
    const right = plain(withoutName(text)) === plain(withoutName(wanted));
    if (right) score[kind].right += 1;
    verdict = right ? "  [as said]" : `  [said: ${wanted}]`;
  }
  console.log(`${name}  ${sound.seconds.toFixed(1)} s of sound, ${ms} ms: ${text}${verdict}`);
}

if (times.length > 0) {
  // The first transcription after loading is slower than the rest, so it is left out of the typical time.
  const later = times.length > 1 ? times.slice(1) : times;
  const sorted = [...later].sort((a, b) => a - b);
  console.log(`\n${times.length} recordings. Time for one: median ${sorted[Math.floor(sorted.length / 2)]} ms, longest ${sorted.at(-1)} ms, first ${times[0]} ms.`);
  for (const kind of ["near", "far"]) {
    if (score[kind].of > 0) console.log(`Word for word as said, ${kind}: ${score[kind].right} of ${score[kind].of}.`);
  }
}
await stt.stop();
