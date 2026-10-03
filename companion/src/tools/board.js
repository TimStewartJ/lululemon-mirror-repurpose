import crypto from "node:crypto";
import { z } from "zod";
import { describeWidget, freePlace, placeOf } from "../layout.js";
import { describeItem } from "../state.js";
import { formatOffset, localIso, parseIsoWithOffset } from "../time.js";
import { saveLayout } from "./layout.js";

const ID_LETTERS = "abcdefghijklmnopqrstuvwxyz0123456789";
// The mirror drops an item a day after it was written unless told otherwise.
// That suits a note, but a to-do someone asked for should outlast the day.
const TODO_STAYS_SECONDS = 7 * 24 * 60 * 60;

/** An id in the style the mirror picks itself: eight lower-case letters and digits. */
function newItemId() {
  return Array.from(crypto.randomBytes(8), (byte) => ID_LETTERS[byte % ID_LETTERS.length]).join("");
}

/** The mirror's clock now, judged from the state fetched for this turn. */
function mirrorNow(turn, clock) {
  return turn.state ? turn.state.now + (clock.now() - turn.stateAt) : clock.now();
}

/**
 * Turns the model's due time into milliseconds, or says what is wrong with
 * it. A time without an offset is refused: the mirror would have to guess.
 *
 * @returns {{ ms: number } | { error: string }}
 */
function readDue(due, turn, clock) {
  const offset = turn.state ? turn.state.offsetMinutes : null;
  const now = mirrorNow(turn, clock);
  const example = offset === null ? "2026-10-04T07:00:00-07:00" : localIso(now + 3_600_000, offset);
  const parsed = parseIsoWithOffset(due);
  if (parsed.problem === "offset") {
    const zone = offset === null ? "" : ` The mirror's offset is ${formatOffset(offset)}.`;
    return { error: `due needs its offset from UTC.${zone} Write it like ${example}.` };
  }
  if (parsed.problem === "impossible") return { error: `due is not a real date and time: ${due}.` };
  if (parsed.problem) return { error: `due is not a time the mirror can read. Write it like ${example}.` };
  if (parsed.ms < now - 60_000) {
    const reads = offset === null ? new Date(now).toISOString() : localIso(now, offset);
    return { error: `That time has already passed: the mirror's clock reads ${reads}. Give a time that is still to come.` };
  }
  return { ms: parsed.ms };
}

/** @returns {import("../tools.js").Tool[]} */
export function boardTools({ mirror, clock }) {
  const offsetOf = (turn) => (turn.state ? turn.state.offsetMinutes : 0);

  const lists = (widget, kind) => !widget.show || widget.show === "all" || widget.show === kind;

  /** Whether the state read for this turn shows a Board widget that would list an item of this kind. */
  function seenListed(turn, kind) {
    const widgets = turn.state?.snapshot.widgets;
    if (!Array.isArray(widgets)) return false;
    return widgets.some((widget) => (widget.type ?? widget.id) === "board" && widget.visible && lists(widget, kind));
  }

  /**
   * Makes sure a Board widget is on the glass and lists items of this kind:
   * an item nobody can see is no use. A board that was hidden is not put on
   * top of what took its place meanwhile: it is given a free place.
   *
   * @returns {Promise<{ layout: object, moved: string } | null>} the layout as it is now, when it had to be
   *   changed, and a sentence on where the board went if that is not where it was kept
   */
  async function putOnGlass(kind) {
    const layout = await mirror.get("/api/v1/dashboard/layout");
    const boards = (layout.widgets ?? []).filter((widget) => widget.type === "board");
    if (boards.length === 0 || boards.some((widget) => widget.visible && lists(widget, kind))) return null;
    const board = boards.find((widget) => lists(widget, kind)) ?? boards.find((widget) => widget.visible) ?? boards[0];
    let moved = "";
    // A board its owner locked in the layout editor stays where it was put.
    const place = board.visible || board.locked ? null : freePlace(board, layout.widgets);
    board.visible = true;
    if (place) {
      const shrunk = place.h < board.h;
      Object.assign(board, place);
      const where = placeOf(board, layout.widgets) || `"${board.id}" is now at x ${board.x}, y ${board.y}.`;
      moved = `Its usual place was taken, so ${where}${shrunk ? " It is less tall than before." : ""}`;
    }
    if (!lists(board, kind)) board.show = "all";
    return { layout: await saveLayout(mirror, layout), moved };
  }

  return [
    {
      name: "board_add",
      description:
        "Puts a note, to-do or reminder on the mirror's board, and shows the board if it was hidden. " +
        "A reminder needs due; a to-do may have one; a note has none. A timer (\"ten minutes\") is a reminder due then. " +
        "due is ISO 8601 with the mirror's UTC offset, worked out from now.local in the state, " +
        "for example 2026-10-04T07:00:00-07:00. Keep the title to a few words that read well from across a room; " +
        "put detail in body. Without ttlSeconds a to-do with no due time stays a week, and anything else leaves a day after it was written or was due.",
      schema: z.object({
        kind: z.enum(["note", "todo", "reminder"]),
        title: z.string().min(1).max(120),
        body: z.string().max(500).optional(),
        due: z.string().optional(),
        priority: z.enum(["low", "normal", "high"]).optional(),
        ttlSeconds: z.number().int().min(60).max(31_622_400).optional().describe("How long the item stays, counted from now."),
      }),
      changes: true,
      async handler(args, turn) {
        const item = { kind: args.kind, title: args.title.replace(/\s+/g, " ").trim() };
        if (args.body) item.body = args.body;
        if (args.priority) item.priority = args.priority;
        if (args.ttlSeconds) item.ttlSeconds = args.ttlSeconds;
        else if (args.kind === "todo" && !args.due) item.ttlSeconds = TODO_STAYS_SECONDS;
        if (args.kind === "note" && args.due) {
          return { error: "A note has no due time. Make it a reminder or a todo, or leave due out." };
        }
        if (args.kind === "reminder" && !args.due) {
          return { error: "A reminder needs due, the moment it is about. If the person named no time, ask when." };
        }
        if (args.due) {
          const due = readDue(args.due, turn, clock);
          if (due.error) return due;
          item.due = due.ms;
        }
        // PUT with an id chosen here, not POST: if the answer is lost and the
        // request is sent again, the item is not added twice.
        const saved = await mirror.call("PUT", `/api/v1/board/items/${newItemId()}`, { body: item });
        const answer = { added: describeItem(saved.item, offsetOf(turn)) };
        if (turn.kind === "conversation" && (saved.notice || !seenListed(turn, args.kind))) {
          try {
            const shown = await putOnGlass(args.kind);
            if (shown) {
              answer.boardNowShown = true;
              if (shown.moved) answer.boardMoved = shown.moved;
              if (turn.state) turn.state.snapshot.widgets = shown.layout.widgets.map(describeWidget);
            } else if (saved.notice) {
              answer.note = saved.notice;
            }
          } catch {
            answer.note = "The item is kept, but the board could not be put on the glass.";
          }
        }
        return answer;
      },
    },
    {
      name: "board_update",
      description:
        "Changes an item on the board: marks a to-do or reminder done or not done, or changes its title, body, due time or priority. " +
        "Give only the fields to change. due is ISO 8601 with the mirror's UTC offset.",
      schema: z.object({
        id: z.string().min(1).describe("The item's id, as the state lists it."),
        done: z.boolean().optional(),
        title: z.string().min(1).max(120).optional(),
        body: z.string().max(500).optional(),
        due: z.string().optional(),
        priority: z.enum(["low", "normal", "high"]).optional(),
      }),
      changes: true,
      async handler({ id, due, ...fields }, turn) {
        if (due !== undefined) {
          const parsed = readDue(due, turn, clock);
          if (parsed.error) return parsed;
          fields.due = parsed.ms;
        }
        if (Object.keys(fields).length === 0) return { error: "Say what to change: done, title, body, due or priority." };
        const saved = await mirror.call("PATCH", `/api/v1/board/items/${encodeURIComponent(id)}`, { body: fields });
        return { updated: describeItem(saved.item, offsetOf(turn)) };
      },
    },
    {
      name: "board_remove",
      description:
        "Takes items off the board: one by its id, every finished one with all \"done\", or the whole board with all \"everything\".",
      schema: z.object({
        id: z.string().min(1).optional(),
        all: z.enum(["done", "everything"]).optional(),
      }),
      changes: true,
      async handler({ id, all }) {
        if ((id === undefined) === (all === undefined)) {
          return { error: "Give either the id of one item, or all: \"done\" or \"everything\"." };
        }
        const path = id
          ? `/api/v1/board/items/${encodeURIComponent(id)}`
          : `/api/v1/board/items?${all === "done" ? "done=true" : "all=true"}`;
        const answer = await mirror.call("DELETE", path);
        return { removed: answer.deleted ?? 0 };
      },
    },
  ];
}
