/**
 * The rules of Mirror Home's dashboard layout, as DashboardLayoutConfig.java
 * has them. The mirror refuses a bad layout with only "Invalid JSON request",
 * so the checks are repeated here to say what was wrong.
 *
 * A widget's x, y, w and h are whole thousandths of the screen: x and w of
 * its width, y and h of its height, with 0,0 at the top left.
 */

export const GRID = 1000;
export const MIN_SIZE = 24;

export const COORDINATES =
  "x, y, w and h are whole thousandths of the screen: x and w of its width, y and h of its height. " +
  "0,0 is the top left corner, so x + w and y + h may not pass 1000. " +
  "The glass is portrait, 1080 by 1920 pixels: one unit is about 1 pixel across and 2 pixels down.";

const GEOMETRY = ["x", "y", "w", "h"];
const SIZES = { board: ["small", "medium", "large"], note: ["auto", "small", "medium", "large"] };
const ALIGNS = ["start", "center", "end"];
const TEXT_LIMIT = { note: 1000 };

/** A widget as the model sees it: what it needs to reason about, nothing more. */
export function describeWidget(widget) {
  const seen = { id: widget.id };
  if (widget.type !== widget.id) seen.type = widget.type;
  seen.visible = Boolean(widget.visible);
  for (const key of GEOMETRY) seen[key] = widget[key];
  if (widget.visible && widget.align) seen.align = widget.align;
  if (widget.locked) seen.locked = true;
  if (widget.text) seen.text = widget.text.length > 60 ? widget.text.slice(0, 57) + "..." : widget.text;
  if (widget.type === "board") {
    seen.size = widget.size;
    if (widget.show && widget.show !== "all") seen.show = widget.show;
  }
  if (widget.type === "note") {
    seen.source = widget.source;
    seen.size = widget.size;
  }
  return seen;
}

/**
 * Applies the asked changes to a copy of the layout. Nothing else in the
 * layout is touched, and nothing is applied at all if any change is wrong.
 *
 * @param {object} layout The layout as the mirror returned it.
 * @param {object[]} changes Each names a widget by id and the fields to set.
 * @param {{ overlapOk?: boolean }} [options] Whether widgets may come to lie over one another.
 * @returns {{ layout: object, changed: string[], problems: string[], notes: string[] }}
 */
export function applyChanges(layout, changes, { overlapOk = false } = {}) {
  const next = structuredClone(layout);
  const widgets = Array.isArray(next.widgets) ? next.widgets : [];
  const problems = [];
  const changed = [];
  const notes = [];
  for (const change of changes) {
    const widget = widgets.find((candidate) => candidate.id === change.id);
    if (!widget) {
      problems.push(`No widget has the id "${change.id}". The ids are: ${widgets.map((w) => w.id).join(", ")}.`);
      continue;
    }
    const before = JSON.stringify(widget);
    problems.push(...applyOne(widget, normalised(change), notes));
    if (JSON.stringify(widget) !== before && !changed.includes(widget.id)) changed.push(widget.id);
  }
  if (problems.length === 0) {
    const covered = overlaps(layout.widgets ?? [], widgets, changed);
    if (overlapOk) notes.push(...covered.map((overlap) => `${overlap} now.`));
    else if (covered.length > 0) {
      problems.push(
        `${covered.map((overlap) => `${overlap}.`).join(" ")} Move or resize those widgets in the same call so that nothing overlaps, ` +
          "choose another place or size, or pass overlapOk: true if lying over one another is what the person wants.",
      );
    }
  }
  if (problems.length > 0) return { layout, changed: [], problems, notes: [] };
  notes.push(...changed.map((id) => placeOf(widgets.find((widget) => widget.id === id), widgets)).filter(Boolean));
  return { layout: next, changed, problems, notes };
}

/**
 * Says in words where a widget has ended up among the others, so that the
 * model can check its arithmetic against what was asked: "below the clock".
 */
function placeOf(widget, widgets) {
  if (!widget.visible) return "";
  const others = widgets.filter((other) => other.id !== widget.id && other.visible && other.type !== "photo");
  const nearest = (gap) =>
    others
      .map((other) => ({ other, gap: gap(other) }))
      .filter((entry) => entry.gap >= -10)
      .sort((a, b) => a.gap - b.gap)[0]?.other;
  const above = nearest((other) => widget.y - (other.y + other.h));
  const below = nearest((other) => other.y - (widget.y + widget.h));
  const parts = [];
  if (above) parts.push(`below "${above.id}"`);
  if (below) parts.push(`above "${below.id}"`);
  if (parts.length === 0) return "";
  return `"${widget.id}" is now ${parts.join(" and ")}.`;
}

/** The spec's tool takes width and height; the layout calls them w and h. */
function normalised(change) {
  const result = { ...change };
  if (result.w === undefined && result.width !== undefined) result.w = result.width;
  if (result.h === undefined && result.height !== undefined) result.h = result.height;
  delete result.width;
  delete result.height;
  return result;
}

function applyOne(widget, change, notes) {
  const problems = [];
  const name = `"${widget.id}"`;
  const moves = GEOMETRY.filter((key) => change[key] !== undefined && change[key] !== widget[key]);
  if (moves.length > 0 && widget.locked) {
    return [
      `${name} is locked in the layout editor, so it cannot be moved or resized. ` +
        "Its owner can unlock it in the phone controls under Display. Showing or hiding it still works.",
    ];
  }
  for (const key of GEOMETRY) {
    if (change[key] === undefined) continue;
    const minimum = key === "w" || key === "h" ? MIN_SIZE : 0;
    if (!Number.isInteger(change[key]) || change[key] < minimum || change[key] > GRID) {
      problems.push(`${name}: ${key} must be a whole number from ${minimum} to ${GRID}, not ${change[key]}.`);
    } else {
      widget[key] = change[key];
    }
  }
  // A box that would reach past the right or bottom edge is pushed back
  // onto the screen: "to the bottom" should not fail for being a little
  // too far down. The answer tells where it ended up.
  if (problems.length === 0) {
    if (widget.x + widget.w > GRID) {
      widget.x = GRID - widget.w;
      notes.push(`${name} did not fit there and is at x ${widget.x}, the furthest right for w ${widget.w}.`);
    }
    if (widget.y + widget.h > GRID) {
      widget.y = GRID - widget.h;
      notes.push(`${name} did not fit there and is at y ${widget.y}, the lowest for h ${widget.h}.`);
    }
  }
  if (change.visible !== undefined) widget.visible = Boolean(change.visible);
  if (change.opacity !== undefined) {
    if (!Number.isInteger(change.opacity) || change.opacity < 10 || change.opacity > 100) {
      problems.push(`${name}: opacity must be a whole number from 10 to 100, not ${change.opacity}.`);
    } else {
      widget.opacity = change.opacity;
    }
  }
  if (change.align !== undefined) {
    if (!ALIGNS.includes(change.align)) problems.push(`${name}: align must be start, center or end.`);
    else widget.align = change.align;
  }
  if (change.size !== undefined) {
    const sizes = SIZES[widget.type];
    if (!sizes) {
      problems.push(`${name} has no size setting. Make it bigger or smaller with w and h: its text follows its height.`);
    } else if (!sizes.includes(change.size)) {
      problems.push(`${name}: size must be one of ${sizes.join(", ")}.`);
    } else {
      widget.size = change.size;
    }
  }
  if (change.text !== undefined) problems.push(...applyText(widget, change.text, name));
  return problems;
}

function applyText(widget, text, name) {
  if (widget.type !== "board" && widget.type !== "note") {
    return [`${name} shows no text of its own. Only a board widget (its heading) and a note widget take text.`];
  }
  const limit = TEXT_LIMIT[widget.type] ?? 120;
  if (typeof text !== "string" || text.length > limit) return [`${name}: text can be up to ${limit} characters.`];
  if (hasControlCharacter(text, widget.type === "note")) {
    return [widget.type === "board" ? `${name}: a board heading is one line.` : `${name}: text cannot hold control characters.`];
  }
  widget.text = text;
  // A note widget shows its own text only when its source says so.
  if (widget.type === "note") widget.source = "text";
  return [];
}

function hasControlCharacter(text, allowLineBreaks) {
  for (const character of text) {
    const code = character.codePointAt(0);
    const control = code < 0x20 || (code >= 0x7f && code <= 0x9f);
    if (control && !(allowLineBreaks && character === "\n")) return true;
  }
  return false;
}

/**
 * Finds where a changed widget has come to lie over another visible one that
 * it did not lie over before. A photo is left out: it may be a frame behind
 * the other widgets.
 *
 * @returns {string[]} one sentence, without its full stop, for each changed widget concerned
 */
function overlaps(before, widgets, changed) {
  const found = [];
  const counts = (widget) => widget.visible && widget.type !== "photo";
  const was = (id) => before.find((candidate) => candidate.id === id);
  for (const id of changed) {
    const widget = widgets.find((candidate) => candidate.id === id);
    if (!counts(widget)) continue;
    const under = widgets.filter((other) => {
      if (other.id === id || !counts(other) || !covers(widget, other)) return false;
      // A pair where both moved is reported once, under the first of them.
      if (changed.includes(other.id) && changed.indexOf(other.id) < changed.indexOf(id)) return false;
      const old = [was(id), was(other.id)];
      return !(old[0] && old[1] && counts(old[0]) && counts(old[1]) && covers(old[0], old[1]));
    });
    if (under.length > 0) {
      found.push(`"${id}" (x ${widget.x} to ${widget.x + widget.w}, y ${widget.y} to ${widget.y + widget.h}) overlaps ${list(under.map(box))}`);
    }
  }
  return found;
}

function box(widget) {
  return `"${widget.id}" (x ${widget.x} to ${widget.x + widget.w}, y ${widget.y} to ${widget.y + widget.h})`;
}

/**
 * True when two boxes share a good part of the smaller one. Boxes that only
 * touch at their edges, as the default clock and weather do, are not worth a
 * remark: their content is aligned away from each other.
 */
function covers(a, b) {
  const across = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const down = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  if (across <= 0 || down <= 0) return false;
  return across * down >= 0.15 * Math.min(a.w * a.h, b.w * b.h);
}

function list(names) {
  return names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}
