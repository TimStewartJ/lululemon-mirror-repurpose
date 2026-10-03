/**
 * Makes a text fit the one caption line the glass has: no line breaks, no
 * markdown marks, no emoji, and at most `limit` characters, cut between words.
 *
 * @param {unknown} text
 * @param {number} [limit]
 */
export function oneLine(text, limit = 200) {
  let line = String(text ?? "")
    // Typographic marks are written plainly; the caption's font on the glass may lack them.
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201C\u201D]/g, '"')
    .replace(/\s*\u2014\s*/g, ", ")
    .replace(/\u2013/g, "-")
    .replace(/\u2026/g, "...")
    .replace(/[*`#]+/g, "")
    .replace(/[\p{Extended_Pictographic}\u{FE0F}\u{200D}]/gu, "")
    .replace(/\s+/g, " ")
    .trim();
  if (line.length <= limit) return line;
  // A cut inside the first word would leave nothing worth showing, so a text
  // without spaces is cut where the room ends.
  const room = line.slice(0, limit - 3);
  const lastSpace = room.lastIndexOf(" ");
  line = lastSpace > 0 ? room.slice(0, lastSpace) : room;
  return line.replace(/[\s,;:.-]+$/, "") + "...";
}

/**
 * The limits of a card, as Mirror Home has them: a headline with up to five
 * rows under it, each a short label beside one line of text.
 */
export const CARD = { rows: 5, label: 14, text: 90, headline: 60, leastSeconds: 3, mostSeconds: 30 };

/**
 * Makes rows fit a card: each on one line and cut to its limit, rows without
 * text dropped, and no more of them than the glass has room for. The mirror
 * refuses rows that are longer, so none leave the companion uncut.
 *
 * @param {unknown} rows
 * @returns {{ label: string, text: string }[]}
 */
export function fitRows(rows) {
  return (Array.isArray(rows) ? rows : [])
    .map((row) => ({ label: oneLine(row?.label ?? "", CARD.label), text: oneLine(row?.text ?? "", CARD.text) }))
    .filter((row) => row.text.length > 0)
    .slice(0, CARD.rows);
}

/** The line above the rows: a headline when there are rows, the whole answer when there are none. */
export function headline(text, rows) {
  return oneLine(text, rows.length > 0 ? CARD.headline : 200);
}

/**
 * A model now and then gives its whole answer twice in one message. Shown on
 * the glass that looks broken, so the second copy is dropped.
 */
export function withoutRepeat(text) {
  const whole = String(text ?? "").trim();
  for (const gap of [0, 1]) {
    const half = (whole.length - gap) / 2;
    if (Number.isInteger(half) && half >= 8 && whole.slice(0, half) === whole.slice(half + gap) && (gap === 0 || /\s/.test(whole[half]))) {
      return whole.slice(0, half);
    }
  }
  return whole;
}

/** True when the reply is a question the person is expected to answer. */
export function asksSomething(reply) {
  return reply.endsWith("?");
}
