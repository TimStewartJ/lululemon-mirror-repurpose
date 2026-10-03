import { BrainError } from "./brain.js";
import { describeError } from "./log.js";
import { conversationMessage, conversationSystem } from "./prompt.js";
import { asksSomething, oneLine, withoutRepeat } from "./reply.js";
import { fetchState } from "./state.js";
import { newTurn, toolsFor } from "./tools.js";

const NAME = /^\s*(?:(?:hey|hi|ok|okay)[\s,]+)?(?:mirror|mira|mirra)\b[\s,.!?;:-]*/i;
// What speech recognition writes when it hears no words: [BLANK_AUDIO], (music).
const ANNOTATION = /[[(][^\])]*[\])]/g;

/** Takes one leading "mirror", "hey mirror" and the like off a transcript. */
export function withoutName(transcript) {
  return transcript.replace(NAME, "").trim();
}

function letters(text) {
  return (text.match(/\p{L}/gu) ?? []).length;
}

/**
 * The answer to an utterance or a typed request, as section 3.1 of the
 * specification has it.
 *
 * @typedef {Object} Answer
 * @property {string} id
 * @property {string} heard
 * @property {string} reply
 * @property {boolean} ignored
 * @property {string} reason
 * @property {boolean} listen
 * @property {string[]} acted
 * @property {{ stt: number, agent: number, total: number }} ms
 * @property {string} [error]
 */

/**
 * Handles what people say or type to the mirror: transcript, agent run, answer.
 *
 * @param {Object} parts
 * @param {import("./brain.js").Brain} parts.brain
 * @param {import("./stt.js").Stt} parts.stt
 * @param {import("./mirror.js").Mirror} parts.mirror
 * @param {import("./tools.js").Tool[]} parts.tools
 * @param {ReturnType<import("./memory.js").createMemory>} parts.memory
 * @param {ReturnType<import("./activity.js").createActivity>} parts.activity
 * @param {ReturnType<import("./recordings.js").createRecordings>} parts.recordings
 * @param {ReturnType<import("./queue.js").createQueue>} parts.queue
 * @param {(event: string, fields?: object) => void} parts.log
 * @param {import("./clock.js").Clock} parts.clock
 * @param {number} [parts.followUpMs] How long a conversation stays open after its last turn.
 * @param {number} [parts.agentTimeoutMs] The agent is cut off after this long.
 * @param {number} [parts.answerWithinMs] Whatever happens, an answer goes out by then.
 */
export function createAssistant({
  brain,
  stt,
  mirror,
  tools,
  memory,
  activity,
  recordings,
  queue,
  log,
  clock,
  followUpMs = 120_000,
  agentTimeoutMs = 30_000,
  answerWithinMs = 38_000,
}) {
  const conversationTools = toolsFor("conversation", tools);
  /** When the open conversation had its last turn; null when none is open. */
  let lastTurnAt = null;
  let lastReply = "";
  let expiry = null;
  /** Counts agent runs for people, to tell when a state read on arrival has gone stale. */
  let turnsFinished = 0;

  const isFresh = () => lastTurnAt === null || clock.now() - lastTurnAt >= followUpMs;

  function readState() {
    // A state read while another run is under way, or before one finished,
    // may be out of date by the time it is used.
    const stale = queue.running;
    const epoch = turnsFinished;
    return fetchState(mirror, clock).then(
      (state) => ({ state, at: clock.now(), current: () => !stale && epoch === turnsFinished }),
      (error) => {
        log("state.unread", { detail: describeError(error) });
        return { state: null, at: clock.now(), current: () => !stale && epoch === turnsFinished };
      },
    );
  }

  /** Closes the conversation when its follow-up time is over. */
  function expire() {
    expiry = null;
    if (queue.running) {
      expiry = clock.setTimeout(expire, 5000);
      return;
    }
    if (lastTurnAt !== null && clock.now() - lastTurnAt < followUpMs) return;
    lastTurnAt = null;
    brain.endConversation().catch((error) => log("brain.session_not_closed", { detail: describeError(error) }));
  }

  function keepOpen() {
    lastTurnAt = clock.now();
    clock.clearTimeout(expiry);
    expiry = clock.setTimeout(expire, followUpMs);
  }

  /** Closes a session that was opened ahead for a request that then needed none. */
  function dropPrepared() {
    if (!queue.running && isFresh()) closeNow();
  }

  function closeNow() {
    lastTurnAt = null;
    clock.clearTimeout(expiry);
    expiry = null;
    brain.endConversation().catch((error) => log("brain.session_not_closed", { detail: describeError(error) }));
  }

  /**
   * Waits for the agent, but never longer than allowed: whatever the harness
   * does, the mirror gets its answer in time.
   */
  function within(run, ms) {
    return new Promise((resolve, reject) => {
      const timer = clock.setTimeout(() => reject(new BrainError("timeout", `No answer within ${ms} ms.`)), ms);
      run.then(resolve, reject).finally(() => clock.clearTimeout(timer));
    });
  }

  /** Completes an answer, records it, and returns it. */
  function finish({ id, source, started, ms, heard = "", reply = "", ignored = false, reason = "", acted = [], error }) {
    ms.total = clock.now() - started;
    /** @type {Answer} */
    const answer = { id, heard, reply, ignored, reason, listen: !ignored && asksSomething(reply), acted, ms };
    if (error) answer.error = error;
    activity.add({ at: started, source, heard, reply, acted, ignored, reason, ms: ms.total, error });
    log("exchange", { id, source, heard, reply, ignored, reason, acted, ms, error });
    return answer;
  }

  /** The agent's part of an exchange, when its turn in the queue comes. */
  function converse({ id, source, addressed, named, words, started, ms, stateAhead }) {
    return queue.turn(async () => {
      const common = { id, source, started, ms, heard: words };
      // What the mirror shows may have been changed by the request served
      // before this one, so the state read on arrival is then read again.
      let seen = await stateAhead;
      if (!seen.current()) seen = await readState();
      const budget = Math.min(agentTimeoutMs, started + answerWithinMs - clock.now());
      if (budget < 2000) {
        return finish({ ...common, reply: "I was busy with something else. Please say it again.", error: "No time was left after waiting." });
      }
      const fresh = isFresh();
      const turn = newTurn("conversation", seen.state, seen.at);
      const agentStarted = clock.now();
      let text = "";
      let failure = null;
      try {
        const asked = brain.run({
          session: "conversation",
          fresh,
          system: conversationSystem(memory.lines()),
          prompt: conversationMessage({
            words,
            source,
            addressed,
            named,
            question: asksSomething(lastReply) ? lastReply : "",
            snapshot: seen.state?.snapshot ?? null,
          }),
          tools: conversationTools,
          turn,
          timeoutMs: budget,
        });
        text = (await within(asked, budget + 1500)).text;
      } catch (error) {
        failure = error;
      }
      // A tool call that arrives after the answer has gone out must not act.
      turn.closed = true;
      turnsFinished += 1;
      ms.agent = clock.now() - agentStarted;
      const acted = turn.acted;

      if (failure) {
        closeNow();
        const kind = failure instanceof BrainError ? failure.kind : "failed";
        let reply = "Something went wrong on my side.";
        if (kind === "timeout") {
          reply = turn.changes > 0 ? "That took too long, but part of it is done." : "That took too long. Please try again.";
        } else if (kind === "not-ready") {
          reply = "I can't think right now. Please try again in a minute.";
        }
        return finish({ ...common, reply, acted, error: `${kind}: ${describeError(failure)}` });
      }
      if (turn.ignored !== null) {
        // Overheard talk does not open a conversation, and does not prolong one.
        if (fresh) closeNow();
        return finish({ ...common, ignored: true, reason: "not-addressed", acted });
      }
      const reply = oneLine(withoutRepeat(text)) || (turn.changes > 0 ? "Done." : "I have no answer to that.");
      lastReply = reply;
      keepOpen();
      return finish({ ...common, reply, acted });
    });
  }

  return {
    /**
     * Something was said to the mirror.
     *
     * @param {Object} request
     * @param {string} request.id
     * @param {Buffer} request.wav The recording as it arrived, for the recordings folder.
     * @param {Buffer} request.pcm Its samples.
     * @param {number} request.seconds
     * @param {boolean} request.silent
     * @param {"name"|"window"|"follow-up"} request.addressed
     * @returns {Promise<Answer>}
     */
    async utterance({ id, wav, pcm, seconds, silent, addressed }) {
      const started = clock.now();
      const ms = { stt: 0, agent: 0, total: 0 };
      const common = { id, source: "voice", started, ms };
      if (silent || seconds < 0.3) {
        recordings.save(id, wav, started);
        return finish({ ...common, ignored: true, reason: "nothing-heard" });
      }
      // Everything that can start before the words are known starts now:
      // the transcription, the read of the mirror's state, and a session.
      const hearing = stt.transcribe(pcm);
      const stateAhead = readState();
      if (!queue.running) brain.prepare({ fresh: isFresh(), system: conversationSystem(memory.lines()), tools: conversationTools });
      recordings.save(id, wav, started);

      let transcript;
      try {
        transcript = (await hearing).text;
      } catch (error) {
        ms.stt = clock.now() - started;
        dropPrepared();
        return finish({ ...common, reply: "I could not hear that properly. Please say it again.", error: describeError(error) });
      }
      ms.stt = clock.now() - started;
      const plain = transcript.replace(ANNOTATION, " ").replace(/\s+/g, " ").trim();
      const words = addressed === "name" ? withoutName(plain) : plain;
      if (letters(words) < 2) {
        dropPrepared();
        return finish({ ...common, heard: words, ignored: true, reason: "nothing-heard" });
      }
      // Shown at once, without waiting for it, so the person sees what was understood.
      mirror
        .call("POST", "/api/v1/assistant/say", { body: { text: oneLine(words), kind: "heard" } })
        .catch((error) => log("heard.not_shown", { detail: describeError(error) }));
      return converse({ id, source: "voice", addressed, named: words !== plain, words, started, ms, stateAhead });
    },

    /**
     * Something was typed to the mirror.
     *
     * @param {{ id: string, text: string, source: "controls"|"test" }} request
     * @returns {Promise<Answer>}
     */
    ask({ id, text, source }) {
      const started = clock.now();
      const ms = { stt: 0, agent: 0, total: 0 };
      return converse({ id, source, words: text, started, ms, stateAhead: readState() });
    },

    /** Ends an open conversation; used when the companion shuts down. */
    close() {
      clock.clearTimeout(expiry);
      expiry = null;
      lastTurnAt = null;
    },
  };
}
