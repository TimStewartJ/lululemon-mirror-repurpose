import { z } from "zod";
import { buildBriefing } from "../briefing.js";
import { CARD, fitRows, oneLine } from "../reply.js";
import { fetchState } from "../state.js";

const BRIEFINGS = {
  morning: "good-morning",
  afternoon: "good-afternoon",
  evening: "good-evening",
  night: "good-night",
  home: "home",
  "catch-up": "catch-up",
};
// Longer than anything a model writes, so that a text is measured whole and not cut first.
const UNCUT = 100_000;

/**
 * The tools that make the answer of a turn a card: a headline with rows
 * under it. Both end the turn, since the card is the answer.
 *
 * @returns {import("../tools.js").Tool[]}
 */
export function cardTools({ mirror, clock, log = () => {} }) {
  return [
    {
      name: "briefing",
      description:
        "Answers a greeting at a time of day, or a wish to be caught up, with the mirror's briefing: a card with the weather, " +
        "what is due, what was missed and what is still to do, built from the mirror's state as it is now. " +
        "Call this, and nothing else, when someone greets you at a time of day or asks how things stand: " +
        "\"morning, mirror\", \"good evening\", \"good night\", \"hey, I'm back\", \"I'm home\", \"what did I miss?\", " +
        "\"catch me up\", \"what's my day like?\", \"anything I should know?\". " +
        "kind is the greeting that was given (morning, afternoon, evening, night, home), whatever the clock says, " +
        "and catch-up when no time of day or homecoming was named. The card is your whole answer: it ends the turn.",
      schema: z.object({ kind: z.enum(["morning", "afternoon", "evening", "night", "home", "catch-up"]) }),
      endsTurn: true,
      async handler({ kind }, turn) {
        // Read afresh: an earlier tool of this turn may have changed the board.
        const state = await fetchState(mirror, clock, { brief: true });
        const briefing = buildBriefing(state, BRIEFINGS[kind]);
        turn.card = { reply: briefing.reply, details: briefing.details, seconds: briefing.seconds };
        turn.briefing = { briefing, state };
        return { shown: { headline: briefing.reply, rows: briefing.details } };
      },
    },
    {
      name: "present",
      description:
        "Shows your answer as a card: a headline with up to 5 rows under it, each a short label beside one line of text. " +
        "Use it when the answer is a list or has several parts that read better apart. " +
        `headline is the answer in a few words, at most ${CARD.headline} characters. ` +
        `A row's text is 1 to ${CARD.text} characters on one line. Its label is one short thing of at most ${CARD.label} characters: ` +
        "a time (\"7:00 AM\"), a day (\"Tomorrow\"), a word or two (\"To do\"), or empty. " +
        "A day and a time together do not fit a label: the label is the day and the text begins with the time, " +
        "as in \"Tomorrow\" beside \"7:00 AM, Take out the trash\". Plain text: no markdown, no emoji. " +
        `seconds (${CARD.leastSeconds} to ${CARD.mostSeconds}) is how long the card stays; leave it out unless the person asked. ` +
        "The card is your whole answer: it ends the turn, so do everything else first.",
      schema: z.object({
        headline: z.string(),
        rows: z.array(z.object({ label: z.string().optional(), text: z.string() })),
        seconds: z.number().optional(),
      }),
      endsTurn: true,
      async handler({ headline, rows, seconds }, turn) {
        const card = {
          reply: oneLine(headline, UNCUT),
          details: rows.map((row) => ({ label: oneLine(row.label ?? "", UNCUT), text: oneLine(row.text, UNCUT) })),
        };
        const problems = cardProblems(card, seconds);
        // Logged, so that it can be seen which limit the model keeps breaking.
        if (problems.length > 0) log("card.limits", { problems, refused: !turn.cardRefused || !card.reply });
        // A card that breaks the limits is handed back once, to be written
        // again. A second one that does is cut to fit: a card with a word
        // missing is better than no answer.
        if (problems.length > 0 && (!turn.cardRefused || !card.reply)) {
          turn.cardRefused = true;
          return { error: `Nothing was shown. ${problems.join(" ")} Call present once more with that put right.` };
        }
        turn.card = { reply: oneLine(card.reply, CARD.headline), details: fitRows(card.details) };
        if (Number.isFinite(seconds)) {
          turn.card.seconds = Math.max(CARD.leastSeconds, Math.min(CARD.mostSeconds, Math.round(seconds)));
        }
        return { shown: true };
      },
    },
  ];
}

/** What about a card breaks the limits of the glass, each as a sentence the model can act on. */
function cardProblems(card, seconds) {
  const problems = [];
  if (!card.reply) problems.push("The headline is empty: it is the answer in a few words.");
  if (card.reply.length > CARD.headline) {
    problems.push(`The headline has ${card.reply.length} characters and may have ${CARD.headline}: say less there and put the rest in rows.`);
  }
  if (card.details.length > CARD.rows) {
    problems.push(`There are ${card.details.length} rows and a card holds ${CARD.rows}: put several items in one row, with " · " between them.`);
  }
  card.details.forEach((row, index) => {
    if (!row.text) problems.push(`Row ${index + 1} has no text.`);
    if (row.text.length > CARD.text) {
      problems.push(`The text of row ${index + 1} has ${row.text.length} characters and may have ${CARD.text}: shorten it or split it over two rows.`);
    }
    if (row.label.length > CARD.label) {
      problems.push(`The label of row ${index + 1} has ${row.label.length} characters and may have ${CARD.label}.`);
    }
  });
  if (seconds !== undefined && !(Number.isInteger(seconds) && seconds >= CARD.leastSeconds && seconds <= CARD.mostSeconds)) {
    problems.push(`seconds must be a whole number from ${CARD.leastSeconds} to ${CARD.mostSeconds}.`);
  }
  return problems;
}
