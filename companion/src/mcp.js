import readline from "node:readline";
import { z } from "zod";
import { MirrorRefused } from "./mirror.js";
import { CARD, headline, oneLine } from "./reply.js";
import { fetchState } from "./state.js";
import { newTurn, runTool } from "./tools.js";

// The Model Context Protocol, for programs on the home network that want to
// use the mirror: an agent on another machine, a home hub, a script. They get
// the tools the mirror's own assistant has, by the same handlers. Only tools
// are offered, and each request is answered by itself, so no session is kept.

/** The versions of the protocol this speaks, newest first. For a server with tools only they do not differ. */
export const PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"];

/** The tools of the assistant that a program on the network gets as they are. */
const OFFERED = [
  "get_state",
  "look",
  "set_power",
  "set_brightness",
  "set_background",
  "set_character",
  "set_answer_place",
  "arrange_widgets",
  "show_moment",
  "end_moment",
  "set_clock",
  "set_display_rules",
  "set_weather",
  "set_film_schedule",
  "set_text_color",
  "set_name",
  "board_add",
  "board_update",
  "board_remove",
];

/**
 * The tools of the assistant that a program on the network does not get, and
 * why. A tool that is in neither list is not offered either: one that is
 * added later reaches the network only when somebody puts it in the list above.
 */
export const KEPT_BACK = {
  say: "It speaks as the assistant in its own turn; programs get a say of their own, which shows a notice.",
  briefing: "It makes a card the answer of the assistant's turn; a program reads the state and shows what it likes.",
  present: "It makes a card the answer of the assistant's turn; programs give rows to say.",
  ignore: "It ends a turn of the assistant silently.",
  remember: "What the household told the mirror goes into every conversation; a program is not to write there.",
  forget: "As remember.",
  habits: "What the companion does unasked is for its owner to say.",
};

const REMOVES = new Set(["board_remove"]);
const ASKS_THE_INTERNET = new Set(["set_weather", "ask"]);

// How long the mirror's clock and offset, read for one call, serve the calls that follow.
const STATE_KEPT_MS = 10_000;
// A program may call the same tool every few seconds, a score that changes; the record of what was done keeps one line of it.
const NOTE_EVERY_MS = 10 * 60_000;
// The mirror waits up to 55 seconds for its assistant.
const ASK_WAITS_MS = 58_000;

const INSTRUCTIONS = [
  "This is a MIRROR: a tall mirror with a display behind its glass, hanging in a home and running Mirror Home. " +
    "These tools change what it shows. People read it from across a room, and nobody touches it.",
  "Read it first with get_state. The other tools call what it returns the state: the mirror's own time and offset, the widgets, the films, the board.",
  "On this glass black is mirror: dark things vanish into the reflection, pale and thin ones read best. It is portrait, 1080 by 1920 pixels. " +
    "It draws letters, digits and plain punctuation; no emoji and no markdown.",
  "To show something for a while (a countdown, a few large words, a list, a chart, a drawing) use show_moment: it leaves by itself. " +
    "To keep something for people (a note, a to-do, a reminder) use the board. For one line or a small card now, use say.",
  "Times are the mirror's: ISO 8601 with the mirror's UTC offset, worked out from now in the state.",
  "Leave the display as dark or as lit as it is unless asked otherwise: set_power holds for four hours, and a dark display at night is meant to be dark.",
  "Whoever is in the room sees a change at once. Change what was asked for and nothing else.",
  "ask hands a wish in plain words to the mirror's own assistant, which acts on it and answers on the glass.",
].join("\n");

/** A request the protocol does not allow, with the code JSON-RPC has for it. */
class RpcError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

/** A failed request as JSON-RPC answers it. */
export function rpcFailure(id, code, message) {
  return { jsonrpc: "2.0", id: id ?? null, error: { code, message } };
}

/**
 * The two tools that only a program on the network has: a line or a small
 * card on the glass, and a wish in words for the mirror's assistant.
 *
 * @returns {import("./tools.js").Tool[]}
 */
function ownTools({ mirror }) {
  return [
    {
      name: "say",
      description:
        "Shows one line on the glass now, where the mirror's assistant shows its answers: something worth telling whoever is in the room. " +
        "text is one line of 1 to 200 characters. rows makes it a small card: " +
        `up to ${CARD.rows} rows under the line, each a short label (at most ${CARD.label} characters: a time, a day, a word or two, or nothing) ` +
        `beside one line of text (1 to ${CARD.text} characters); with rows, text is the headline and has at most ${CARD.headline} characters. ` +
        "seconds (2 to 30) is how long it stays; left out, as long as its length needs. Plain text: no markdown, no emoji. " +
        "A dark display shows nothing and is not woken by this; the answer then says so. " +
        "For something that should stay longer than half a minute, use show_moment or the board.",
      schema: z.object({
        text: z.string().min(1).max(200),
        rows: z
          .array(z.object({ label: z.string().max(CARD.label).optional(), text: z.string().min(1).max(CARD.text) }))
          .max(CARD.rows)
          .optional(),
        seconds: z.number().int().min(2).max(30).optional(),
      }),
      changes: true,
      async handler({ text, rows, seconds }) {
        const details = (rows ?? []).map((row) => ({ label: oneLine(row.label ?? "", CARD.label), text: oneLine(row.text, CARD.text) }));
        if (details.some((row) => !row.text)) return { error: "A row has no text that this glass can draw. Nothing was shown." };
        const body = { text: headline(text, details), kind: "notice" };
        if (!body.text) return { error: "text has nothing that this glass can draw. Nothing was shown." };
        if (seconds) body.seconds = seconds;
        if (details.length > 0) body.details = details;
        const answer = await mirror.call("POST", "/api/v1/assistant/say", { body });
        if (answer.shown) return { shown: true };
        const unseen = { shown: false, reason: answer.reason || "unknown" };
        if (answer.reason === "sleeping") unseen.note = "The display is dark, so nobody saw it. It was not woken.";
        return unseen;
      },
    },
    {
      name: "ask",
      description:
        "Hands a wish in plain words to the mirror's own assistant, as if it had been typed in the mirror's phone controls: " +
        "the assistant works out what is meant, acts on the mirror, and shows its answer on the glass. " +
        "Use it when a wish is easier said than done with the other tools, or when the mirror itself should answer the people in the room. " +
        "text is 1 to 500 characters. Returns what the assistant answered and which of its tools it used. It can take half a minute. " +
        "It needs the assistant switched on in the mirror's controls and a companion that has a model.",
      schema: z.object({ text: z.string().min(1).max(500) }),
      changes: true,
      // The mirror passes the words to its companion, which gives them a turn of their own: this call must not hold one meanwhile.
      alone: true,
      async handler({ text }) {
        const words = text.trim();
        if (!words) return { error: "text has no words." };
        let answer;
        try {
          answer = await mirror.call("POST", "/api/v1/assistant/ask", { body: { text: words }, responseTimeoutMs: ASK_WAITS_MS });
        } catch (error) {
          if (!(error instanceof MirrorRefused)) throw error;
          if (error.status === 404) return { error: "This mirror's software has no assistant to ask. That comes with a later version of Mirror Home." };
          if (error.status === 503) return { error: `The mirror's assistant gave no answer: ${error.message.replace(/[.\s]+$/, "")}.` };
          throw error;
        }
        const seen = { reply: answer.reply ?? "" };
        if (answer.details?.length > 0) seen.rows = answer.details;
        if (answer.acted?.length > 0) seen.used = answer.acted;
        if (answer.error) seen.trouble = String(answer.error);
        return seen;
      },
    },
  ];
}

/** A tool as tools/list names it: its schema as JSON Schema, and what kind of thing it does. */
function listed(tool) {
  const { $schema: _dialect, ...inputSchema } = tool.schema.toJSONSchema({ io: "input" });
  return {
    name: tool.name,
    description: tool.outside ?? tool.description,
    inputSchema,
    annotations: {
      readOnlyHint: !tool.changes,
      destructiveHint: REMOVES.has(tool.name),
      openWorldHint: ASKS_THE_INTERNET.has(tool.name),
    },
  };
}

/** What a tool answered, as tools/call returns it: a refusal as an error the model reads, a picture as a picture. */
function callResult(result) {
  if (result.error) return { content: [{ type: "text", text: String(result.error) }], isError: true };
  if (result.image) {
    return {
      content: [
        { type: "text", text: result.text ?? "" },
        { type: "image", data: result.image.data, mimeType: result.image.mimeType },
      ],
    };
  }
  return { content: [{ type: "text", text: JSON.stringify(result) }] };
}

/**
 * The mirror's tools as an MCP server: what answers one JSON-RPC message.
 * How the message came, over HTTP or on standard input, is not its concern.
 *
 * A call that changes the mirror takes its turn in the queue, so that it
 * and a request of a person do not change the layout or the display's rules
 * under one another. A call that only reads does not wait.
 *
 * @param {Object} parts
 * @param {import("./tools.js").Tool[]} parts.tools Every tool of the assistant; the ones for the network are picked here.
 * @param {import("./mirror.js").Mirror} parts.mirror
 * @param {ReturnType<import("./queue.js").createQueue>} parts.queue
 * @param {import("./clock.js").Clock} parts.clock
 * @param {string} parts.version
 * @param {(event: string, fields?: object) => void} [parts.log]
 * @param {ReturnType<import("./activity.js").createActivity>} [parts.activity] Where a change is noted for the owner,
 *   and for the tending run, which leaves alone what was asked for.
 */
export function createMcp({ tools, mirror, queue, clock, version, log = () => {}, activity = null }) {
  const offered = [...OFFERED.map((name) => tools.find((tool) => tool.name === name)).filter(Boolean), ...ownTools({ mirror })];
  const byName = new Map(offered.map((tool) => [tool.name, tool]));
  const fromAssistant = new Set(OFFERED);
  const list = offered.map(listed);
  const logged = (event, fields) => log(event, { ...fields, via: "mcp" });
  /** The mirror's state as last read for a call: its clock and offset, which the tools reckon times by. */
  let known = { state: null, at: 0 };
  /** When a change by each tool was last noted. */
  const noted = new Map();
  let calls = 0;

  async function stateNow() {
    if (known.state && clock.now() - known.at < STATE_KEPT_MS) return known;
    try {
      // The status and the board are all that the tools take from it.
      known = { state: await fetchState(mirror, clock, { brief: true }), at: clock.now() };
    } catch {
      // A mirror that cannot be read cannot be changed either; the tool says so itself.
      known = { state: null, at: 0 };
    }
    return known;
  }

  async function run(tool, args) {
    const started = clock.now();
    const seen = fromAssistant.has(tool.name) && tool.changes ? await stateNow() : { state: null, at: 0 };
    const turn = newTurn("conversation", seen.state, seen.at);
    // Nothing a program sends was overheard.
    turn.certain = true;
    const result = await runTool(tool, args, turn, logged);
    // get_state read everything afresh, and that serves the next call as well.
    if (turn.state && turn.state !== seen.state) known = { state: turn.state, at: turn.stateAt };
    // A clock that was set reads differently from here on.
    if (tool.name === "set_clock") known = { state: null, at: 0 };
    if (activity && tool.changes && !tool.alone && !result.error && !(started - (noted.get(tool.name) ?? -Infinity) < NOTE_EVERY_MS)) {
      noted.set(tool.name, started);
      activity.add({ at: started, source: "mcp", acted: [tool.name], ms: clock.now() - started });
    }
    return result;
  }

  async function call(params) {
    const tool = typeof params.name === "string" ? byName.get(params.name) : undefined;
    if (!tool) throw new RpcError(-32602, `There is no tool called "${String(params.name).slice(0, 80)}". tools/list names the tools.`);
    calls += 1;
    const waits = tool.changes && !tool.alone;
    return callResult(await (waits ? queue.turn(() => run(tool, params.arguments)) : run(tool, params.arguments)));
  }

  function initialize(params) {
    const wanted = params.protocolVersion;
    logged("mcp.client", { name: String(params.clientInfo?.name ?? "").slice(0, 80), version: String(params.clientInfo?.version ?? "").slice(0, 40), protocol: wanted });
    return {
      protocolVersion: PROTOCOL_VERSIONS.includes(wanted) ? wanted : PROTOCOL_VERSIONS[0],
      capabilities: { tools: { listChanged: false } },
      serverInfo: { name: "mirror-companion", title: "MIRROR", version },
      instructions: INSTRUCTIONS,
    };
  }

  /** What a method answers; throws RpcError for one that cannot be answered. */
  async function dispatch(method, params) {
    if (method === "initialize") return initialize(params);
    if (method === "ping") return {};
    if (method === "tools/list") return { tools: list };
    if (method === "tools/call") return call(params);
    // Asked by clients that do not look at what a server says it has.
    if (method === "resources/list") return { resources: [] };
    if (method === "resources/templates/list") return { resourceTemplates: [] };
    if (method === "prompts/list") return { prompts: [] };
    if (method === "logging/setLevel" || method.startsWith("notifications/")) return {};
    throw new RpcError(-32601, `This server has tools only and does not know "${method.slice(0, 80)}".`);
  }

  /**
   * Answers one JSON-RPC message.
   *
   * @param {unknown} message
   * @returns {Promise<object|null>} the answer to send, or null for a message that gets none
   */
  async function answer(message) {
    if (message === null || typeof message !== "object" || Array.isArray(message) || message.jsonrpc !== "2.0") {
      return rpcFailure(null, -32600, "A message is a JSON-RPC 2.0 object.");
    }
    const asks = Object.hasOwn(message, "id");
    if (typeof message.method !== "string") {
      // An answer to something this server never asked is let pass.
      if (asks && (Object.hasOwn(message, "result") || Object.hasOwn(message, "error"))) return null;
      return rpcFailure(message.id, -32600, "A request needs a method.");
    }
    const params = message.params ?? {};
    if (typeof params !== "object" || Array.isArray(params)) {
      return asks ? rpcFailure(message.id, -32602, "params is an object.") : null;
    }
    try {
      const result = await dispatch(message.method, params);
      return asks ? { jsonrpc: "2.0", id: message.id, result } : null;
    } catch (error) {
      if (!asks) return null;
      if (error instanceof RpcError) return rpcFailure(message.id, error.code, error.message);
      logged("mcp.failed", { method: message.method, detail: error instanceof Error ? error.message : String(error) });
      return rpcFailure(message.id, -32603, "The companion could not handle the request.");
    }
  }

  return {
    /** The tools as tools/list gives them. */
    tools: list,

    /** How many tool calls came since the start. */
    calls: () => calls,

    answer,

    /**
     * Answers what one delivery held: a message, or several in a list, as older clients send them.
     *
     * @param {unknown} delivered
     * @returns {Promise<object|object[]|null>} what to send back, or null when nothing is
     */
    async receive(delivered) {
      if (!Array.isArray(delivered)) return answer(delivered);
      if (delivered.length === 0) return rpcFailure(null, -32600, "A list of messages needs a message.");
      const answers = (await Promise.all(delivered.map(answer))).filter(Boolean);
      return answers.length > 0 ? answers : null;
    },
  };
}

/** @typedef {ReturnType<typeof createMcp>} Mcp */

/**
 * Serves one client on a pair of streams, as a client does that starts the
 * server itself: a JSON-RPC message per line in, an answer per line out.
 *
 * @param {Mcp} mcp
 * @param {{ input?: NodeJS.ReadableStream, output?: NodeJS.WritableStream }} [streams]
 * @returns {Promise<void>} settles when the client has closed its end and every answer is out
 */
export function serveLines(mcp, { input = process.stdin, output = process.stdout } = {}) {
  return new Promise((resolve) => {
    const open = new Set();
    let ended = false;
    const write = (answer) => output.write(JSON.stringify(answer) + "\n");
    const done = () => ended && open.size === 0 && resolve();
    const lines = readline.createInterface({ input, crlfDelay: Infinity });
    lines.on("line", (line) => {
      if (!line.trim()) return;
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        write(rpcFailure(null, -32700, "That line is not JSON."));
        return;
      }
      // Not one after the other: a ping is answered while a call waits its turn.
      const work = mcp.receive(message).then((answer) => {
        if (answer) write(answer);
        open.delete(work);
        done();
      });
      open.add(work);
    });
    lines.on("close", () => {
      ended = true;
      done();
    });
  });
}
