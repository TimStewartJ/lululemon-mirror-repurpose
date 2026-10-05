import { createHabits } from "./habits.js";
import { describeError } from "./log.js";
import { MirrorRefused, MirrorUnreachable } from "./mirror.js";
import { boardTools } from "./tools/board.js";
import { cardTools } from "./tools/card.js";
import { characterTools } from "./tools/character.js";
import { displayTools } from "./tools/display.js";
import { layoutTools } from "./tools/layout.js";
import { settingsTools } from "./tools/settings.js";
import { talkTools } from "./tools/talk.js";

/**
 * A tool the model may call. Independent of any agent harness: a harness
 * turns the name, description and schema into its own form and calls
 * {@link runTool}.
 *
 * @typedef {Object} Tool
 * @property {string} name
 * @property {string} description
 * @property {import("zod").ZodType} schema
 * @property {boolean} [changes] True when it alters what the mirror shows or holds.
 * @property {boolean} [endsTurn] True when a successful call leaves nothing more to say.
 * @property {(args: any, turn: Turn) => Promise<object>} handler
 */

/**
 * What one run of the agent shares with its tools.
 *
 * @typedef {Object} Turn
 * @property {"conversation"|"greeting"|"tend"} kind
 * @property {string[]} acted Names of the tools that ran, in order.
 * @property {string|null} ignored The reason given to the ignore tool, if it ran.
 * @property {boolean} [certain] True when the words were surely meant for the mirror: typed, or begun with its name.
 * @property {boolean} closed Set when the run is over; a tool call that comes later is refused.
 * @property {number} changes How many tools altered the mirror in this run.
 * @property {number} [deadline] After this moment a greeting may no longer speak.
 * @property {string} [said] The line the say tool put on the glass, if it did.
 * @property {{ reply: string, details: { label: string, text: string }[], seconds?: number }} [card]
 *   The card that is the answer of this turn, when the briefing or the present tool made one.
 * @property {{ briefing: import("./briefing.js").Briefing, state: import("./state.js").MirrorState }} [briefing]
 *   The briefing the briefing tool gave, with the state it was built from.
 * @property {boolean} [cardRefused] Set when a card that broke the limits was handed back to be written again.
 * @property {import("./state.js").MirrorState|null} state The mirror's state as last fetched.
 * @property {number} stateAt When that state was fetched, on the companion's clock.
 * @property {Promise<void>} [busy] Settles when the tool call now running for this turn is over.
 */

/** @returns {Turn} */
export function newTurn(kind, state = null, stateAt = 0) {
  return { kind, acted: [], ignored: null, closed: false, changes: 0, state, stateAt };
}

// In a conversation the answer itself is the line on the glass. Given the say
// tool as well, the model answered through it and the line was shown twice.
const NOT_FOR_KIND = { conversation: ["say"] };
const FOR_KIND = {
  conversation: null,
  greeting: ["say"],
  tend: ["get_state", "set_background", "arrange_widgets", "board_remove"],
};

/**
 * Every tool, bound to the mirror and the memory it works on.
 *
 * @param {Object} context
 * @param {import("./mirror.js").Mirror} context.mirror
 * @param {ReturnType<import("./memory.js").createMemory>} context.memory
 * @param {import("./clock.js").Clock} context.clock
 * @param {(event: string, fields?: object) => void} context.log
 * @param {import("./habits.js").Habits} [context.habits] What the companion does unasked; without them, habits that
 *   are all on and are kept in memory only.
 * @returns {Tool[]}
 */
export function createTools(context) {
  const habits =
    context.habits ??
    createHabits({ settings: { greet: true, morningBriefing: true, reminders: true, tend: true, tendMinutes: 60, quietHours: ["22:30", "06:30"] } });
  return [
    ...displayTools(context),
    ...characterTools(context),
    ...layoutTools(context),
    ...settingsTools({ ...context, habits }),
    ...boardTools(context),
    ...cardTools(context),
    ...talkTools(context),
  ];
}

/** The tools a kind of run may use: a greeting may only speak, a tending run may only tidy. */
export function toolsFor(kind, tools) {
  const allowed = FOR_KIND[kind];
  const kept = (NOT_FOR_KIND[kind] ?? []);
  return tools.filter((tool) => (allowed ? allowed.includes(tool.name) : !kept.includes(tool.name)));
}

/**
 * Runs one tool call for the model. Never throws: whatever goes wrong comes
 * back as {error}, in words the model can act on or pass to the person.
 *
 * A model may ask for several calls in one breath, and a harness may start
 * them side by side. The calls of a turn are run one after the other, in
 * the order they came: the mirror takes its layout and its display rules as
 * a whole, so two calls that each read, change and store them would undo
 * one another, and the one change a tending run may make is counted before
 * the next call is looked at.
 *
 * @param {Tool} tool
 * @param {unknown} args As the model gave them.
 * @param {Turn} turn
 * @param {(event: string, fields?: object) => void} [log]
 */
export async function runTool(tool, args, turn, log = () => {}) {
  const before = turn.busy ?? Promise.resolve();
  let done;
  turn.busy = new Promise((resolve) => (done = resolve));
  await before;
  try {
    return await runOne(tool, args, turn, log);
  } finally {
    done();
  }
}

async function runOne(tool, args, turn, log) {
  if (turn.closed) return { error: "This request is over. Do nothing more." };
  turn.acted.push(tool.name);
  if (turn.kind === "tend" && tool.changes && turn.changes >= 1) {
    return { error: "A tending run may change one thing, and it has. Stop here." };
  }
  const parsed = tool.schema.safeParse(args ?? {});
  if (!parsed.success) {
    const problems = parsed.error.issues.map((issue) => `${issue.path.join(".") || "arguments"}: ${issue.message}`);
    // A call the model has to make again costs the person seconds, so it is worth seeing what was asked for.
    log("tool", { tool: tool.name, ok: false, ms: 0, refused: problems.join("; "), args: brief(args) });
    return { error: `The arguments are not right. ${problems.join("; ")}.` };
  }
  const started = Date.now();
  let result;
  try {
    result = await tool.handler(parsed.data, turn);
  } catch (error) {
    if (error instanceof MirrorUnreachable) {
      result = { error: "I can't reach the display right now. Nothing was changed. Tell the person so." };
    } else if (error instanceof MirrorRefused) {
      result = { error: `The mirror refused: ${error.message}` };
    } else {
      result = { error: `The tool failed: ${describeError(error)}. Nothing more can be done about it now.` };
    }
    log("tool.failed", { tool: tool.name, detail: describeError(error) });
  }
  if (tool.changes && !result.error) turn.changes += 1;
  const outcome = { tool: tool.name, ok: !result.error, ms: Date.now() - started };
  if (result.error) Object.assign(outcome, { refused: String(result.error).slice(0, 200), args: brief(args) });
  log("tool", outcome);
  return result;
}

/** The arguments of a call that was refused, short enough for a log line. */
function brief(args) {
  try {
    return JSON.stringify(args ?? {}).slice(0, 300);
  } catch {
    return "(not printable)";
  }
}
