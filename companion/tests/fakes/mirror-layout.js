/**
 * The rules of Mirror Home's dashboard layout, ported from
 * DashboardLayoutConfig.java so that the fake mirror accepts and refuses
 * what the real one does.
 */

const GRID = 1000;
const MIN_SIZE = 24;
const MAX_WIDGETS = 40;
const TYPES = [
  "clock", "date", "name", "wifi", "media", "schedule", "brightness", "fcast",
  "uptime", "motion", "weather", "forecast", "pairing", "note", "photo", "board",
];

function widget(id, x, y, w, h, visible, opacity, align, text, layer, extra = {}) {
  return { id, type: id, x, y, w, h, visible, opacity, align, text, locked: false, layer, ...extra };
}

/** DashboardLayoutConfig.defaults(). */
export function defaultLayout() {
  return {
    version: 2,
    background: { mode: "solid", primary: "#000000", secondary: "#000000", photo: "", fit: "cover", dim: 0 },
    textColor: "#f5f2ec",
    accentColor: "#c2ced3",
    widgets: [
      widget("clock", 50, 52, 560, 150, true, 100, "start", "", 10),
      widget("date", 54, 208, 540, 46, true, 70, "start", "", 11),
      widget("name", 700, 20, 250, 34, false, 58, "end", "", 12),
      widget("weather", 600, 58, 350, 110, true, 86, "end", "", 13),
      widget("forecast", 560, 185, 390, 95, true, 70, "end", "", 14),
      widget("wifi", 50, 905, 170, 40, false, 56, "start", "", 20),
      widget("media", 240, 905, 200, 40, false, 56, "start", "", 21),
      widget("schedule", 460, 905, 200, 40, false, 56, "start", "", 22),
      widget("brightness", 680, 905, 120, 40, false, 56, "start", "", 23),
      widget("fcast", 820, 905, 130, 40, false, 56, "end", "", 24),
      widget("uptime", 250, 950, 180, 32, false, 48, "start", "", 26),
      widget("motion", 450, 950, 200, 32, false, 48, "start", "", 27),
      widget("pairing", 690, 950, 260, 32, false, 48, "end", "", 28),
      widget("note", 50, 330, 480, 90, false, 56, "start", "Make space for what matters.", 15, {
        source: "latest", note: "", size: "auto", weight: "light",
      }),
      widget("photo", 50, 680, 440, 190, false, 82, "center", "", 16, { photo: "", fit: "cover" }),
      widget("board", 50, 440, 520, 230, false, 90, "start", "", 17, { size: "medium", show: "all" }),
    ],
  };
}

function fail(message) {
  throw new Error(message);
}

/** org.json's optInt: numbers are truncated, numeric text is read, anything else gives the fallback. */
function optInt(value, fallback) {
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Math.trunc(Number(value));
  return fallback;
}

function optString(value, fallback) {
  return value === undefined || value === null ? fallback : String(value);
}

function optBoolean(value, fallback) {
  return typeof value === "boolean" ? value : fallback;
}

function bounded(value, minimum, maximum, field) {
  if (value < minimum || value > maximum) fail(`Dashboard ${field} is out of range`);
  return value;
}

function color(value) {
  if (!/^#[0-9a-fA-F]{6}$/.test(value)) fail("Dashboard colors must use #RRGGBB");
  return value.toLowerCase();
}

function hasControl(text) {
  for (const character of text) {
    const code = character.codePointAt(0);
    if ((code < 0x20 || (code >= 0x7f && code <= 0x9f)) && character !== "\n") return true;
  }
  return false;
}

function photoName(photo) {
  if (photo.length > 180 || /[\\/]/.test(photo) || photo.includes("..") || hasControl(photo)) fail("Invalid dashboard photo");
  return photo;
}

function normalizeWidget(source, fallback, id, type) {
  const value = source ?? fallback;
  const x = bounded(optInt(value.x, fallback.x), 0, GRID, "x");
  const y = bounded(optInt(value.y, fallback.y), 0, GRID, "y");
  const w = bounded(optInt(value.w, fallback.w), MIN_SIZE, GRID, "width");
  const h = bounded(optInt(value.h, fallback.h), MIN_SIZE, GRID, "height");
  if (x + w > GRID || y + h > GRID) fail(`Dashboard widget exceeds the canvas: ${id}`);
  const opacity = bounded(optInt(value.opacity, fallback.opacity), 10, 100, "opacity");
  const align = optString(value.align, fallback.align);
  if (!["start", "center", "end"].includes(align)) fail("Invalid dashboard widget alignment");
  const text = optString(value.text, fallback.text ?? "");
  if (text.length > (type === "note" ? 1000 : 120) || hasControl(text)) fail("Dashboard note is invalid");
  const layer = bounded(optInt(value.layer, fallback.layer ?? 10), 0, 99, "layer");
  const normalized = {
    id, type, x, y, w, h,
    visible: optBoolean(value.visible, fallback.visible),
    opacity, align, text,
    locked: optBoolean(value.locked, fallback.locked ?? false),
    layer,
  };
  if (type === "photo") {
    const fit = optString(value.fit, fallback.fit ?? "cover");
    if (!["cover", "contain"].includes(fit)) fail("Dashboard photo fit must be cover or contain");
    normalized.photo = photoName(optString(value.photo, ""));
    normalized.fit = fit;
  }
  if (type === "note") {
    const defaultSource = source ? "text" : fallback.source ?? "text";
    const noteSource = optString(value.source, defaultSource);
    const noteId = optString(value.note, fallback.note ?? "");
    const size = optString(value.size, fallback.size ?? "auto");
    const weight = optString(value.weight, fallback.weight ?? "light");
    if (!["text", "latest", "rotate", "list", "pinned"].includes(noteSource)) fail(`Unknown dashboard note source: ${noteSource}`);
    if (noteId !== "" && !/^[A-Za-z0-9_-]{1,64}$/.test(noteId)) fail("Invalid dashboard note reference");
    if (!["auto", "small", "medium", "large"].includes(size)) fail("Dashboard note size must be auto, small, medium, or large");
    if (!["thin", "light", "regular", "medium"].includes(weight)) fail("Dashboard note weight must be thin, light, regular, or medium");
    Object.assign(normalized, { source: noteSource, note: noteId, size, weight });
  }
  if (type === "board") {
    const size = optString(value.size, fallback.size ?? "medium");
    const show = optString(value.show, fallback.show ?? "all");
    if (!["small", "medium", "large"].includes(size)) fail("Dashboard board size must be small, medium, or large");
    if (!["all", "note", "todo", "reminder"].includes(show)) fail("Dashboard board show must be all, note, todo, or reminder");
    if (text.includes("\n")) fail("Dashboard board heading must be one line");
    Object.assign(normalized, { size, show });
  }
  return normalized;
}

/**
 * DashboardLayoutConfig.parse for a version 2 layout: returns the layout as
 * the mirror would store it, or throws.
 */
export function normalizeLayout(source) {
  if (source === null || typeof source !== "object" || Array.isArray(source)) fail("Not an object");
  if (optInt(source.version, 1) !== 2) fail("Unsupported dashboard layout version");
  const defaults = defaultLayout();
  const background = source.background && typeof source.background === "object" ? source.background : defaults.background;
  const mode = optString(background.mode, defaults.background.mode);
  if (!["solid", "gradient", "photo", "video"].includes(mode)) fail("Unknown dashboard background mode");
  const fit = optString(background.fit, "cover");
  if (!["cover", "contain"].includes(fit)) fail("Dashboard background fit must be cover or contain");
  const dim = optInt(background.dim, 0);
  if (dim < 0 || dim > 90) fail("Dashboard background dim must be 0-90");
  const result = {
    version: 2,
    background: {
      mode,
      primary: color(optString(background.primary, defaults.background.primary)),
      secondary: color(optString(background.secondary, defaults.background.secondary)),
      photo: photoName(optString(background.photo, "")),
      fit,
      dim,
    },
    textColor: color(optString(source.textColor, defaults.textColor)),
    accentColor: color(optString(source.accentColor, defaults.accentColor)),
    widgets: [],
  };
  const widgets = source.widgets;
  if (!Array.isArray(widgets) || widgets.length < 1 || widgets.length > MAX_WIDGETS) {
    fail(`Dashboard must contain 1-${MAX_WIDGETS} widgets`);
  }
  const byType = new Map(defaults.widgets.map((fallback) => [fallback.type, fallback]));
  const canonical = new Map(defaults.widgets.map((fallback) => [fallback.id, fallback]));
  const ids = new Set();
  for (const item of widgets) {
    if (item === null || typeof item !== "object" || Array.isArray(item)) fail("Dashboard widgets must be objects");
    const id = optString(item.id, "");
    const type = optString(item.type, "");
    if (type === "ble") continue;
    if (!/^[a-z][a-z0-9-]{0,39}$/.test(id) || ids.has(id)) fail(`Invalid or duplicate dashboard widget id: ${id}`);
    ids.add(id);
    if (!TYPES.includes(type)) fail(`Unknown dashboard widget type: ${type}`);
    if (canonical.has(id) && canonical.get(id).type !== type) fail(`Canonical dashboard widget type cannot change: ${id}`);
    result.widgets.push(normalizeWidget(item, byType.get(type), id, type));
  }
  for (const fallback of defaults.widgets) {
    if (!ids.has(fallback.id) && result.widgets.length < MAX_WIDGETS) {
      result.widgets.push({ ...normalizeWidget(null, fallback, fallback.id, fallback.type), visible: false });
    }
  }
  return result;
}
