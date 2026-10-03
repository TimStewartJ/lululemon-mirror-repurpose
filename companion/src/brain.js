import { systemClock } from "./clock.js";
import { describeError } from "./log.js";
import { runTool } from "./tools.js";

/**
 * The agent harness as the rest of the companion sees it. The scripted brain
 * of the tests and the Copilot one below both fit it; another harness would
 * be a third.
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
const PING_EVERY_MS = 60_000;

/**
 * @param {Object} options
 * @param {"copilot"} [options.harness]
 * @param {string} options.model
 * @param {string} [options.reasoningEffort] Passed to the model when given.
 * @param {string} options.workingDirectory Where the Copilot runtime runs; it is given no files to work on.
 * @param {(event: string, fields?: object) => void} [options.log]
 * @param {import("./clock.js").Clock} [options.clock]
 * @param {() => Promise<any>} [options.loadSdk] Tests supply a stand-in for the SDK.
 * @returns {Brain}
 */
export function createBrain({ harness = "copilot", ...options }) {
  if (harness !== "copilot") throw new Error(`There is no agent harness called "${harness}". The only one is "copilot".`);
  return createCopilotBrain(options);
}

/** The brain on GitHub Copilot's SDK, using the Copilot CLI login of the account it runs under. */
function createCopilotBrain({
  model,
  reasoningEffort,
  workingDirectory,
  log = () => {},
  clock = systemClock,
  loadSdk = () => import("@github/copilot-sdk"),
}) {
  let sdk = null;
  let client = null;
  let ready = false;
  let stopped = true;
  let detail = "Not started.";
  let retryMs = FIRST_RETRY_MS;
  let timer = null;
  /** @type {{ session: any, holder: { turn: any }, used: boolean } | null} */
  let conversation = null;
  /** @type {Promise<unknown> | null} */
  let opening = null;

  async function connect() {
    timer = null;
    if (stopped) return;
    try {
      sdk ??= await loadSdk();
      const next = new sdk.CopilotClient({ workingDirectory, logLevel: "error" });
      await next.start();
      const models = await next.listModels();
      if (!models.some((offered) => offered.id === model)) {
        await next.stop().catch(() => {});
        throw new Error(
          `the model ${model} is not offered to this Copilot login. Set "model" in the config to one of: ` +
            models.map((offered) => offered.id).join(", "),
        );
      }
      client = next;
      // The first session of a client takes over a second to open; later ones
      // take a few hundredths. One is opened and thrown away now so that the
      // first person does not wait for it.
      await close(await open("You are a test. Answer with the word ready.", [])).catch(() => {});
      ready = true;
      detail = "";
      retryMs = FIRST_RETRY_MS;
      log("brain.ready", { model });
      timer = clock.setTimeout(ping, PING_EVERY_MS);
    } catch (error) {
      ready = false;
      client = null;
      detail =
        `GitHub Copilot is not usable: ${describeError(error)}. ` +
        `Check the login with the Copilot CLI as this user. Trying again in ${Math.round(retryMs / 1000)} s.`;
      log("brain.unavailable", { detail: describeError(error), retryMs });
      timer = clock.setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 2, LONGEST_RETRY_MS);
    }
  }

  /** Notices a Copilot runtime that has died while nobody was asking. */
  async function ping() {
    timer = null;
    if (stopped || !client) return;
    try {
      await client.ping();
      timer = clock.setTimeout(ping, PING_EVERY_MS);
    } catch (error) {
      log("brain.lost", { detail: describeError(error) });
      await reconnect();
    }
  }

  async function reconnect() {
    clock.clearTimeout(timer);
    const old = client;
    ready = false;
    client = null;
    conversation = null;
    detail = "GitHub Copilot is being started again.";
    await old?.forceStop().catch(() => {});
    await connect();
  }

  function open(system, tools) {
    const holder = { turn: null };
    return client
      .createSession({
        model,
        ...(reasoningEffort ? { reasoningEffort } : {}),
        streaming: true,
        enableSessionStore: false,
        // Only the tools given here: no shell, no files, no web.
        availableTools: ["custom:*"],
        // Measured: without these two a first turn starts about 0.8 s later.
        disabledMcpServers: ["github-mcp-server"],
        infiniteSessions: { enabled: false },
        onPermissionRequest: sdk.approveAll,
        systemMessage: { mode: "replace", content: system },
        tools: tools.map((tool) =>
          sdk.defineTool(tool.name, {
            description: tool.description,
            parameters: tool.schema,
            skipPermission: true,
            isTerminal: tool.endsTurn === true,
            handler: (args) => call(tool, args, holder),
          }),
        ),
      })
      .then((session) => {
        // One line per call to the model, to see where the time of a turn goes.
        session.on?.("assistant.usage", (event) => {
          const usage = event.data ?? {};
          log("brain.model_call", {
            ms: Math.round(usage.duration ?? 0),
            firstTokenMs: Math.round(usage.timeToFirstTokenMs ?? 0),
            inputTokens: usage.inputTokens,
            outputTokens: usage.outputTokens,
            reasoningTokens: usage.reasoningTokens,
            cachedTokens: usage.cacheReadTokens,
          });
        });
        return { session, holder, used: false };
      });
  }

  async function call(tool, args, holder) {
    if (!holder.turn) return { error: "This request is over. Do nothing more." };
    const result = await runTool(tool, args, holder.turn, log);
    if (result.image) {
      return {
        textResultForLlm: result.text,
        binaryResultsForLlm: [{ type: "image", data: result.image.data, mimeType: result.image.mimeType }],
        resultType: "success",
      };
    }
    // A tool that ends the turn ends it only when it did what it was asked.
    // The runtime goes by the result's type, so a refusal is marked as a
    // failure: the model then reads why and can call again or answer.
    if (result.error && tool.endsTurn) {
      return { textResultForLlm: JSON.stringify(result), resultType: "failure" };
    }
    return result;
  }

  /** Disconnects a session and deletes it, so the owner's Copilot session list stays clean. */
  async function close(live) {
    if (!live) return;
    live.holder.turn = null;
    const id = live.session.sessionId;
    try {
      await live.session.disconnect();
      await client?.deleteSession(id);
    } catch (error) {
      log("brain.session_not_closed", { detail: describeError(error) });
    }
  }

  async function conversationSession(fresh, system, tools) {
    if (opening) await opening.catch(() => {});
    if (conversation && (!fresh || !conversation.used)) return conversation;
    const old = conversation;
    conversation = null;
    void close(old);
    conversation = await open(system, tools);
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
      live.session.sendAndWait({ prompt }, timeoutMs + 10_000).then(
        (message) => finish(null, String(message?.data?.content ?? "")),
        (error) => finish(error),
      );
      function finish(error, text) {
        if (settled) return;
        settled = true;
        clock.clearTimeout(deadline);
        signal?.removeEventListener("abort", aborted);
        if (!error) return resolve(text);
        // Stops the model's work; without this it would go on calling tools.
        if (error instanceof BrainError) live.session.abort().catch(() => {});
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
      if (opening) await opening.catch(() => {});
      await close(conversation);
      conversation = null;
      const old = client;
      client = null;
      if (old) {
        const stopping = old.stop().catch(() => {});
        const slow = new Promise((resolve) => setTimeout(resolve, 5000, "slow").unref());
        if ((await Promise.race([stopping, slow])) === "slow") await old.forceStop().catch(() => {});
      }
    },

    health: () => ({ ready, detail }),

    prepare({ fresh, system, tools }) {
      if (!ready || opening || (conversation && (!fresh || !conversation.used))) return;
      const old = conversation;
      conversation = null;
      void close(old);
      opening = open(system, tools)
        .then((live) => {
          conversation = live;
        })
        .finally(() => {
          opening = null;
        });
      opening.catch((error) => log("brain.prepare_failed", { detail: describeError(error) }));
    },

    async run({ session, fresh = false, system, prompt, tools, turn, timeoutMs, signal }) {
      if (!ready) throw new BrainError("not-ready", detail);
      const deadline = clock.now() + timeoutMs;
      for (let attempt = 1; ; attempt++) {
        let live = null;
        try {
          live =
            session === "conversation"
              ? await conversationSession(fresh || attempt > 1, system, tools)
              : await open(system, tools);
          live.holder.turn = turn;
          const text = await exchange(live, prompt, deadline - clock.now(), signal);
          if (session !== "conversation") void close(live);
          return { text };
        } catch (error) {
          // A session that failed or was cut off is not trusted with another turn.
          if (live === conversation) conversation = null;
          void close(live);
          if (error instanceof BrainError) throw error;
          const failure = describeError(error);
          log("brain.turn_failed", { attempt, detail: failure, acted: turn.acted.length });
          // One more try with a new session, but only if nothing has been
          // done yet: a tool that already acted must not act twice.
          const again = attempt === 1 && turn.acted.length === 0 && deadline - clock.now() > 3000 && !signal?.aborted;
          if (!again) throw new BrainError("failed", failure);
          try {
            await client.ping();
          } catch {
            await reconnect();
            if (!ready) throw new BrainError("not-ready", detail);
          }
        }
      }
    },

    async endConversation() {
      if (opening) await opening.catch(() => {});
      const old = conversation;
      conversation = null;
      await close(old);
    },
  };
}
