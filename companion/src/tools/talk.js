import { z } from "zod";
import { oneLine } from "../reply.js";

/** @returns {import("../tools.js").Tool[]} */
export function talkTools({ mirror, memory, clock }) {
  return [
    {
      name: "say",
      description:
        "Shows one line on the glass now, for something worth telling while work goes on. Rarely needed: " +
        "your final message is shown by itself, so never use this for the answer.",
      schema: z.object({
        text: z.string().min(1).max(200),
        seconds: z.number().int().min(2).max(30).optional(),
      }),
      async handler({ text, seconds }, turn) {
        if (turn.deadline !== undefined && clock.now() > turn.deadline) {
          return { error: "Too late: the moment for this has passed. Say nothing." };
        }
        const body = { text: oneLine(text, 200), kind: turn.kind === "conversation" ? "reply" : "notice" };
        if (seconds) body.seconds = seconds;
        const answer = await mirror.call("POST", "/api/v1/assistant/say", { body });
        if (answer.shown) turn.said = body.text;
        return answer.shown ? { shown: true } : { shown: false, reason: answer.reason || "unknown" };
      },
    },
    {
      name: "remember",
      description:
        "Keeps one line about this household for later conversations: a preference, a name, a routine. " +
        "Use it when someone asks you to remember something, or states a lasting preference.",
      schema: z.object({ note: z.string().min(1).max(200) }),
      async handler({ note }) {
        return memory.remember(note);
      },
    },
    {
      name: "forget",
      description: "Removes every remembered line that contains the given words.",
      schema: z.object({ containing: z.string().min(3) }),
      async handler({ containing }) {
        return memory.forget(containing);
      },
    },
    {
      name: "ignore",
      description:
        "Call this, and nothing else, when the words were not meant for the mirror: talk between people, " +
        "a television, or words that make no request. It ends the turn silently.",
      schema: z.object({ reason: z.string().max(200).describe("A few words on why this was not for the mirror.") }),
      endsTurn: true,
      async handler({ reason }, turn) {
        turn.ignored = reason || "not addressed";
        return { ignored: true };
      },
    },
  ];
}
