import { BrainError } from "./brain.js";
import { briefingHeadline, buildBriefing, createBriefingMemory } from "./briefing.js";
import { describeError } from "./log.js";
import { conversationMessage, conversationSystem } from "./prompt.js";
import { asksSomething, fitRows, headline, oneLine, withoutRepeat } from "./reply.js";
import { fetchState } from "./state.js";
import { newTurn, toolsFor } from "./tools.js";

const NAME = /^\s*(?:(?:hey|hi|ok|okay)[\s,]+)?(?:mirror|mira|mirra)\b[\s,.!?;:-]*/i;
// What speech recognition writes when it hears no words: [BLANK_AUDIO], (music).
const ANNOTATION = /[[(][^\])]*[\])]/g;

const SECOND_LOOK =
  "Weigh those words once more before you let them pass. " +
  "They are for you if they ask for something you can do or tell (the time, the weather, the board, a list, a reminder, " +
  "the background, the brightness, the display), or greet you or thank you. Then answer them or act on them now. " +
  "They are not for you if someone is telling or asking another person something, or speaking about a mirror " +
  "and not to one. Then call ignore again.";

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
 * @property {string} reply The line to show, or the headline of a card.
 * @property {{ label: string, text: string }[]} details The rows of a card; empty when the answer is one line.
 * @property {number} [seconds] How long the glass should show it, when that is not left to the mirror.
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
 * @param {ReturnType<import("./briefing.js").createBriefingMemory>} [parts.briefings] The last briefing shown, shared
 *   with the part that shows one by itself.
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
  briefings = createBriefingMemory(clock),
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
  function finish({ id, source, started, ms, heard = "", reply = "", details = [], seconds, ignored = false, reason = "", acted = [], error }) {
    ms.total = clock.now() - started;
    /** @type {Answer} */
    const answer = { id, heard, reply, details, ignored, reason, listen: !ignored && asksSomething(reply), acted, ms };
    if (seconds !== undefined) answer.seconds = seconds;
    if (error) answer.error = error;
    activity.add({ at: started, source, heard, reply, details, acted, ignored, reason, ms: ms.total, error });
    log("exchange", { id, source, heard, reply, details: details.length > 0 ? details : undefined, ignored, reason, acted, ms, error });
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
      // A briefing the glass showed a moment ago is told to the model, so
      // that "dismiss those" has something to refer to.
      const shown = briefings.recall();
      const turn = newTurn("conversation", seen.state, seen.at);
      // Typed words cannot have been overheard, and a sentence that both the
      // mirror and the transcript have beginning with its name was said to it.
      turn.certain = source !== "voice" || named === true;
      const agentStarted = clock.now();
      let text = "";
      let failure = null;
      const run = (prompt, fresh, timeoutMs) =>
        within(
          brain.run({ session: "conversation", fresh, system: conversationSystem(memory.lines()), prompt, tools: conversationTools, turn, timeoutMs }),
          timeoutMs + 1500,
        );
      try {
        text = (
          await run(
            conversationMessage({
              words,
              source,
              addressed,
              named,
              question: asksSomething(lastReply) ? lastReply : "",
              briefing: shown,
              snapshot: seen.state?.snapshot ?? null,
            }),
            fresh,
            budget,
          )
        ).text;
        // A model that passed such words over all the same, and so said
        // nothing, is asked once more.
        const left = started + answerWithinMs - clock.now() - 1500;
        if (turn.certain && !oneLine(text) && !turn.card && turn.changes === 0 && left >= 3000) {
          log("request.passed_over", { id });
          text = (
            await run(
              "Those words were addressed to you and are meant for you. Answer them now in one line, or act on them. Do not call ignore.",
              false,
              Math.min(agentTimeoutMs, left),
            )
          ).text;
        }
        // Words that may or may not have been for the mirror, and were
        // passed over, get a second look. Asked once, the model drops about
        // one request in four that was said to it, now this one and now
        // that; talk between people it drops both times.
        const leftToLook = started + answerWithinMs - clock.now() - 1500;
        if (!turn.certain && turn.ignored !== null && leftToLook >= 3000) {
          turn.ignored = null;
          log("request.second_look", { id });
          text = (await run(SECOND_LOOK, false, Math.min(agentTimeoutMs, leftToLook))).text;
        }
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
      if (turn.briefing) briefings.note(turn.briefing.briefing, turn.briefing.state);
      // Once the items of a briefing were dealt with, what it said of them is out of date.
      else if (shown && acted.some((name) => name === "board_update" || name === "board_remove")) briefings.forget();
      if (turn.card) {
        // The card a tool made is the answer; whatever the model wrote beside it is not shown.
        const details = fitRows(turn.card.details);
        lastReply = headline(turn.card.reply, details);
        keepOpen();
        return finish({ ...common, reply: lastReply, details, seconds: turn.card.seconds, acted });
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

    /**
     * A greeting the mirror recognised by itself. It is answered with the
     * briefing, built from the mirror's state by code: no recording, no
     * model, and no place in the queue, since it only reads. An open
     * conversation is left as it is.
     *
     * @param {{ id: string, text: string, shortcut: "good-morning"|"good-afternoon"|"good-evening"|"good-night"|"home" }} request
     * @returns {Promise<Answer>}
     */
    async shortcut({ id, text, shortcut }) {
      const started = clock.now();
      const common = { id, source: "shortcut", started, ms: { stt: 0, agent: 0, total: 0 }, heard: text };
      let state;
      try {
        state = await fetchState(mirror, clock, { brief: true });
      } catch (error) {
        log("state.unread", { detail: describeError(error) });
        // The greeting alone is still an answer to a greeting.
        return finish({ ...common, reply: briefingHeadline(shortcut), error: describeError(error) });
      }
      const briefing = buildBriefing(state, shortcut);
      briefings.note(briefing, state);
      return finish({ ...common, reply: briefing.reply, details: briefing.details, seconds: briefing.seconds });
    },

    /** Ends an open conversation; used when the companion shuts down. */
    close() {
      clock.clearTimeout(expiry);
      expiry = null;
      lastTurnAt = null;
    },
  };
}
