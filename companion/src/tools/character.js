import { z } from "zod";

// How the mirror shows itself when it answers: as which character, and where on the glass.

/**
 * What each character of Mirror Home is, in the words a person would ask for
 * it by: the mirror lists its characters by name only, and nobody says
 * "be Mochi" who has not read the manual. One that a later Mirror Home
 * adds is known by its name until it is listed here.
 */
const LOOKS = { blink: "two eyes", wisp: "a ghost", mochi: "a cat", lune: "a moon" };

const NONE = "none";

/** A character as the model reads it: "Mochi (a cat)". */
function label(character) {
  const looks = LOOKS[character.id];
  return looks ? `${character.name} (${looks})` : character.name;
}

/**
 * The character the mirror answers as and the ones it could, by name, for
 * the snapshot; null for a Mirror Home that has no characters. What each
 * one is stands in the tool's description, which is read once and not with
 * every message.
 *
 * @param {object|null} assistant The mirror's answer to GET /api/v1/assistant.
 */
export function describeCharacter(assistant) {
  const known = assistant?.mascots;
  if (!Array.isArray(known) || known.length === 0) return null;
  const chosen = known.find((character) => character.id === assistant.mascot);
  return { now: chosen ? chosen.name : NONE, choices: known.map((character) => character.name) };
}

/** The heights at which the answers can stand, from the upper edge of the glass down, and the sides. */
const HEIGHTS = ["top", "upper", "middle", "lower", "bottom"];
const SIDES = ["left", "center", "right"];

/**
 * Where on the glass the mirror's answers stand, as "bottom center", for the
 * snapshot; null for a Mirror Home whose answers cannot be moved.
 *
 * @param {object|null} assistant The mirror's answer to GET /api/v1/assistant.
 */
export function describePlace(assistant) {
  const place = assistant?.place;
  return typeof place?.height === "string" && typeof place?.side === "string" ? `${place.height} ${place.side}` : null;
}

/** The words by which a character may be asked for, in lower case. */
function names(character) {
  const looks = LOOKS[character.id] ?? "";
  return [character.id, character.name.toLowerCase(), looks, ...looks.split(" ").slice(1)].filter(Boolean);
}

/** @returns {import("../tools.js").Tool[]} */
export function characterTools({ mirror }) {
  return [
    {
      name: "set_character",
      description:
        "Chooses the small character that stands above your words on the glass and acts out what you do: " +
        "the face people see when you answer. Give one of character.choices from the state by its name or by what it is " +
        "(\"Mochi\" or \"cat\"), \"next\" for the one after the present one, or \"none\" for words alone. " +
        "Blink is two eyes, Wisp a ghost, Mochi a cat, Lune a moon.",
      outside:
        "Chooses the small character that stands above the words of the mirror's assistant and acts out what it does: " +
        "the face people see when the mirror answers. Give one of character.choices from the state by its name or by what it is " +
        "(\"Mochi\" or \"cat\"), \"next\" for the one after the present one, or \"none\" for words alone. " +
        "Blink is two eyes, Wisp a ghost, Mochi a cat, Lune a moon.",
      schema: z.object({ character: z.string().min(1) }),
      changes: true,
      async handler({ character }) {
        const before = await mirror.get("/api/v1/assistant");
        const known = Array.isArray(before.mascots) ? before.mascots : [];
        if (known.length === 0) {
          return { error: "This mirror's software has no characters yet. They come with a later version of Mirror Home." };
        }
        const was = known.find((candidate) => candidate.id === before.mascot) ?? null;
        const wanted = character.trim().toLowerCase().replace(/^(the|a|an) /, "");
        let chosen;
        if (["none", "no", "off", "nothing", "no character"].includes(wanted)) {
          chosen = null;
        } else if (wanted === "next") {
          chosen = known[(known.indexOf(was) + 1) % known.length];
        } else {
          const matches = known.filter((candidate) => names(candidate).includes(wanted));
          if (matches.length !== 1) {
            return { error: `No character matches "${character}". The choices are: ${[...known.map(label), NONE].join(", ")}.` };
          }
          chosen = matches[0];
        }
        const now = chosen ? label(chosen) : NONE;
        if ((chosen?.id ?? NONE) === (was?.id ?? NONE)) {
          return { character: now, changed: false, note: chosen ? `You already are ${chosen.name}.` : "There was no character." };
        }
        // The mirror shows the new one with its name and a wave by itself.
        await mirror.call("PUT", "/api/v1/assistant", { body: { mascot: chosen?.id ?? NONE } });
        return { character: now, was: was ? label(was) : NONE, changed: true };
      },
    },
    {
      name: "set_answer_place",
      description:
        "Moves where on the glass your words appear: the panel with your answer and your character. " +
        "height is top, upper, middle, lower or bottom, or \"up\" or \"down\" for one step from where it is; " +
        "side is left, center or right. Give height, side or both; what you leave out stays as it is. " +
        "answersAt in the state is the present place. The mirror shows a line at the new place by itself.",
      outside:
        "Moves where on the glass the mirror's assistant shows its answers, and the lines that say shows: the panel with the words and the character. " +
        "height is top, upper, middle, lower or bottom, or \"up\" or \"down\" for one step from where it is; " +
        "side is left, center or right. Give height, side or both; what is left out stays as it is. " +
        "answersAt in the state is the present place. The mirror shows a line at the new place by itself.",
      schema: z.object({
        height: z.enum([...HEIGHTS, "up", "down"]).optional(),
        side: z.enum(SIDES).optional(),
      }),
      changes: true,
      async handler({ height, side }) {
        if (height === undefined && side === undefined) return { error: "Give height, side or both." };
        const before = await mirror.get("/api/v1/assistant");
        const was = describePlace(before);
        if (!was) {
          return { error: "This mirror's software cannot move its answers yet. That comes with a later version of Mirror Home." };
        }
        // The mirror says which places it has; the order of its heights is from the top down.
        const heights = Array.isArray(before.places?.heights) ? before.places.heights : HEIGHTS;
        const sides = Array.isArray(before.places?.sides) ? before.places.sides : SIDES;
        let wantedHeight = height ?? before.place.height;
        if (height === "up" || height === "down") {
          const step = heights.indexOf(before.place.height) + (height === "up" ? -1 : 1);
          if (step < 0 || step >= heights.length) {
            const end = height === "up" ? "as high as they go" : "as low as they go";
            if (side === undefined || side === before.place.side) {
              return { answersAt: was, changed: false, note: `Your answers are ${end}.` };
            }
            wantedHeight = before.place.height;
          } else {
            wantedHeight = heights[step];
          }
        }
        const wantedSide = side ?? before.place.side;
        if (!heights.includes(wantedHeight) || !sides.includes(wantedSide)) {
          return { error: `This mirror has no such place. Its heights are ${heights.join(", ")}; its sides are ${sides.join(", ")}.` };
        }
        const now = `${wantedHeight} ${wantedSide}`;
        if (now === was) return { answersAt: was, changed: false, note: "Your answers were already there." };
        await mirror.call("PUT", "/api/v1/assistant", { body: { place: { height: wantedHeight, side: wantedSide } } });
        return { answersAt: now, was, changed: true };
      },
    },
  ];
}
