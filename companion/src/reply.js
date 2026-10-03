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
