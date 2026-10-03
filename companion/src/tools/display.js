import { z } from "zod";
import { MirrorRefused } from "../mirror.js";
import { fetchState, shortId } from "../state.js";
import { saveLayout } from "./layout.js";

const BRIGHTNESS_STEP = 40;
const DIMMEST = 15;
const BRIGHTEST = 255;

/** @returns {import("../tools.js").Tool[]} */
export function displayTools({ mirror, clock }) {
  /** Finds a film by its id, the start of its id, or its name. */
  function findFilm(videos, wanted) {
    const text = wanted.trim().toLowerCase();
    const plain = (name) => String(name).toLowerCase().replace(/\.[a-z0-9]+$/, "");
    const matches = videos.filter(
      (video) =>
        video.id === text ||
        (text.length >= 6 && video.id.startsWith(text)) ||
        String(video.name).toLowerCase() === text ||
        plain(video.name) === plain(text),
    );
    return matches.length === 1 ? matches[0] : null;
  }

  async function showFilm(wanted) {
    const catalog = await mirror.get("/api/v1/background-videos");
    const videos = catalog.videos ?? [];
    const listed = videos.map((video) => `${shortId(video.id)} (${video.name})`).join(", ");
    if (videos.length === 0) {
      return { error: "There is no film on the mirror. Films are added in the phone controls under Display." };
    }
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

  async function setMode(mode) {
    const layout = await mirror.get("/api/v1/dashboard/layout");
    const background = layout.background ?? {};
    if (mode === "film") {
      const catalog = await mirror.get("/api/v1/background-videos");
      if ((catalog.videos ?? []).length === 0) {
        return { error: "There is no film on the mirror. Films are added in the phone controls under Display." };
      }
      background.mode = "video";
    } else if (mode === "black") {
      background.mode = "solid";
      background.primary = "#000000";
    } else {
      if (!background.photo) {
        const library = await mirror.get("/api/v1/photos");
        const first = (library.photos ?? [])[0];
        if (!first) {
          return { error: "There is no photo on the mirror. Photos are added in the phone controls under Display." };
        }
        background.photo = first.name;
      }
      background.mode = "photo";
    }
    layout.background = background;
    const saved = await saveLayout(mirror, layout);
    return { background: saved.background.mode === "video" ? "film" : mode };
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
        // The mirror takes its schedule settings as a whole, so they are sent
        // back as read with one number changed.
        await mirror.call("PUT", "/api/v1/automation", {
          body: {
            enabled: before.enabled,
            wakeTime: before.wakeTime,
            sleepTime: before.sleepTime,
            wakeBrightness: target,
            ambientEnabled: before.ambientEnabled,
            ambientMinimum: before.ambientMinimum,
            ambientMaximum: before.ambientMaximum,
            motionEnabled: before.motionEnabled,
            motionTimeoutSeconds: before.motionTimeoutSeconds,
            motionSensitivity: before.motionSensitivity,
          },
        });
        // Saving those settings ends a sleep or wake someone asked for, so it is asked for again.
        if (before.manualOverride) {
          await mirror.call("POST", `/api/v1/automation/${before.sleeping ? "sleep" : "wake"}`);
        }
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
        "Changes what is behind the widgets. video: a film's id or name from the state, or \"next\" for the one after the film showing. " +
        "mode: \"film\" (the chosen film), \"black\" (plain black, the most mirror-like) or \"photo\" (a photo from the mirror's library). " +
        "Give video or mode.",
      schema: z.object({
        video: z.string().min(1).optional(),
        mode: z.enum(["film", "black", "photo"]).optional(),
      }),
      changes: true,
      async handler({ video, mode }) {
        if (video === undefined && mode === undefined) return { error: "Give video or mode." };
        if (video !== undefined) return showFilm(video);
        return setMode(mode);
      },
    },
  ];
}
