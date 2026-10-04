import { z } from "zod";

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
  ];
}
