import { z } from "zod";
import { MirrorRefused } from "../mirror.js";
import { fetchState, shortId } from "../state.js";
import { findFilm, parseColor, saveAutomation } from "./common.js";
import { saveLayout } from "./layout.js";

const BRIGHTNESS_STEP = 40;
const DIMMEST = 15;
const BRIGHTEST = 255;
const NO_FILM = "There is no film on the mirror. Films are added in the phone controls under Display.";
const NO_PHOTO = "There is no photo on the mirror. Photos are added in the phone controls under Display.";

/** @returns {import("../tools.js").Tool[]} */
export function displayTools({ mirror, clock }) {
  async function showFilm(wanted) {
    const catalog = await mirror.get("/api/v1/background-videos");
    const videos = catalog.videos ?? [];
    const listed = videos.map((video) => `${shortId(video.id)} (${video.name})`).join(", ");
    if (videos.length === 0) return { error: NO_FILM };
    let film;
    if (wanted.trim().toLowerCase() === "next") {
      if (videos.length < 2) return { error: `There is only one film, ${videos[0].name}, so there is no next one.` };
      const index = videos.findIndex((video) => video.id === catalog.effectiveId);
      film = videos[(index + 1) % videos.length];
    } else {
      film = findFilm(videos, wanted);
      if (!film) return { error: `No film matches "${wanted}". The films are: ${listed}. Use one of these ids.` };
    }
    // Activating a film also turns the background to film mode.
    const after = await mirror.call("POST", `/api/v1/background-videos/${film.id}/activate`);
    const answer = { showing: { id: shortId(film.id), name: film.name } };
    const hold = after.schedule?.hold;
    if (hold) answer.note = `A film schedule is on, so this one shows until ${hold.untilTime}.`;
    return answer;
  }

  /**
   * Which photo of the library is meant: "next", "previous", its place in
   * the library counted from 1, or its name. The photos carry the names a
   * camera gave them, so a person asks by place.
   *
   * @returns {number} its index, or -1 when none is meant
   */
  function findPhoto(photos, wanted, current) {
    const text = wanted.trim().toLowerCase();
    const at = photos.findIndex((photo) => photo.name === current);
    if (text === "next") return at < 0 ? 0 : (at + 1) % photos.length;
    if (text === "previous") return at < 0 ? 0 : (at - 1 + photos.length) % photos.length;
    if (text === "first") return 0;
    if (text === "last") return photos.length - 1;
    if (/^\d+$/.test(text)) return Number(text) >= 1 && Number(text) <= photos.length ? Number(text) - 1 : -1;
    const named = photos.map((photo, index) => (String(photo.name).toLowerCase().includes(text) ? index : -1)).filter((index) => index >= 0);
    return named.length === 1 ? named[0] : -1;
  }

  /** Changes what the layout says of the background: its kind, its colours, its photo, how far it is darkened. */
  async function restyle({ mode, photo, color, secondColor, dim }) {
    const layout = await mirror.get("/api/v1/dashboard/layout");
    const background = { ...(layout.background ?? {}) };
    const answer = {};
    if (color !== undefined) {
      const first = parseColor(color);
      const second = secondColor === undefined ? null : parseColor(secondColor);
      if (!first || (secondColor !== undefined && !second)) {
        return { error: "A colour is written as #rrggbb, for example #1a2b3c. Nothing was changed." };
      }
      background.primary = first;
      if (second) {
        background.secondary = second;
        background.mode = "gradient";
        answer.background = `a gradient from ${first} to ${second}`;
      } else {
        background.mode = "solid";
        answer.background = first === "#000000" ? "black" : `the colour ${first}`;
      }
    } else if (photo !== undefined) {
      const photos = (await mirror.get("/api/v1/photos")).photos ?? [];
      if (photos.length === 0) return { error: NO_PHOTO };
      const onGlass = background.mode === "photo" && background.photo === photos[0].name;
      if (photos.length === 1 && onGlass && ["next", "previous"].includes(photo.trim().toLowerCase())) {
        return { error: "There is only one photo, and it is showing." };
      }
      // "Next" goes on from the photo last shown, also when a film is showing now.
      const index = findPhoto(photos, photo, background.photo);
      if (index < 0) {
        const many = photos.length === 1 ? "is one photo" : `are ${photos.length} photos`;
        return { error: `There ${many}. Give "next", "previous" or a number from 1 to ${photos.length}.` };
      }
      background.photo = photos[index].name;
      background.mode = "photo";
      answer.background = "photo";
      answer.photo = `${index + 1} of ${photos.length}`;
    } else if (mode === "film") {
      const catalog = await mirror.get("/api/v1/background-videos");
      if ((catalog.videos ?? []).length === 0) return { error: NO_FILM };
      background.mode = "video";
      answer.background = "film";
    } else if (mode === "black") {
      background.mode = "solid";
      background.primary = "#000000";
      answer.background = "black";
    } else if (mode === "photo") {
      if (!background.photo) {
        const first = ((await mirror.get("/api/v1/photos")).photos ?? [])[0];
        if (!first) return { error: NO_PHOTO };
        background.photo = first.name;
      }
      background.mode = "photo";
      answer.background = "photo";
    }
    if (dim !== undefined) {
      background.dim = dim;
      answer.darkenedBy = `${dim}%`;
    }
    layout.background = background;
    await saveLayout(mirror, layout);
    return answer;
  }

  return [
    {
      name: "get_state",
      description:
        "Reads the mirror afresh: its local time and zone, whether the display is awake, the widgets and where they are, " +
        "the background and films, the board's items, the weather and voice. " +
        "The state is already in each message, so call this only to check after a change.",
      schema: z.object({}),
      async handler(_args, turn) {
        const state = await fetchState(mirror, clock);
        turn.state = state;
        turn.stateAt = clock.now();
        return state.snapshot;
      },
    },
    {
      name: "look",
      description:
        "Takes a picture of what is drawn on the glass now (widgets, captions, a still of the film), to answer how the display " +
        "looks or to check a rearrangement. It never shows the room, a person or a reflection.",
      schema: z.object({}),
      async handler() {
        const answer = await mirror.request("GET", "/api/v1/screenshot");
        if (answer.status === 409) {
          return { error: "The display is dark, so there is nothing to see. Wake it with set_power if the person wants to look." };
        }
        if (answer.status !== 200 || !answer.contentType.startsWith("image/")) {
          throw new MirrorRefused(answer.status, answer.body?.error || "the mirror gave no picture.");
        }
        return {
          image: { data: answer.buffer.toString("base64"), mimeType: answer.contentType.split(";")[0] },
          text: "This is the glass as it is now.",
        };
      },
    },
    {
      name: "set_power",
      description:
        "Puts the display to sleep (dark) or wakes it. Either holds for four hours unless changed; " +
        "while asleep this way, walking past does not wake it.",
      schema: z.object({ state: z.enum(["asleep", "awake"]) }),
      changes: true,
      async handler({ state }) {
        const after = await mirror.call("POST", `/api/v1/automation/${state === "asleep" ? "sleep" : "wake"}`);
        return { power: after.sleeping ? "asleep" : "awake" };
      },
    },
    {
      name: "set_brightness",
      description:
        `Sets how bright the display is when awake, now and from then on. Give level (${DIMMEST} dimmest to ${BRIGHTEST} brightest) ` +
        "or change (\"brighter\" or \"dimmer\", one step). For \"much brighter\" give a level.",
      schema: z.object({
        level: z.number().int().min(DIMMEST).max(BRIGHTEST).optional(),
        change: z.enum(["brighter", "dimmer"]).optional(),
      }),
      changes: true,
      async handler({ level, change }) {
        if ((level === undefined) === (change === undefined)) {
          return { error: "Give either level or change, not both and not neither." };
        }
        const before = await mirror.get("/api/v1/automation");
        if (before.ambientEnabled && before.ambientLightAvailable) {
          return { error: "The room's light sets the brightness on this mirror, so there is no level to change." };
        }
        const was = before.wakeBrightness;
        const step = change === "brighter" ? BRIGHTNESS_STEP : -BRIGHTNESS_STEP;
        const target = level ?? Math.max(DIMMEST, Math.min(BRIGHTEST, was + step));
        if (target === was) {
          const limit = was >= BRIGHTEST ? " It is at its brightest." : was <= DIMMEST ? " It is at its dimmest." : "";
          return { wakeBrightness: was, changed: false, note: `The brightness was already ${was}.${limit}` };
        }
        await saveAutomation(mirror, before, { wakeBrightness: target });
        // The saved level applies at the next wake; this applies it to a display that is on now.
        if (!before.sleeping) {
          try {
            await mirror.call("POST", "/api/v1/control/brightness", { body: { value: target } });
          } catch (error) {
            if (!(error instanceof MirrorRefused)) throw error;
            return { wakeBrightness: target, was, changed: true, note: "It is saved and applies when the display next wakes." };
          }
        }
        return { wakeBrightness: target, was, changed: true };
      },
    },
    {
      name: "set_background",
      description:
        "Changes what is behind the widgets. Give one of these four. " +
        "video: a film's id or name from the state, or \"next\" for the one after the film showing. " +
        "mode: \"film\" (the chosen film), \"black\" (plain black, the most mirror-like) or \"photo\" (the photo last shown). " +
        "photo: \"next\", \"previous\", or its number in the mirror's library counted from 1, for a particular photo. " +
        "color: one plain colour as #rrggbb, with secondColor for a gradient from the first, top left, to the second, bottom right; " +
        "on this glass dark colours look like mirror and bright ones glow. " +
        "dim darkens whatever is behind the widgets so that they are easier to read: 0 (not at all) to 90 percent. " +
        "It may come alone or with one of the four.",
      schema: z.object({
        video: z.string().min(1).optional(),
        mode: z.enum(["film", "black", "photo"]).optional(),
        photo: z.string().min(1).optional(),
        color: z.string().min(1).optional(),
        secondColor: z.string().min(1).optional(),
        dim: z.number().int().min(0).max(90).optional(),
      }),
      changes: true,
      async handler({ video, mode, photo, color, secondColor, dim }) {
        const given = [video, mode, photo, color].filter((value) => value !== undefined).length;
        if (secondColor !== undefined && color === undefined) return { error: "secondColor needs color: the two ends of a gradient." };
        if (given === 0 && dim === undefined) return { error: "Give video, mode, photo, color or dim." };
        if (given > 1) return { error: "Give one of video, mode, photo and color, not several." };
        if (video === undefined) return restyle({ mode, photo, color, secondColor, dim });
        const answer = await showFilm(video);
        if (answer.error || dim === undefined) return answer;
        return { ...answer, ...(await restyle({ dim })) };
      },
    },
  ];
}
