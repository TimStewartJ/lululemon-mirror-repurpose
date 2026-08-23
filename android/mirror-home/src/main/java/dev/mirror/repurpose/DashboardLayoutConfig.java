package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.HashMap;
import java.util.Map;

public final class DashboardLayoutConfig {
    private static final int GRID_SIZE = 1000;
    private static final int MIN_WIDGET_SIZE = 24;
    private static final String[] WIDGET_TYPES = {
            "clock",
            "date",
            "name",
            "wifi",
            "media",
            "schedule",
            "brightness",
            "fcast",
            "ble",
            "uptime",
            "pairing",
            "note"
    };

    private final JSONObject value;

    private DashboardLayoutConfig(JSONObject value) {
        this.value = value;
    }

    public static DashboardLayoutConfig defaults() {
        try {
            JSONObject root = new JSONObject();
            root.put("version", 1);
            root.put(
                    "background",
                    new JSONObject()
                            .put("mode", "gradient")
                            .put("primary", "#020607")
                            .put("secondary", "#10242b")
                            .put("photo", "")
                            .put("dim", 58));
            root.put("textColor", "#e7eff1");
            root.put("accentColor", "#8ab8c2");

            JSONArray widgets = new JSONArray();
            widgets.put(widget("clock", 55, 58, 455, 132, true, 88, "start", ""));
            widgets.put(widget("date", 60, 188, 390, 48, true, 64, "start", ""));
            widgets.put(widget("name", 745, 65, 200, 42, true, 58, "end", ""));
            widgets.put(widget("wifi", 55, 905, 170, 36, true, 54, "start", ""));
            widgets.put(widget("media", 240, 905, 170, 36, true, 54, "start", ""));
            widgets.put(widget("schedule", 425, 905, 200, 36, true, 54, "start", ""));
            widgets.put(widget("brightness", 640, 905, 145, 36, true, 54, "start", ""));
            widgets.put(widget("fcast", 800, 905, 145, 36, true, 54, "end", ""));
            widgets.put(widget("ble", 55, 950, 180, 30, false, 46, "start", ""));
            widgets.put(widget("uptime", 250, 950, 180, 30, false, 46, "start", ""));
            widgets.put(widget("pairing", 690, 950, 255, 30, true, 42, "end", ""));
            widgets.put(widget(
                    "note",
                    55,
                    360,
                    480,
                    85,
                    false,
                    52,
                    "start",
                    "Make space for what matters."));
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
        if (source.optInt("version", 1) != 1) {
            throw new JSONException("Unsupported dashboard layout version");
        }
        DashboardLayoutConfig defaults = defaults();
        JSONObject defaultRoot = defaults.toJson();

        JSONObject result = new JSONObject();
        result.put("version", 1);
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

        Map<String, JSONObject> supplied = new HashMap<>();
        JSONArray sourceWidgets = source.optJSONArray("widgets");
        if (sourceWidgets != null) {
            for (int index = 0; index < sourceWidgets.length(); index++) {
                JSONObject widget = sourceWidgets.optJSONObject(index);
                if (widget == null) {
                    throw new JSONException("Dashboard widgets must be objects");
                }
                String id = widget.optString("id", "");
                if (!isAllowedWidget(id) || supplied.put(id, widget) != null) {
                    throw new JSONException("Unknown or duplicate dashboard widget: " + id);
                }
            }
        }

        JSONArray normalized = new JSONArray();
        JSONArray defaultWidgets = defaultRoot.getJSONArray("widgets");
        for (int index = 0; index < defaultWidgets.length(); index++) {
            JSONObject fallback = defaultWidgets.getJSONObject(index);
            String id = fallback.getString("id");
            normalized.put(normalizeWidget(supplied.get(id), fallback));
        }
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

    private static JSONObject normalizeBackground(JSONObject source, JSONObject fallback)
            throws JSONException {
        JSONObject value = source == null ? fallback : source;
        String mode = value.optString("mode", fallback.getString("mode"));
        if (!"solid".equals(mode) && !"gradient".equals(mode) && !"photo".equals(mode)) {
            throw new JSONException("Unknown dashboard background mode");
        }
        String photo = value.optString("photo", "");
        if (photo.length() > 180
                || photo.contains("/")
                || photo.contains("\\")
                || photo.contains("..")
                || containsControlCharacter(photo)) {
            throw new JSONException("Invalid dashboard background photo");
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
                .put("dim", dim);
    }

    private static JSONObject normalizeWidget(JSONObject source, JSONObject fallback)
            throws JSONException {
        JSONObject value = source == null ? fallback : source;
        String id = fallback.getString("id");
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
        if (text.length() > 120 || containsControlCharacter(text)) {
            throw new JSONException("Dashboard note is invalid");
        }
        return new JSONObject()
                .put("id", id)
                .put("type", id)
                .put("x", x)
                .put("y", y)
                .put("w", width)
                .put("h", height)
                .put("visible", value.optBoolean("visible", fallback.getBoolean("visible")))
                .put("opacity", opacity)
                .put("align", align)
                .put("text", text);
    }

    private static JSONObject widget(
            String id,
            int x,
            int y,
            int width,
            int height,
            boolean visible,
            int opacity,
            String align,
            String text) throws JSONException {
        return new JSONObject()
                .put("id", id)
                .put("type", id)
                .put("x", x)
                .put("y", y)
                .put("w", width)
                .put("h", height)
                .put("visible", visible)
                .put("opacity", opacity)
                .put("align", align)
                .put("text", text);
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

    private static boolean isAllowedWidget(String id) {
        for (String candidate : WIDGET_TYPES) {
            if (candidate.equals(id)) {
                return true;
            }
        }
        return false;
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
