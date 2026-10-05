import { z } from "zod";
import { MirrorRefused } from "../mirror.js";
import { parseIsoWithOffset } from "../time.js";
import { parseColor } from "./common.js";

// Moments: what you put on the glass for a while as part of an answer, beyond
// the line of words: a countdown that runs, a few large words, a list, a
// small chart, a drawing. The mirror takes each one away when its time is up.

const KINDS = ["text", "countdown", "list", "chart", "drawing"];
const HEIGHTS = ["top", "upper", "middle", "lower", "bottom"];
const SIDES = ["left", "center", "right"];
const SIZES = ["small", "medium", "large"];
const MOST_SECONDS = 6 * 60 * 60;
// How long a moment stays when nothing is said: long enough to read it from across a room.
const STAYS = { text: 45, list: 90, chart: 60, drawing: 45 };

/**
 * The moments on the glass as the model reads them: what each is, by which
 * id it can be replaced or taken away, and how long it has left.
 *
 * @param {object|null} document The mirror's answer to GET /api/v1/moments; null for a Mirror Home without moments.
 * @param {number} now The mirror's clock.
 */
export function describeMoments(document, now) {
  if (!Array.isArray(document?.moments)) return null;
  return document.moments
    .filter((moment) => moment.until > now)
    .map((moment) => {
      const seen = { id: moment.id, kind: moment.kind };
      const shows = moment.title || moment.text || "";
      if (shows) seen.shows = shows.length > 40 ? shows.slice(0, 37) + "..." : shows;
      if (moment.kind === "countdown") seen.runsOutIn = `${Math.max(0, Math.round((moment.endsAt - now) / 1000))} s`;
      seen.leavesIn = `${Math.max(1, Math.round((moment.until - now) / 1000))} s`;
      return seen;
    });
}

const shape = z.object({
  shape: z.enum(["line", "circle", "rect", "path", "text"]),
  x: z.number().optional(),
  y: z.number().optional(),
  x1: z.number().optional(),
  y1: z.number().optional(),
  x2: z.number().optional(),
  y2: z.number().optional(),
  r: z.number().optional(),
  w: z.number().optional(),
  h: z.number().optional(),
  round: z.number().optional(),
  d: z.string().optional().describe("SVG path data for a path."),
  text: z.string().optional(),
  size: z.number().optional(),
  stroke: z.string().optional().describe("#rrggbb or none. Left out, the moment's colour."),
  fill: z.string().optional().describe("#rrggbb or none. Left out, none; for text, the moment's colour."),
  width: z.number().optional().describe("Line width; 1.2 if left out."),
});

/** @returns {import("../tools.js").Tool[]} */
export function momentTools({ mirror, clock }) {
  /** The mirror's own clock at this moment, as far as the turn knows it. */
  const mirrorNow = (turn) => (turn.state ? turn.state.now + (clock.now() - turn.stateAt) : clock.now());

  return [
    {
      name: "show_moment",
      description:
        "Puts something on the glass for a while, beside your line of words, and the mirror takes it away by itself. " +
        "Use it when seeing serves better than a sentence, or to delight: a timer that can be watched running down, " +
        "a few words written large, the steps of a recipe, numbers as a chart, a small drawing. kind is one of: " +
        "text (text: up to 280 characters, a line break for a new line); " +
        "countdown (countdownSeconds from now, or countdownTo as an ISO time with the mirror's UTC offset; it runs on the glass and stays 20 seconds after it has run out); " +
        "list (rows: up to 8 of {label, text}; a label is a number, a time or nothing); " +
        "chart (values: 2 to 12 of {label, value}; chart: bars, or line for how something goes over time); " +
        "drawing (shapes on a square 100 units wide and high, 0,0 at its top left: line {x1,y1,x2,y2}, circle {x,y,r}, " +
        "rect {x,y,w,h,round}, path {d}, text {x,y,text,size} centred on x; thin lines in one or two colours suit this glass, where black is mirror). " +
        "title is a small heading over it. Letters, digits and plain punctuation only, in every text of it: this glass draws no emoji or pictographs. " +
        "seconds is how long it stays (5 to 21600); left out, about a minute. A countdown needs none. " +
        "You need not place it: moments stand in a column in the middle of the glass in the order they came, and widgets under them " +
        "step back until they leave. Give height (top, upper, middle, lower, bottom) or side (left, center, right) only when asked for a place. " +
        "size is small, medium (if left out) or large, which is for when something big is asked for. color is #rrggbb; motion is pulse, float or spin, for a drawing or a word that should live. " +
        "id names it: showing the same id again replaces it where it stands (a score that changes); moments in the state lists what is showing. " +
        "Nothing else is needed for it: the display need not be woken and no widget moved. " +
        "Your line of words still shows: keep it to a few words and do not repeat what the moment says.",
      schema: z.object({
        kind: z.enum(KINDS),
        id: z.string().regex(/^[a-z0-9][a-z0-9-]{0,31}$/, "takes up to 32 small letters, digits and dashes").optional(),
        title: z.string().max(40).optional(),
        text: z.string().max(280).optional(),
        countdownSeconds: z.number().min(1).max(MOST_SECONDS).optional(),
        countdownTo: z.string().optional(),
        rows: z.array(z.object({ label: z.string().max(14).optional(), text: z.string().min(1).max(60) })).max(8).optional(),
        values: z.array(z.object({ label: z.string().max(8), value: z.number() })).max(12).optional(),
        chart: z.enum(["bars", "line"]).optional(),
        shapes: z.array(shape).max(40).optional(),
        seconds: z.number().int().min(5).max(MOST_SECONDS).optional(),
        height: z.enum(HEIGHTS).optional(),
        side: z.enum(SIDES).optional(),
        size: z.enum(SIZES).optional(),
        color: z.string().optional(),
        motion: z.enum(["none", "pulse", "float", "spin"]).optional(),
      }),
      changes: true,
      async handler(args, turn) {
        const { kind } = args;
        const moment = { kind };
        if (args.id) moment.id = args.id;
        if (args.title?.trim()) moment.title = args.title.trim();
        if (args.motion && args.motion !== "none") moment.motion = args.motion;
        // A countdown stays until it has run out and a little longer, whatever is said: one that left earlier would be no use.
        if (kind !== "countdown") moment.seconds = args.seconds ?? STAYS[kind];
        // Where it goes is for the glass, which knows what is drawn where; a place is passed on only when one was asked for.
        for (const name of ["size", "height", "side"]) if (args[name]) moment[name] = args[name];
        if (args.color !== undefined) {
          moment.color = parseColor(args.color);
          if (!moment.color) return { error: "color is written as #rrggbb, for example #ffd9a0. Nothing was shown." };
        }
        const needs = { text: "text", list: "rows", chart: "values", drawing: "shapes" }[kind];
        if (needs && !(args[needs]?.length > 0)) return { error: `A ${kind} needs ${needs}. Nothing was shown.` };
        if (kind === "text") moment.text = args.text.trim();
        if (kind === "list") moment.rows = args.rows.map((row) => ({ label: row.label ?? "", text: row.text }));
        if (kind === "chart") Object.assign(moment, { values: args.values, chart: args.chart ?? "bars" });
        if (kind === "drawing") moment.shapes = args.shapes;
        const now = mirrorNow(turn);
        if (kind === "countdown") {
          if ((args.countdownSeconds === undefined) === (args.countdownTo === undefined)) {
            return { error: "A countdown needs countdownSeconds or countdownTo, one of them. Nothing was shown." };
          }
          const until = args.countdownTo === undefined ? null : parseIsoWithOffset(args.countdownTo);
          if (until?.problem) return { error: "countdownTo is an ISO time with its UTC offset, such as 2026-10-05T07:00:00-07:00. Nothing was shown." };
          moment.endsAt = until ? until.ms : Math.round(now + args.countdownSeconds * 1000);
          if (moment.endsAt <= now) return { error: "That moment has passed already. Nothing was shown." };
          if (moment.endsAt > now + MOST_SECONDS * 1000) {
            return { error: "A countdown runs for six hours at most. For something further off, add a reminder to the board instead." };
          }
        }
        let answer;
        try {
          answer = await mirror.call("POST", "/api/v1/moments", { body: moment });
        } catch (error) {
          if (!(error instanceof MirrorRefused)) throw error;
          if (error.status === 404) {
            return { error: "This mirror's software cannot show moments yet. That comes with a later version of Mirror Home. Answer in words." };
          }
          if (error.status !== 400) throw error;
          return { error: `The mirror did not take it: ${error.message}. Nothing was shown.` };
        }
        const shown = answer.moment;
        const result = { showing: shown.id, kind, leavesIn: `${Math.round((shown.until - now) / 1000)} s` };
        if (kind === "countdown") result.runsOutIn = `${Math.round((shown.endsAt - now) / 1000)} s`;
        if (answer.replaced) result.replaced = true;
        if (answer.shown === false) result.note = "The display is dark, so nobody sees it yet.";
        return result;
      },
    },
    {
      name: "end_moment",
      description:
        "Takes a moment off the glass before its time is up: \"stop the timer\", \"take that down\", \"clear the screen\". " +
        "Give its id from moments in the state, or \"all\" for every one. Widgets and the board are not moments: they stay as they are.",
      schema: z.object({ id: z.string().min(1).max(32) }),
      changes: true,
      async handler({ id }) {
        const everything = id.trim().toLowerCase() === "all";
        const answer = await mirror.request("DELETE", everything ? "/api/v1/moments" : `/api/v1/moments/${encodeURIComponent(id.trim())}`);
        // A Mirror Home without moments has none to take down.
        if (answer.status === 404 && everything) return { removed: 0, note: "No moment was on the glass." };
        if (answer.status === 404) {
          const showing = await mirror.request("GET", "/api/v1/moments");
          const ids = (showing.body?.moments ?? []).map((moment) => moment.id);
          return { error: ids.length > 0 ? `No moment is called "${id}". Showing now: ${ids.join(", ")}.` : "No moment is on the glass." };
        }
        if (answer.status !== 200) throw new MirrorRefused(answer.status, answer.body?.error || `the mirror answered ${answer.status}.`);
        const removed = Number(answer.body?.removed) || 0;
        return removed > 0 ? { removed } : { removed: 0, note: "No moment was on the glass." };
      },
    },
  ];
}
