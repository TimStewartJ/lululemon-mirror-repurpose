import http from "node:http";
import { Board, BoardRefusal, invalid, notFound } from "./mirror-board.js";
import { defaultLayout, normalizeLayout } from "./mirror-layout.js";

const FILMS = [
  { id: "546e5d02" + "a".repeat(56), name: "four-seasons-spatial-120s.mp4" },
  { id: "9c1f44e7" + "b".repeat(56), name: "luminous-flowers-spatial-180s.mp4" },
  { id: "e03b77d1" + "c".repeat(56), name: "still-water-at-dusk.mp4" },
];

export const PAIRING_CODE = "123456";

// A real JPEG of 16 by 16 pixels: black with a white bar. A model that is
// handed the "screenshot" needs a picture it can open.
const TINY_JPEG = Buffer.from(
  "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDABQODxIPDRQSERIXFhQYHzMhHxwcHz8tLyUzSkFOTUlBSEZSXHZkUldvWEZIZoxob3p9hIWET2ORm4+AmnaBhH//" +
    "2wBDARYXFx8bHzwhITx/VEhUf39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f39/f3//wAARCAAQABADASIAAhEBAxEB/8QA" +
    "HwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK" +
    "FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG" +
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAEC" +
    "AxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE" +
    "hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDI0218OyWM" +
    "b39/cRXJzvRFJA5OP4T2x3rM1JLSO+kSwleW2GNjuME8DPYd89qrUUAf/9k=",
  "base64",
);

/**
 * A stand-in for Mirror Home's control API, for the tests and the try-scripts.
 * It keeps a real layout and board, follows the mirror's rules for both, and
 * records every request so that a test can look at what was sent.
 *
 * @param {Object} [options]
 * @param {string} [options.token]
 * @param {() => number} [options.now] The mirror's clock.
 * @param {string} [options.timeZone]
 * @param {number} [options.utcOffsetMinutes]
 * @param {number} [options.port] The port to listen on; a free one by default.
 * @param {(request: { method: string, path: string, body: object | null }) => void} [options.onRequest]
 */
export async function startFakeMirror({
  token = "test-mirror-token",
  port = 0,
  onRequest = () => {},
  now = () => Date.now(),
  timeZone = "America/Los_Angeles",
  utcOffsetMinutes = -420,
} = {}) {
  const state = {
    layout: { ...defaultLayout(), background: { ...defaultLayout().background, mode: "video" } },
    board: new Board(now),
    films: FILMS.map((film) => ({ ...film })),
    activeFilm: FILMS[0].id,
    photos: [{ name: "garden.jpg", sizeBytes: 204800 }],
    automation: {
      enabled: true, wakeTime: "06:30", sleepTime: "23:00", wakeBrightness: 180, sleeping: false, sleepReason: "none",
      manualOverride: false, ambientLightAvailable: false, ambientEnabled: false, ambientMinimum: 20, ambientMaximum: 220,
      ambientLux: null, motionEnabled: true, motionTimeoutSeconds: 300, motionSensitivity: 6,
      motion: { available: true, monitoring: true },
    },
    brightness: 180,
    weather: true,
    /** The character the mirror answers as, and the ones it has; null for a Mirror Home without any. */
    mascot: "none",
    mascots: [
      { id: "blink", name: "Blink" }, { id: "wisp", name: "Wisp" }, { id: "mochi", name: "Mochi" }, { id: "lune", name: "Lune" },
    ],
    /** Where the mirror's answers stand, and where they can; null for a Mirror Home that cannot move them. */
    place: { height: "bottom", side: "center" },
    places: { heights: ["top", "upper", "middle", "lower", "bottom"], sides: ["left", "center", "right"] },
    /** Captions shown or asked for, oldest first: { text, kind, seconds, shown }, and details when rows came along. */
    said: [],
    /** Every request received: { method, path, body }. */
    requests: [],
    /** How many of the next connections are dropped without an answer, as a sleeping radio does. */
    dropNext: 0,
    /** Paths answered with this status instead of their usual answer: path to status. */
    failing: new Map(),
  };

  function weather() {
    if (!state.weather) {
      return { config: { enabled: false, locationName: "", units: "metric" }, state: "unconfigured", refreshing: false, stale: false, updatedAt: null, nextRefreshAt: null, error: null, data: null };
    }
    const hour = 60 * 60 * 1000;
    const thisHour = Math.floor(now() / hour) * hour;
    const midnight = Math.floor((now() + utcOffsetMinutes * 60_000) / (24 * hour)) * 24 * hour - utcOffsetMinutes * 60_000;
    const day = (index, high, low, rain, condition) => ({
      time: midnight + index * 24 * hour, high, low, precipitationProbability: rain, weatherCode: 3, condition,
      sunrise: midnight + index * 24 * hour + 7 * hour, sunset: midnight + index * 24 * hour + 18.75 * hour,
    });
    return {
      config: { enabled: true, locationName: "Seattle", units: "metric" },
      state: "ready", refreshing: false, stale: false, updatedAt: now() - 600_000, nextRefreshAt: now() + 1_200_000, error: null,
      data: {
        provider: "open-meteo", fetchedAt: now() - 600_000, locationName: "Seattle", unitsSystem: "metric",
        units: { temperature: "°C", windSpeed: "km/h", precipitation: "mm" },
        current: { time: thisHour, temperature: 12.4, apparentTemperature: 10.9, weatherCode: 3, condition: "Overcast", daylight: true, precipitation: 0, windSpeed: 9.2 },
        hourly: [1, 2, 3, 4, 5, 6, 7, 8].map((index) => ({
          time: thisHour + index * hour, temperature: 12 + index * 0.5, precipitationProbability: index >= 4 ? 70 : 10,
          weatherCode: index >= 4 ? 61 : 3, condition: index >= 4 ? "Light rain" : "Overcast",
        })),
        daily: [day(0, 16.2, 9.1, 70, "Light rain"), day(1, 18.4, 8.3, 10, "Mostly clear"), day(2, 15, 9, 40, "Overcast")],
      },
    };
  }

  function automation() {
    return { ...state.automation };
  }

  function catalog() {
    return {
      videos: state.films.map((film) => ({
        id: film.id, name: film.name, sizeBytes: 90_000_000, addedAt: 1790000000000, mimeType: "video/mp4", width: 1080, height: 1920,
        rotation: 0, durationMs: 120000, frameRate: 30, bitrate: 6_000_000, decoderName: "OMX.qcom.video.decoder.avc",
        avcProfile: 8, avcLevel: 4096, hasAudio: false, posterAvailable: true,
        active: film.id === state.activeFilm, previous: false, showing: film.id === state.activeFilm, scheduledStarts: [],
      })),
      activeId: state.activeFilm, previousId: "", effectiveId: state.activeFilm, canRollback: false,
      schedule: { enabled: false, slots: [], active: false, maxSlots: 8, utcOffsetMinutes, current: null, next: null, nextChangeAt: null, hold: null },
      totalBytes: 270_000_000, usableBytes: 3_000_000_000, maxVideoBytes: 268435456, maxLibraryBytes: 805306368, minFreeBytes: 536870912, maxVideos: 12,
    };
  }

  function status() {
    return {
      apiVersion: 1, appVersion: "2.3.0", deviceUptimeSeconds: 86400, paired: true, displayName: "Mirror",
      timeZone, utcOffsetMinutes, nextUtcOffsetChange: null, clock24Hour: false,
      mirrorBinderConnected: true, systemHelperConnected: false,
      brightness: state.automation.sleeping ? 0 : state.brightness,
      wifi: { connected: true, ssid: "home", ipAddress: "192.0.2.10" }, address: "192.0.2.10",
      media: { state: "idle" }, ambientVideo: { enabled: true, state: "playing", playing: !state.automation.sleeping },
      backgroundVideos: { selectedId: state.activeFilm, canRollback: false },
      automation: automation(),
      voice: { enabled: true, state: "listening", detail: "Listening." },
      weather: weather(),
      notesVersion: 0,
      boardVersion: state.board.version,
    };
  }

  function boardNotice() {
    return state.layout.widgets.some((widget) => widget.type === "board" && widget.visible)
      ? null
      : "No Board widget is visible on the Mirror, so the board is not on the glass. " +
          "Someone can turn it on in the controls under Display. The items are kept.";
  }

  function flag(query, name) {
    const value = query.get(name);
    if (value === null) return undefined;
    if (value !== "true" && value !== "false") throw invalid(name, `${name} must be true or false`);
    return value === "true";
  }

  function board(method, path, query, body) {
    const board = state.board;
    const saved = (item) => ({ item, version: board.version });
    const withNotice = (reply) => (boardNotice() ? { ...reply, notice: boardNotice() } : reply);
    const filter = { kind: query.get("kind") ?? undefined, source: query.get("source") ?? undefined, done: flag(query, "done") };
    if (path === "/api/v1/board") {
      const showing = board.list().filter((item) => item.showing);
      const glass = boardNotice() ? { showsBoard: false, notice: boardNotice() } : { showsBoard: true };
      return [200, { version: board.version, now: now(), nowIso: new Date(now()).toISOString(), glass, counts: { total: board.items.length, showing: showing.length }, items: showing, guide: "/api/v1/board/guide" }];
    }
    if (path === "/api/v1/board/items") {
      if (method === "GET") {
        const limit = Number(query.get("limit") ?? 50);
        const offset = Number(query.get("offset") ?? 0);
        const all = board.list(filter);
        const end = Math.min(all.length, offset + limit);
        return [200, { items: all.slice(offset, end), total: all.length, offset, limit, nextOffset: end < all.length ? end : null, version: board.version, now: now(), nowIso: new Date(now()).toISOString() }];
      }
      if (method === "POST") return [201, withNotice(saved(board.create(body, "Mirror companion")))];
      if (method === "DELETE") {
        const everything = flag(query, "all") === true;
        if (filter.kind === undefined && filter.source === undefined && filter.done === undefined && !everything) {
          throw invalid(null, "Say which items to remove: ?source=NAME, ?kind=todo, ?done=true, a combination, or ?all=true for the whole board");
        }
        return [200, { deleted: board.deleteMatching(filter), version: board.version }];
      }
      throw new BoardRefusal(405, null, `${method} is not available here. This path takes GET, POST or DELETE`);
    }
    const id = decodeURIComponent(path.slice("/api/v1/board/items/".length));
    if (method === "PUT") {
      const result = board.put(id, body, "Mirror companion");
      return [result.created ? 201 : 200, withNotice({ ...saved(result.item), created: result.created })];
    }
    if (method === "PATCH") {
      const item = board.patch(id, body);
      if (!item) throw notFound(id);
      return [200, saved(item)];
    }
    if (method === "DELETE") {
      if (!board.delete(id)) throw notFound(id);
      return [200, { deleted: 1, version: board.version }];
    }
    if (method === "GET") {
      const item = board.find(id);
      if (!item) throw notFound(id);
      return [200, saved(item)];
    }
    throw new BoardRefusal(405, null, `${method} is not available here. This path takes GET, PUT, PATCH or DELETE`);
  }

  /** Whether the rows of a card keep the limits the real mirror sets: up to five, a short label, one line of text. */
  function rowsFit(details) {
    const line = (value, least, most) =>
      typeof value === "string" && value.length >= least && value.length <= most && !/[\r\n]/.test(value);
    return (
      Array.isArray(details) &&
      details.length <= 5 &&
      details.every((row) => row !== null && typeof row === "object" && line(row.label ?? "", 0, 14) && line(row.text, 1, 90))
    );
  }

  function say(body) {
    const kind = body.kind ?? "reply";
    const bad =
      typeof body.text !== "string" || body.text.length < 1 || body.text.length > 200 || /[\r\n]/.test(body.text) ||
      !["heard", "reply", "notice"].includes(kind) ||
      (body.seconds !== undefined && (!Number.isInteger(body.seconds) || body.seconds < 2 || body.seconds > 30));
    if (bad) return [400, { error: "A caption needs text of 1 to 200 characters on one line, a kind of heard, reply or notice, and 2 to 30 seconds." }];
    if (body.details !== undefined && !rowsFit(body.details)) {
      return [400, { error: "details takes up to 5 rows, each with a label of at most 14 characters and text of 1 to 90 characters on one line." }];
    }
    const shown = !state.automation.sleeping;
    const caption = { text: body.text, kind, seconds: body.seconds ?? null, shown };
    // Rows are recorded only for a caption that came with them, so that one without reads as before.
    if (body.details !== undefined) caption.details = body.details;
    state.said.push(caption);
    return [200, shown ? { shown: true } : { shown: false, reason: "sleeping" }];
  }

  /** Answers one request: [status, body] where a Buffer body is a picture. */
  function route(method, path, query, body) {
    const is = (wantedMethod, wantedPath) => method === wantedMethod && path === wantedPath;
    if (path === "/api/v1/board" || path.startsWith("/api/v1/board/")) return board(method, path, query, body);
    if (is("GET", "/api/v1/status")) return [200, status()];
    if (is("GET", "/api/v1/assistant")) {
      const characters = state.mascots ? { mascot: state.mascot, mascots: state.mascots } : {};
      const placed = state.place ? { place: { ...state.place }, places: state.places } : {};
      return [200, { enabled: true, state: "connected", ...characters, ...placed }];
    }
    if (is("PUT", "/api/v1/assistant")) {
      // As on the mirror: a character it does not know is refused with the ones it does.
      const known = ["none", ...(state.mascots ?? []).map((character) => character.id)];
      if (body.mascot !== undefined) {
        if (typeof body.mascot !== "string") return [400, { error: "mascot must be text" }];
        if (!known.includes(body.mascot)) return [400, { error: `mascot must be one of: ${known.join(", ")}` }];
      }
      if (body.place !== undefined) {
        // As on the mirror: either part may be left out, and one it does not know is refused with the ones it does.
        if (body.place === null || typeof body.place !== "object") return [400, { error: "place must be an object with height, side or both" }];
        const wanted = { ...state.place, ...body.place };
        if (!state.places.heights.includes(wanted.height)) return [400, { error: `place.height must be one of: ${state.places.heights.join(", ")}` }];
        if (!state.places.sides.includes(wanted.side)) return [400, { error: `place.side must be one of: ${state.places.sides.join(", ")}` }];
        state.place = { height: wanted.height, side: wanted.side };
      }
      if (body.mascot !== undefined) state.mascot = body.mascot;
      return [200, { enabled: true, state: "connected", mascot: state.mascot, mascots: state.mascots, place: state.place, places: state.places }];
    }
    if (is("POST", "/api/v1/assistant/say")) return say(body);
    if (is("GET", "/api/v1/screenshot")) {
      const width = query.get("width");
      if (width !== null && !(Number(width) >= 180 && Number(width) <= 1080)) return [400, { error: "width must be 180 to 1080" }];
      if (state.automation.sleeping) return [409, { error: "The display is dark." }];
      return [200, TINY_JPEG];
    }
    if (is("GET", "/api/v1/automation")) return [200, automation()];
    if (is("PUT", "/api/v1/automation")) {
      const valid =
        /^\d\d:\d\d$/.test(body.wakeTime ?? "") && /^\d\d:\d\d$/.test(body.sleepTime ?? "") &&
        Number.isInteger(body.wakeBrightness) && body.wakeBrightness >= 1 && body.wakeBrightness <= 255;
      if (!valid) return [400, { error: "Invalid automation settings" }];
      Object.assign(state.automation, {
        enabled: body.enabled === true, wakeTime: body.wakeTime, sleepTime: body.sleepTime, wakeBrightness: body.wakeBrightness,
        ambientEnabled: body.ambientEnabled === true, ambientMinimum: body.ambientMinimum ?? 20, ambientMaximum: body.ambientMaximum ?? 220,
        motionEnabled: body.motionEnabled ?? state.automation.motionEnabled,
        motionTimeoutSeconds: body.motionTimeoutSeconds ?? state.automation.motionTimeoutSeconds,
        motionSensitivity: body.motionSensitivity ?? state.automation.motionSensitivity,
        // As on the mirror: saving the settings ends a sleep or wake that was asked for.
        manualOverride: false,
      });
      return [200, automation()];
    }
    if (is("POST", "/api/v1/automation/sleep") || is("POST", "/api/v1/automation/wake")) {
      const sleeping = path.endsWith("/sleep");
      Object.assign(state.automation, { sleeping, sleepReason: sleeping ? "manual" : "none", manualOverride: true });
      return [200, automation()];
    }
    if (is("POST", "/api/v1/control/brightness")) {
      if (!Number.isInteger(body.value) || body.value < 1 || body.value > 255) return [400, { error: "Brightness must be between 1 and 255" }];
      state.brightness = body.value;
      return [200, { changed: true, value: body.value }];
    }
    if (is("GET", "/api/v1/dashboard/layout")) return [200, structuredClone(state.layout)];
    if (is("PUT", "/api/v1/dashboard/layout") || is("POST", "/api/v1/dashboard/layout/validate")) {
      let normalized;
      try {
        normalized = normalizeLayout(body);
      } catch {
        // The real mirror says no more than this about a layout it refuses.
        return [400, { error: "Invalid JSON request" }];
      }
      if (method === "PUT") state.layout = normalized;
      return [200, structuredClone(normalized)];
    }
    if (is("GET", "/api/v1/background-videos")) return [200, catalog()];
    if (method === "POST" && /^\/api\/v1\/background-videos\/[^/]+\/activate$/.test(path)) {
      const id = path.split("/")[4];
      if (!state.films.some((film) => film.id === id)) return [404, { error: "Background video not found" }];
      state.activeFilm = id;
      state.layout.background.mode = "video";
      return [200, catalog()];
    }
    if (is("GET", "/api/v1/photos")) return [200, { photos: state.photos }];
    if (is("GET", "/api/v1/weather")) return [200, weather()];
    return [404, { error: "Endpoint not found" }];
  }

  const server = http.createServer((request, response) => {
    if (state.dropNext > 0) {
      state.dropNext -= 1;
      request.socket.destroy();
      return;
    }
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      const url = new URL(request.url, "http://mirror");
      const text = Buffer.concat(chunks).toString("utf8");
      let body = {};
      let status;
      let answer;
      try {
        if (request.method === "POST" && url.pathname === "/api/v1/pair") {
          // Pairing needs no token. The fake mirror always "shows" the code 123456.
          const right = JSON.parse(text).code === PAIRING_CODE;
          [status, answer] = right
            ? [200, { token, clientId: "fake-client", clientName: JSON.parse(text).name ?? "Device" }]
            : [401, { error: "That code is not right. Check the code and try again.", reason: "wrong-code" }];
        } else if (request.headers.authorization !== `Bearer ${token}`) {
          [status, answer] = [401, { error: "Authentication required" }];
        } else {
          if (text.trim()) body = JSON.parse(text);
          const seen = { method: request.method, path: url.pathname + url.search, body: text.trim() ? body : null };
          state.requests.push(seen);
          onRequest(seen);
          if (state.failing.has(url.pathname)) {
            [status, answer] = [state.failing.get(url.pathname), { error: "Mirror Home could not complete the request" }];
          } else {
            [status, answer] = route(request.method, url.pathname, url.searchParams, body);
          }
        }
      } catch (error) {
        if (error instanceof BoardRefusal) [status, answer] = [error.status, error.body()];
        else if (error instanceof SyntaxError) [status, answer] = [400, { error: "Invalid JSON request" }];
        else [status, answer] = [500, { error: "Mirror Home could not complete the request" }];
      }
      const picture = Buffer.isBuffer(answer);
      const payload = picture ? answer : Buffer.from(JSON.stringify(answer), "utf8");
      response.writeHead(status, {
        "Content-Type": picture ? "image/jpeg" : "application/json; charset=utf-8",
        "Content-Length": payload.length,
        "Cache-Control": "no-store",
      });
      response.end(payload);
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });

  return {
    host: "127.0.0.1",
    port: server.address().port,
    token,
    state,
    requests: () => state.requests,
    /** The requests that changed something, for tests that check nothing was written. */
    writes: () => state.requests.filter((request) => request.method !== "GET" && !request.path.endsWith("/validate")),
    widget: (id) => state.layout.widgets.find((candidate) => candidate.id === id),
    close: () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(resolve);
      }),
  };
}
