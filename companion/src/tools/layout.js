import { z } from "zod";
import { applyChanges, describeWidget } from "../layout.js";
import { MirrorRefused } from "../mirror.js";

const change = z.object({
  id: z.string().describe("The widget's id, as get_state lists it."),
  visible: z.boolean().optional(),
  x: z.number().int().optional(),
  y: z.number().int().optional(),
  w: z.number().int().optional(),
  h: z.number().int().optional(),
  width: z.number().int().optional().describe("Another name for w."),
  height: z.number().int().optional().describe("Another name for h."),
  opacity: z.number().int().optional().describe("10 (faint) to 100 (full)."),
  align: z.enum(["start", "center", "end"]).optional().describe("Where its content sits in its box: left, middle or right."),
  size: z.string().optional().describe("Text size of a board (small, medium, large) or a note (auto, small, medium, large)."),
  text: z.string().optional().describe("A board widget's heading, or a note widget's own words."),
});

/**
 * Checks a layout with the mirror and then stores it. The mirror answers a
 * layout it will not take with a bare "Invalid JSON request".
 *
 * @returns {Promise<object>} the layout as the mirror now has it
 */
export async function saveLayout(mirror, layout) {
  try {
    await mirror.call("POST", "/api/v1/dashboard/layout/validate", { body: layout });
  } catch (error) {
    if (error instanceof MirrorRefused && error.status === 400) {
      throw new MirrorRefused(
        400,
        "the mirror does not accept this layout and does not say why. Nothing was changed. " +
          "Change less at a time, with whole numbers that keep each widget on the screen.",
      );
    }
    throw error;
  }
  return mirror.call("PUT", "/api/v1/dashboard/layout", { body: layout });
}

/** @returns {import("../tools.js").Tool[]} */
export function layoutTools({ mirror }) {
  return [
    {
      name: "arrange_widgets",
      description:
        "Shows, hides, moves or resizes widgets on the glass. Give only the fields to change; everything else stays as it is. " +
        "x, y, w and h are whole thousandths of the screen: x and w of its width, y and h of its height, " +
        "with 0,0 at the top left, so a widget at the bottom has y = 1000 - h and one at the right has x = 1000 - w. " +
        "w and h are at least 24. The glass is portrait (1080 by 1920 pixels). " +
        "A widget's text follows its height and shrinks if the box is too narrow, so to make one bigger raise h and w together; " +
        "a board and a fixed-size note use size instead. " +
        "Below a widget means a y of at least that widget's y + h; above it means a y + h of at most its y. " +
        "Where there is no room for that, move the other widget in the same call. " +
        "A change that would make visible widgets overlap is refused with their boxes, so that you can move them apart in one call. " +
        "All changes are checked first and applied together, or not at all. Returns every widget as it is afterwards, " +
        "with notes that are for you: mention one to the person only if it matters to them.",
      schema: z.object({
        changes: z.array(change).min(1).max(20),
        overlapOk: z.boolean().optional().describe("True only when the person wants widgets to lie over one another."),
      }),
      changes: true,
      async handler({ changes, overlapOk }, turn) {
        const layout = await mirror.get("/api/v1/dashboard/layout");
        const result = applyChanges(layout, changes, { overlapOk });
        if (result.problems.length > 0) {
          return { error: `Nothing was changed. ${result.problems.join(" ")}` };
        }
        if (result.changed.length === 0) {
          return { changed: [], note: "The widgets were already like that.", widgets: layout.widgets.map(describeWidget) };
        }
        const saved = await saveLayout(mirror, result.layout);
        if (turn.state) turn.state.snapshot.widgets = saved.widgets.map(describeWidget);
        const answer = { changed: result.changed, widgets: saved.widgets.map(describeWidget) };
        if (result.notes.length > 0) answer.notes = result.notes;
        return answer;
      },
    },
  ];
}
