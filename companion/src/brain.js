import { randomUUID } from "node:crypto";
import { systemClock } from "./clock.js";
import { describeError } from "./log.js";
import { runTool } from "./tools.js";

/**
 * The agent harness as the rest of the companion sees it. The scripted brain
 * of the tests and the Pi one below both fit it; another harness would be a
 * third.
 *
 * @typedef {Object} BrainRun
 * @property {"conversation"|"proactive"} session A conversation keeps its session between turns, so a
 *   follow-up has its context; a proactive run gets a session of its own that is closed afterwards.
 * @property {boolean} [fresh] Start a new conversation even if one is open.
 * @property {string} system The standing instructions; used when a session is opened.
 * @property {string} prompt What is said in this turn.
 * @property {import("./tools.js").Tool[]} tools
 * @property {import("./tools.js").Turn} turn
 * @property {number} timeoutMs The turn is cut off after this long.
 * @property {AbortSignal} [signal] Cuts the turn off sooner.
 *
 * @typedef {Object} Brain
 * @property {() => Promise<void>} start
 * @property {() => Promise<void>} stop
 * @property {() => { ready: boolean, detail: string }} health
 * @property {(run: BrainRun) => Promise<{ text: string }>} run Rejects with a {@link BrainError}.
 * @property {(run: Pick<BrainRun, "fresh"|"system"|"tools">) => void} prepare
 *   A hint that a conversation turn is coming, so a session can be opened meanwhile.
 * @property {() => Promise<void>} endConversation Closes the conversation's session.
 */

/** Why a turn did not produce an answer. */
export class BrainError extends Error {
  /**
   * @param {"timeout"|"aborted"|"failed"|"not-ready"} kind
   * @param {string} message
   */
  constructor(kind, message) {
    super(message);
    this.name = "BrainError";
    this.kind = kind;
  }
}

const FIRST_RETRY_MS = 5000;
const LONGEST_RETRY_MS = 5 * 60_000;
const CHECK_EVERY_MS = 60_000;
const FIRST_ANSWER_WITHIN_MS = 20_000;
const MODELS_NAMED = 40;

/**
 * @param {Object} options
 * @param {"pi"} [options.harness]
 * @param {string} options.provider Whose model: one of Pi's providers, "copilot-cli", or the name of an endpoint.
 * @param {string} options.model
 * @param {string} [options.reasoningEffort] How hard the model thinks; "default", "none" or nothing leaves thinking off.
 * @param {import("./config.js").Config["endpoint"]} [options.endpoint]
 * @param {string} [options.authFile] Where the "login" command keeps its sign-ins.
 * @param {string} [options.userAgent]
 * @param {(event: string, fields?: object) => void} [options.log]
 * @param {import("./clock.js").Clock} [options.clock]
 * @param {(options: object) => { models: any, providerId: string, signIn: string } | Promise<any>} [options.openModels]
 *   Tests supply providers of their own.
 * @returns {Brain}
 */
export function createBrain({ harness = "pi", ...options }) {
  if (harness !== "pi") throw new Error(`There is no agent harness called "${harness}". The only one is "pi".`);
  return createPiBrain(options);
}

/** Pi is loaded when the brain starts: it takes half a second, which the other commands need not spend. */
async function loadPi() {
  const [core, ai] = await Promise.all([import("@earendil-works/pi-agent-core"), import("@earendil-works/pi-ai")]);
  return { Agent: core.Agent, clampThinkingLevel: ai.clampThinkingLevel };
}

/** What went wrong and what caused it: Pi wraps what a provider said in an error of its own. */
function reason(error) {
  const parts = [];
  for (let at = error, depth = 0; at && depth < 4; at = at.cause, depth++) {
    const text = describeError(at).trim().replace(/[.:]$/, "");
    if (text && !parts.some((part) => part.includes(text))) parts.push(text);
  }
  return parts.join(": ");
}

/** The arguments of a call that was refused, short enough for a log line. */
function brief(args) {
  try {
    return JSON.stringify(args ?? {}).slice(0, 300);
  } catch {
    return "(not printable)";
  }
}

/**
 * Two things in a request to the Responses API are not as the mirror needs
 * them, and are put right before it is sent.
 *
 * A tool whose declaration does not say otherwise is taken as a strict one,
 * and the model then gives every argument a value, those that are meant to
 * be left out too ("text": "", "size": "auto"), which the mirror's tools
 * refuse. Pi leaves the word out; here it is put in.
 *
 * Pi asks for a summary of the model's thinking with every answer. Nobody
 * reads it here, and writing it costs time: measured with gpt-6-luna, a call
 * without it comes back 0.15 to 0.4 s sooner.
 */
function asTheMirrorNeedsIt(payload) {
  if (!payload || typeof payload !== "object" || !("input" in payload)) return undefined;
  for (const tool of Array.isArray(payload.tools) ? payload.tools : []) {
    if (tool?.type === "function" && typeof tool.name === "string" && tool.strict === undefined) tool.strict = false;
  }
  if (payload.reasoning && typeof payload.reasoning === "object") delete payload.reasoning.summary;
  return payload;
}

/**
 * The words of an answer. A model may write a remark on what it is about to
 * do before its answer; where the provider tells the two apart, only the
 * answer is taken.
 */
function wordsOf(message) {
  const blocks = (message?.content ?? []).filter((block) => block.type === "text");
  const final = blocks.filter((block) => {
    try {
      return JSON.parse(block.textSignature ?? "null")?.phase === "final_answer";
    } catch {
      return false;
    }
  });
  return (final.length > 0 ? final : blocks).map((block) => block.text).join("");
}

/**
 * The brain on Pi (@earendil-works/pi-agent-core): Pi's agent loop in this
 * process, with the mirror's tools and no others, and whichever provider and
 * model the config names.
 */
function createPiBrain({
  provider,
  model: modelId,
  reasoningEffort,
  endpoint = null,
  authFile,
  userAgent,
  log = () => {},
  clock = systemClock,
  openModels = async (options) => (await import("./providers.js")).openModels(options),
}) {
  let pi = null;
  /** The providers, once the model was found to answer. @type {{ models: any, providerId: string, signIn: string } | null} */
  let source = null;
  let model = null;
  let level = "off";
  let ready = false;
  let stopped = true;
  let detail = "Not started.";
  let retryMs = FIRST_RETRY_MS;
  let timer = null;
  let reconnecting = null;
  /** @type {{ agent: any, holder: { turn: any }, used: boolean } | null} */
  let conversation = null;

  async function connect() {
    timer = null;
    if (stopped) return;
    let opened = null;
    try {
      pi ??= await loadPi();
      opened = await openModels({ provider, model: modelId, endpoint, authFile, userAgent, log });
      const { models, providerId } = opened;
      const auth = await models.getAuth(providerId);
      if (!auth) throw new Error(`nobody is signed in to ${provider}. ${opened.signIn}`.trim());
      // A provider that lists its models itself is asked for them when the model is not among those known.
      if (!models.getModel(providerId, modelId)) await models.refresh({ providers: [providerId] });
      const offered = await models.getAvailable(providerId);
      const found = offered.find((candidate) => candidate.id === modelId);
      if (!found) {
        const ids = offered.map((candidate) => candidate.id);
        const more = ids.length > MODELS_NAMED ? `, and ${ids.length - MODELS_NAMED} more ("node src/cli.js models" lists them)` : "";
        throw new Error(
          `the model ${modelId} is not ${models.getModel(providerId, modelId) ? "offered to this sign-in" : `one that ${provider} has`}. ` +
            (ids.length > 0 ? `Set "model" in the config to one of: ${ids.slice(0, MODELS_NAMED).join(", ")}${more}` : "It is offered no model at all"),
        );
      }
      const thinking = pi.clampThinkingLevel(found, !reasoningEffort || reasoningEffort === "default" || reasoningEffort === "none" ? "off" : reasoningEffort);
      // One question is asked and its answer thrown away: a key that is not
      // accepted shows now and not when the first person asks, and that
      // person does not wait for the connection and the client library.
      const first = await models.completeSimple(
        found,
        { systemPrompt: "You are a test. Answer with the word ready.", messages: [{ role: "user", content: "Are you ready?", timestamp: Date.now() }] },
        { ...(thinking === "off" ? {} : { reasoning: thinking }), signal: AbortSignal.timeout(FIRST_ANSWER_WITHIN_MS) },
      );
      if (first.stopReason === "aborted") throw new Error(`it did not answer a first question within ${FIRST_ANSWER_WITHIN_MS / 1000} s`);
      if (first.stopReason === "error") throw new Error(first.errorMessage || "it gave no answer to a first question");
      if (stopped) return void opened.close?.();
      source?.close?.();
      source = opened;
      model = found;
      level = thinking;
      ready = true;
      detail = "";
      retryMs = FIRST_RETRY_MS;
      log("brain.ready", { provider, model: modelId, signIn: auth.source, reasoning: level });
      timer = clock.setTimeout(check, CHECK_EVERY_MS);
    } catch (error) {
      opened?.close?.();
      if (stopped) return;
      ready = false;
      source?.close?.();
      source = null;
      model = null;
      detail = `The model ${modelId} of ${provider} is not usable: ${reason(error)}. Trying again in ${Math.round(retryMs / 1000)} s.`;
      log("brain.unavailable", { detail: reason(error), retryMs });
      timer = clock.setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 2, LONGEST_RETRY_MS);
    }
  }

  /**
   * Every minute: a sign-in that is about to run out is renewed here and not
   * in someone's turn, and one that has gone is noticed while nobody asks.
   */
  async function check() {
    timer = null;
    if (stopped || !source) return;
    try {
      if (!(await source.models.getAuth(source.providerId))) throw new Error("the sign-in is gone");
      if (!stopped) timer = clock.setTimeout(check, CHECK_EVERY_MS);
    } catch (error) {
      if (stopped) return;
      log("brain.lost", { detail: reason(error) });
      await reconnect();
    }
  }

  function reconnect() {
    reconnecting ??= (async () => {
      clock.clearTimeout(timer);
      ready = false;
      const old = conversation;
      conversation = null;
      close(old);
      detail = "The model is being reached again.";
      await connect();
    })().finally(() => {
      reconnecting = null;
    });
    return reconnecting;
  }

  /** A tool of the mirror in the form Pi runs: its schema as JSON Schema, its result as text or a picture. */
  function adapt(tool, holder) {
    return {
      name: tool.name,
      label: tool.name,
      description: tool.description,
      parameters: tool.schema.toJSONSchema(),
      execute: async (callId, args) => {
        holder.ran.add(callId);
        const refusal = { error: "This request is over. Do nothing more." };
        const result = holder.turn ? await runTool(tool, args, holder.turn, log) : refusal;
        if (result.image) {
          return {
            content: [
              { type: "text", text: result.text },
              { type: "image", data: result.image.data, mimeType: result.image.mimeType },
            ],
            details: {},
          };
        }
        const content = [{ type: "text", text: JSON.stringify(result) }];
        // A refusal goes back as a failure, so the model reads why and can call again or answer.
        if (result.error) return { content, details: {}, isError: true };
        // A tool that ends the turn ends it only when it did what it was asked.
        return { content, details: {}, terminate: tool.endsTurn === true };
      },
    };
  }

  function open(system, tools) {
    const { models } = source;
    const holder = { turn: null, ran: new Set(), asked: new Map() };
    const agent = new pi.Agent({
      // Only the tools given here: Pi's agent core has none of its own.
      initialState: { systemPrompt: system, model, thinkingLevel: level, tools: tools.map((tool) => adapt(tool, holder)) },
      streamFn: (which, context, options) => models.streamSimple(which, context, options),
      onPayload: asTheMirrorNeedsIt,
      // What the provider keys its cache of the instructions on.
      sessionId: randomUUID(),
      // runTool takes the calls of a turn one at a time as well; here Pi is told so.
      toolExecution: "sequential",
    });
    let asked = 0;
    let first = 0;
    agent.subscribe((event) => {
      if (event.type === "turn_start") {
        asked = Date.now();
        first = 0;
      } else if (event.type === "message_update") {
        first ||= Date.now();
      } else if (event.type === "message_end" && event.message.role === "assistant") {
        // One line per call to the model, to see where the time of a turn goes.
        const usage = event.message.usage ?? {};
        const failed = event.message.stopReason === "error" ? { failed: String(event.message.errorMessage ?? "").slice(0, 200) } : {};
        log("brain.model_call", {
          ms: Date.now() - asked,
          firstTokenMs: (first || Date.now()) - asked,
          // All that was sent, whether the provider had it at hand from before or not.
          inputTokens: (usage.input ?? 0) + (usage.cacheRead ?? 0) + (usage.cacheWrite ?? 0),
          outputTokens: usage.output,
          reasoningTokens: usage.reasoning,
          cachedTokens: usage.cacheRead,
          ...failed,
        });
      } else if (event.type === "tool_execution_start") {
        holder.asked.set(event.toolCallId, event.args);
      } else if (event.type === "tool_execution_end") {
        const args = holder.asked.get(event.toolCallId);
        holder.asked.delete(event.toolCallId);
        if (holder.ran.delete(event.toolCallId) || !event.isError || !holder.turn || holder.turn.closed) return;
        // Pi checks the arguments against the schema first and answered the
        // model itself. The call still counts as one the model made.
        holder.turn.acted.push(event.toolName);
        const refused = (event.result?.content ?? []).map((block) => block.text ?? "").join(" ").replace(/\s+/g, " ");
        log("tool", { tool: event.toolName, ok: false, ms: 0, refused: refused.slice(0, 200), args: brief(args) });
      }
    });
    return { agent, holder, used: false };
  }

  /** Ends a session: what the model still asks for does nothing, and its work is stopped. */
  function close(live) {
    if (!live) return;
    live.holder.turn = null;
    live.agent.abort();
  }

  function conversationSession(fresh, system, tools) {
    if (conversation && (!fresh || !conversation.used)) return conversation;
    close(conversation);
    conversation = open(system, tools);
    return conversation;
  }

  /** Sends the prompt and waits for the final message, the deadline or the signal, whichever is first. */
  function exchange(live, prompt, timeoutMs, signal) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const deadline = clock.setTimeout(() => finish(new BrainError("timeout", `No answer within ${timeoutMs} ms.`)), timeoutMs);
      const aborted = () => finish(new BrainError("aborted", "The run was stopped for a person's request."));
      if (signal?.aborted) return aborted();
      signal?.addEventListener("abort", aborted, { once: true });
      live.used = true;
      const from = live.agent.state.messages.length;
      live.agent.prompt(prompt).then(() => {
        const said = live.agent.state.messages.slice(from).findLast((message) => message.role === "assistant");
        if (said?.stopReason === "error" || said?.stopReason === "aborted") {
          return finish(new Error(said.errorMessage || `The model's answer ended as ${said.stopReason}.`));
        }
        finish(null, wordsOf(said));
      }, finish);
      function finish(error, text) {
        if (settled) return;
        settled = true;
        clock.clearTimeout(deadline);
        signal?.removeEventListener("abort", aborted);
        if (!error) return resolve(text);
        // Stops the model's work; without this it would go on calling tools.
        if (error instanceof BrainError) live.agent.abort();
        reject(error);
      }
    });
  }

  return {
    async start() {
      if (!stopped) return;
      stopped = false;
      detail = "Starting.";
      await connect();
    },

    async stop() {
      stopped = true;
      ready = false;
      detail = "Stopped.";
      clock.clearTimeout(timer);
      close(conversation);
      conversation = null;
      source?.close?.();
      source = null;
    },

    health: () => ({ ready, detail }),

    // With Pi a session is made in no time, so there is nothing to wait for; it is made all the same, so that the
    // turn finds it.
    prepare({ fresh, system, tools }) {
      if (ready) conversationSession(fresh, system, tools);
    },

    async run({ session, fresh = false, system, prompt, tools, turn, timeoutMs, signal }) {
      if (!ready) throw new BrainError("not-ready", detail);
      const deadline = clock.now() + timeoutMs;
      for (let attempt = 1; ; attempt++) {
        let live = null;
        try {
          live = session === "conversation" ? conversationSession(fresh || attempt > 1, system, tools) : open(system, tools);
          live.holder.turn = turn;
          const text = await exchange(live, prompt, deadline - clock.now(), signal);
          if (session !== "conversation") close(live);
          return { text };
        } catch (error) {
          // A session that failed or was cut off is not trusted with another turn.
          if (live === conversation) conversation = null;
          close(live);
          if (error instanceof BrainError) throw error;
          const failure = reason(error);
          log("brain.turn_failed", { attempt, detail: failure, acted: turn.acted.length });
          // One more try with a new session, but only if nothing has been
          // done yet: a tool that already acted must not act twice.
          const again = attempt === 1 && turn.acted.length === 0 && deadline - clock.now() > 3000 && !signal?.aborted;
          if (!again) throw new BrainError("failed", failure);
          // A sign-in that has gone, or cannot be renewed, is looked into before the second try.
          const signedIn = await source?.models.getAuth(source.providerId).catch(() => null);
          if (!signedIn) {
            await reconnect();
            if (!ready) throw new BrainError("not-ready", detail);
          }
        }
      }
    },

    async endConversation() {
      const old = conversation;
      conversation = null;
      close(old);
    },
  };
}
