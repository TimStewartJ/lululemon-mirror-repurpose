package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.HashMap;
import java.util.Map;

public final class DashboardLayoutConfig {
    private static final int CURRENT_VERSION = 2;
    private static final int GRID_SIZE = 1000;
    private static final int MIN_WIDGET_SIZE = 24;
    private static final int MAX_WIDGETS = 40;
    private static final String[] WIDGET_TYPES = {
            "clock",
            "date",
            "name",
            "wifi",
            "media",
            "schedule",
            "brightness",
            "fcast",
            "uptime",
            "motion",
            "weather",
            "forecast",
            "pairing",
            "note",
            "photo"
    };
    private static final String DEFAULT_PHOTO_FIT = "cover";
    private static final String DEFAULT_BACKGROUND_FIT = "cover";
    /* Other widgets carry an unused text field; only notes hold real prose. */
    private static final int MAX_WIDGET_TEXT = 120;
    /* Where a note widget gets its words: its own text, or the NoteBook. */
    private static final String[] NOTE_SOURCES = {"text", "latest", "rotate", "list", "pinned"};
    private static final String[] NOTE_SIZES = {"auto", "small", "medium", "large"};
    private static final String[] NOTE_WEIGHTS = {"thin", "light", "regular", "medium"};
    private static final String DEFAULT_NOTE_SIZE = "auto";
    private static final String DEFAULT_NOTE_WEIGHT = "light";
    /* Widget types from removed features; dropped from saved layouts instead of
       invalidating the whole layout and resetting it to defaults. */
    private static final String[] RETIRED_WIDGET_TYPES = {
            "ble"
    };

    private final JSONObject value;

    private DashboardLayoutConfig(JSONObject value) {
        this.value = value;
    }

    public static DashboardLayoutConfig defaults() {
        try {
            JSONObject root = new JSONObject();
            root.put("version", CURRENT_VERSION);
            root.put(
                    "background",
                    new JSONObject()
                            .put("mode", "solid")
                            .put("primary", "#000000")
                            .put("secondary", "#000000")
                            .put("photo", "")
                            .put("fit", DEFAULT_BACKGROUND_FIT)
                            .put("dim", 0));
            root.put("textColor", "#f5f2ec");
            root.put("accentColor", "#c2ced3");

            JSONArray widgets = new JSONArray();
            widgets.put(widget("clock", "clock", 50, 52, 560, 150, true, 100, "start", "", 10));
            widgets.put(widget("date", "date", 54, 208, 540, 46, true, 70, "start", "", 11));
            widgets.put(widget("name", "name", 700, 20, 250, 34, false, 58, "end", "", 12));
            widgets.put(widget("weather", "weather", 600, 58, 350, 110, true, 86, "end", "", 13));
            widgets.put(widget("forecast", "forecast", 560, 185, 390, 95, true, 70, "end", "", 14));
            widgets.put(widget("wifi", "wifi", 50, 905, 170, 40, false, 56, "start", "", 20));
            widgets.put(widget("media", "media", 240, 905, 200, 40, false, 56, "start", "", 21));
            widgets.put(widget("schedule", "schedule", 460, 905, 200, 40, false, 56, "start", "", 22));
            widgets.put(widget("brightness", "brightness", 680, 905, 120, 40, false, 56, "start", "", 23));
            widgets.put(widget("fcast", "fcast", 820, 905, 130, 40, false, 56, "end", "", 24));
            widgets.put(widget("uptime", "uptime", 250, 950, 180, 32, false, 48, "start", "", 26));
            widgets.put(widget("motion", "motion", 450, 950, 200, 32, false, 48, "start", "", 27));
            widgets.put(widget("pairing", "pairing", 690, 950, 260, 32, false, 48, "end", "", 28));
            widgets.put(widget(
                    "note",
                    "note",
                    50,
                    330,
                    480,
                    90,
                    false,
                    56,
                    "start",
                    "Make space for what matters.",
                    15)
                    .put("source", "latest")
                    .put("note", "")
                    .put("size", DEFAULT_NOTE_SIZE)
                    .put("weight", DEFAULT_NOTE_WEIGHT));
            widgets.put(widget("photo", "photo", 50, 680, 440, 190, false, 82, "center", "", 16)
                    .put("photo", "")
                    .put("fit", DEFAULT_PHOTO_FIT));
            root.put("widgets", widgets);
            return new DashboardLayoutConfig(root);
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to create default dashboard layout", impossible);
        }
    }

    public static DashboardLayoutConfig parse(String serialized) throws JSONException {
        return parse(new JSONObject(serialized));
    }

    public static DashboardLayoutConfig parse(JSONObject source) throws JSONException {
        int version = source.optInt("version", 1);
        if (version != 1 && version != CURRENT_VERSION) {
            throw new JSONException("Unsupported dashboard layout version");
        }
        DashboardLayoutConfig defaults = defaults();
        JSONObject defaultRoot = defaults.toJson();

        JSONObject result = new JSONObject();
        result.put("version", CURRENT_VERSION);
        result.put(
                "background",
                normalizeBackground(
                        source.optJSONObject("background"),
                        defaultRoot.getJSONObject("background")));
        result.put(
                "textColor",
                normalizeColor(source.optString("textColor", defaultRoot.getString("textColor"))));
        result.put(
                "accentColor",
                normalizeColor(source.optString(
                        "accentColor",
                        defaultRoot.getString("accentColor"))));

        JSONArray sourceWidgets = source.optJSONArray("widgets");
        JSONArray normalized = version == 1
                ? migrateVersionOne(sourceWidgets, defaultRoot.getJSONArray("widgets"))
                : normalizeVersionTwo(sourceWidgets, defaultRoot.getJSONArray("widgets"));
        result.put("widgets", normalized);
        return new DashboardLayoutConfig(result);
    }

    public JSONObject toJson() {
        try {
            return new JSONObject(value.toString());
        } catch (JSONException impossible) {
            throw new IllegalStateException("Stored dashboard layout is invalid", impossible);
        }
    }

    public String serialize() {
        return value.toString();
    }

    public String backgroundMode() {
        return background().optString("mode", "solid");
    }

    public String backgroundFit() {
        return background().optString("fit", DEFAULT_BACKGROUND_FIT);
    }

    /** Whether the layout shows at least one widget of this type. */
    public boolean showsWidget(String type) {
        JSONArray widgets = value.optJSONArray("widgets");
        for (int index = 0; widgets != null && index < widgets.length(); index++) {
            JSONObject widget = widgets.optJSONObject(index);
            if (widget != null
                    && type.equals(widget.optString("type"))
                    && widget.optBoolean("visible", false)) {
                return true;
            }
        }
        return false;
    }

    public DashboardLayoutConfig withBackgroundMode(String mode) {
        JSONObject copy = toJson();
        try {
            copy.getJSONObject("background").put("mode", mode);
            return parse(copy);
        } catch (JSONException error) {
            throw new IllegalArgumentException("Invalid dashboard background mode", error);
        }
    }

    private static JSONObject normalizeBackground(JSONObject source, JSONObject fallback)
            throws JSONException {
        JSONObject value = source == null ? fallback : source;
        String mode = value.optString("mode", fallback.getString("mode"));
        if (!"solid".equals(mode)
                && !"gradient".equals(mode)
                && !"photo".equals(mode)
                && !"video".equals(mode)) {
            throw new JSONException("Unknown dashboard background mode");
        }
        String photo = normalizePhotoName(value.optString("photo", ""));
        String fit = value.optString("fit", fallback.optString("fit", DEFAULT_BACKGROUND_FIT));
        if (!"cover".equals(fit) && !"contain".equals(fit)) {
            throw new JSONException("Dashboard background fit must be cover or contain");
        }
        int dim = value.optInt("dim", fallback.getInt("dim"));
        if (dim < 0 || dim > 90) {
            throw new JSONException("Dashboard background dim must be 0-90");
        }
        return new JSONObject()
                .put("mode", mode)
                .put("primary", normalizeColor(value.optString(
                        "primary",
                        fallback.getString("primary"))))
                .put("secondary", normalizeColor(value.optString(
                        "secondary",
                        fallback.getString("secondary"))))
                .put("photo", photo)
                .put("fit", fit)
                .put("dim", dim);
    }

    private JSONObject background() {
        JSONObject background = value.optJSONObject("background");
        if (background == null) {
            throw new IllegalStateException("Dashboard background is missing");
        }
        return background;
    }

    private static JSONArray migrateVersionOne(
            JSONArray sourceWidgets,
            JSONArray defaultWidgets) throws JSONException {
        Map<String, JSONObject> supplied = new HashMap<>();
        if (sourceWidgets != null) {
            if (sourceWidgets.length() > MAX_WIDGETS) {
                throw new JSONException("Dashboard has too many widgets");
            }
            for (int index = 0; index < sourceWidgets.length(); index++) {
                JSONObject widget = sourceWidgets.optJSONObject(index);
                if (widget == null) {
                    throw new JSONException("Dashboard widgets must be objects");
                }
                String id = widget.optString("id", "");
                if (isRetiredWidgetType(id)) {
                    continue;
                }
                if (!isAllowedWidgetType(id) || supplied.put(id, widget) != null) {
                    throw new JSONException("Unknown or duplicate dashboard widget: " + id);
                }
            }
        }

        JSONArray normalized = new JSONArray();
        for (int index = 0; index < defaultWidgets.length(); index++) {
            JSONObject fallback = defaultWidgets.getJSONObject(index);
            String id = fallback.getString("id");
            JSONObject suppliedWidget = supplied.get(id);
            JSONObject migrated = normalizeWidget(
                    suppliedWidget,
                    fallback,
                    id,
                    fallback.getString("type"));
            if (suppliedWidget == null
                    && ("weather".equals(id) || "forecast".equals(id))) {
                migrated.put("visible", false);
            }
            normalized.put(migrated);
        }
        return normalized;
    }

    private static JSONArray normalizeVersionTwo(
            JSONArray sourceWidgets,
            JSONArray defaultWidgets) throws JSONException {
        if (sourceWidgets == null
                || sourceWidgets.length() < 1
                || sourceWidgets.length() > MAX_WIDGETS) {
            throw new JSONException("Dashboard must contain 1-" + MAX_WIDGETS + " widgets");
        }
        Map<String, JSONObject> fallbacks = new HashMap<>();
        Map<String, JSONObject> canonical = new HashMap<>();
        for (int index = 0; index < defaultWidgets.length(); index++) {
            JSONObject fallback = defaultWidgets.getJSONObject(index);
            fallbacks.put(fallback.getString("type"), fallback);
            canonical.put(fallback.getString("id"), fallback);
        }
        Map<String, Boolean> ids = new HashMap<>();
        JSONArray normalized = new JSONArray();
        for (int index = 0; index < sourceWidgets.length(); index++) {
            JSONObject source = sourceWidgets.optJSONObject(index);
            if (source == null) {
                throw new JSONException("Dashboard widgets must be objects");
            }
            String id = source.optString("id", "");
            String type = source.optString("type", "");
            if (isRetiredWidgetType(type)) {
                continue;
            }
            if (!validWidgetId(id) || ids.put(id, true) != null) {
                throw new JSONException("Invalid or duplicate dashboard widget id: " + id);
            }
            JSONObject fallback = fallbacks.get(type);
            if (fallback == null || !isAllowedWidgetType(type)) {
                throw new JSONException("Unknown dashboard widget type: " + type);
            }
            JSONObject canonicalWidget = canonical.get(id);
            if (canonicalWidget != null
                    && !canonicalWidget.getString("type").equals(type)) {
                throw new JSONException("Canonical dashboard widget type cannot change: " + id);
            }
            normalized.put(normalizeWidget(source, fallback, id, type));
        }
        for (int index = 0; index < defaultWidgets.length(); index++) {
            JSONObject fallback = defaultWidgets.getJSONObject(index);
            String id = fallback.getString("id");
            if (!ids.containsKey(id)) {
                JSONObject restored = normalizeWidget(
                        null,
                        fallback,
                        id,
                        fallback.getString("type"));
                restored.put("visible", false);
                normalized.put(restored);
            }
        }
        if (normalized.length() > MAX_WIDGETS) {
            throw new JSONException("Dashboard has too many widgets after restoring defaults");
        }
        return normalized;
    }

    private static JSONObject normalizeWidget(
            JSONObject source,
            JSONObject fallback,
            String id,
            String type)
            throws JSONException {
        JSONObject value = source == null ? fallback : source;
        int x = bounded(value.optInt("x", fallback.getInt("x")), 0, GRID_SIZE, "x");
        int y = bounded(value.optInt("y", fallback.getInt("y")), 0, GRID_SIZE, "y");
        int width = bounded(
                value.optInt("w", fallback.getInt("w")),
                MIN_WIDGET_SIZE,
                GRID_SIZE,
                "width");
        int height = bounded(
                value.optInt("h", fallback.getInt("h")),
                MIN_WIDGET_SIZE,
                GRID_SIZE,
                "height");
        if (x + width > GRID_SIZE || y + height > GRID_SIZE) {
            throw new JSONException("Dashboard widget exceeds the canvas: " + id);
        }
        int opacity = bounded(
                value.optInt("opacity", fallback.getInt("opacity")),
                10,
                100,
                "opacity");
        String align = value.optString("align", fallback.getString("align"));
        if (!"start".equals(align) && !"center".equals(align) && !"end".equals(align)) {
            throw new JSONException("Invalid dashboard widget alignment");
        }
        String text = value.optString("text", fallback.optString("text", ""));
        int maxText = "note".equals(type) ? NoteBook.MAX_TEXT_LENGTH : MAX_WIDGET_TEXT;
        if (text.length() > maxText || containsControlCharacter(text)) {
            throw new JSONException("Dashboard note is invalid");
        }
        int layer = bounded(
                value.optInt("layer", fallback.optInt("layer", 10)),
                0,
                99,
                "layer");
        JSONObject normalized = new JSONObject()
                .put("id", id)
                .put("type", type)
                .put("x", x)
                .put("y", y)
                .put("w", width)
                .put("h", height)
                .put("visible", value.optBoolean("visible", fallback.getBoolean("visible")))
                .put("opacity", opacity)
                .put("align", align)
                .put("text", text)
                .put("locked", value.optBoolean("locked", fallback.optBoolean("locked", false)))
                .put("layer", layer);
        if ("photo".equals(type)) {
            /* An empty photo name means "rotate through the library". */
            String fit = value.optString("fit", fallback.optString("fit", DEFAULT_PHOTO_FIT));
            if (!"cover".equals(fit) && !"contain".equals(fit)) {
                throw new JSONException("Dashboard photo fit must be cover or contain");
            }
            normalized.put("photo", normalizePhotoName(value.optString("photo", "")));
            normalized.put("fit", fit);
        }
        if ("note".equals(type)) {
            /* Layouts saved before notes had sources keep showing their own text;
               only a restored default inherits the default source. */
            String defaultSource = source == null ? fallback.optString("source", "text") : "text";
            String noteSource = value.optString("source", defaultSource);
            String noteId = value.optString("note", fallback.optString("note", ""));
            String size = value.optString("size", fallback.optString("size", DEFAULT_NOTE_SIZE));
            String weight = value.optString(
                    "weight",
                    fallback.optString("weight", DEFAULT_NOTE_WEIGHT));
            if (!isOneOf(noteSource, NOTE_SOURCES)) {
                throw new JSONException("Unknown dashboard note source: " + noteSource);
            }
            if (!noteId.isEmpty() && !NoteBook.validId(noteId)) {
                throw new JSONException("Invalid dashboard note reference");
            }
            if (!isOneOf(size, NOTE_SIZES)) {
                throw new JSONException("Dashboard note size must be auto, small, medium, or large");
            }
            if (!isOneOf(weight, NOTE_WEIGHTS)) {
                throw new JSONException("Dashboard note weight must be thin, light, regular, or medium");
            }
            normalized.put("source", noteSource);
            normalized.put("note", noteId);
            normalized.put("size", size);
            normalized.put("weight", weight);
        }
        return normalized;
    }

    private static String normalizePhotoName(String photo) throws JSONException {
        if (photo == null
                || photo.length() > 180
                || photo.contains("/")
                || photo.contains("\\")
                || photo.contains("..")
                || containsControlCharacter(photo)) {
            throw new JSONException("Invalid dashboard photo");
        }
        return photo;
    }

    private static JSONObject widget(
            String id,
            String type,
            int x,
            int y,
            int width,
            int height,
            boolean visible,
            int opacity,
            String align,
            String text,
            int layer) throws JSONException {
        return new JSONObject()
                .put("id", id)
                .put("type", type)
                .put("x", x)
                .put("y", y)
                .put("w", width)
                .put("h", height)
                .put("visible", visible)
                .put("opacity", opacity)
                .put("align", align)
                .put("text", text)
                .put("locked", false)
                .put("layer", layer);
    }

    private static int bounded(int value, int minimum, int maximum, String field)
            throws JSONException {
        if (value < minimum || value > maximum) {
            throw new JSONException("Dashboard " + field + " is out of range");
        }
        return value;
    }

    private static String normalizeColor(String value) throws JSONException {
        if (value == null || !value.matches("#[0-9a-fA-F]{6}")) {
            throw new JSONException("Dashboard colors must use #RRGGBB");
        }
        return value.toLowerCase(java.util.Locale.US);
    }

    private static boolean isAllowedWidgetType(String id) {
        for (String candidate : WIDGET_TYPES) {
            if (candidate.equals(id)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isRetiredWidgetType(String type) {
        for (String candidate : RETIRED_WIDGET_TYPES) {
            if (candidate.equals(type)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isOneOf(String value, String[] options) {
        for (String option : options) {
            if (option.equals(value)) {
                return true;
            }
        }
        return false;
    }

    private static boolean validWidgetId(String id) {
        return id != null && id.matches("[a-z][a-z0-9-]{0,39}");
    }

    private static boolean containsControlCharacter(String value) {
        for (int index = 0; index < value.length(); index++) {
            if (Character.isISOControl(value.charAt(index))
                    && value.charAt(index) != '\n') {
                return true;
            }
        }
        return false;
    }
}
