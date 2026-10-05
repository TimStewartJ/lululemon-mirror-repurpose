package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * What is on the glass for a while and then leaves by itself: a countdown,
 * a few large words, a list, a small chart, a drawing. A moment is put
 * there by whoever answers a person, lies over the widgets where it was
 * found room, and is gone when its time is up. Nothing of it is kept: a
 * Mirror Home that restarts has none.
 *
 * <p>Whoever sends a moment says how large it is and, if they care, at
 * which height and side it goes. Where exactly is for the glass, which
 * alone knows where something is drawn already.
 *
 * <p>A moment is described, not programmed. The glass draws the five kinds
 * it knows from the fields below, so that nothing a client sends can run
 * there.
 */
final class Moments {
    /** How many moments the glass holds at once; one more takes the place of the oldest. */
    static final int LIMIT = 6;
    static final int LEAST_SECONDS = 5;
    static final int MOST_SECONDS = 6 * 60 * 60;
    static final int USUAL_SECONDS = 45;
    /** How long a countdown that has run out stays, unless told otherwise. */
    static final int LINGER_SECONDS = 20;
    static final int MOST_ROWS = 8;
    static final int MOST_VALUES = 12;
    static final int MOST_SHAPES = 40;

    private static final List<String> KINDS = Arrays.asList("text", "countdown", "list", "chart", "drawing");
    private static final List<String> MOTIONS = Arrays.asList("none", "pulse", "float", "spin");
    private static final List<String> SIZES = Arrays.asList("small", "medium", "large");
    private static final List<String> CHARTS = Arrays.asList("bars", "line");
    private static final List<String> SHAPES = Arrays.asList("line", "circle", "rect", "path", "text");
    private static final Pattern ID = Pattern.compile("[a-z0-9][a-z0-9-]{0,31}");
    private static final Pattern COLOR = Pattern.compile("#[0-9a-f]{6}");
    /** The letters and numbers of an SVG path, and nothing else. */
    private static final Pattern PATH = Pattern.compile("[MmLlHhVvCcSsQqTtAaZz0-9eE+\\-., ]{1,800}");

    /** Why a moment was not taken: in words for whoever sent it, with the field at fault. */
    static final class Refusal extends Exception {
        final String field;

        Refusal(String field, String message) {
            super(message);
            this.field = field;
        }
    }

    private static final Moments INSTANCE = new Moments();

    private final List<JSONObject> moments = new ArrayList<>();
    private long version;
    private long made;

    static Moments getInstance() {
        return INSTANCE;
    }

    /** Changes with every moment that comes, goes or runs out. */
    synchronized long version(long now) {
        prune(now);
        return version;
    }

    /** What is on the glass now, oldest first. */
    synchronized JSONObject document(long now) throws JSONException {
        prune(now);
        return new JSONObject().put("moments", new JSONArray(moments)).put("version", version).put("now", now);
    }

    /**
     * Puts a moment on the glass, in place of the one with its id if there is one.
     *
     * @return the moment as the glass has it
     */
    synchronized JSONObject put(JSONObject body, long now) throws Refusal, JSONException {
        prune(now);
        JSONObject moment = read(body, now);
        String id = moment.getString("id");
        int at = indexOf(id);
        if (at >= 0) {
            moments.set(at, moment);
        } else {
            if (moments.size() >= LIMIT) {
                moments.remove(0);
            }
            moments.add(moment);
        }
        version++;
        return moment;
    }

    /** Whether a moment of this id is on the glass now. */
    synchronized boolean shows(String id, long now) {
        prune(now);
        return indexOf(id) >= 0;
    }

    synchronized boolean remove(String id, long now) {
        prune(now);
        int at = indexOf(id);
        if (at < 0) {
            return false;
        }
        moments.remove(at);
        version++;
        return true;
    }

    synchronized int clear() {
        int count = moments.size();
        if (count > 0) {
            moments.clear();
            version++;
        }
        return count;
    }

    private int indexOf(String id) {
        for (int index = 0; index < moments.size(); index++) {
            if (moments.get(index).optString("id").equals(id)) {
                return index;
            }
        }
        return -1;
    }

    private void prune(long now) {
        for (int index = moments.size() - 1; index >= 0; index--) {
            if (moments.get(index).optLong("until") <= now) {
                moments.remove(index);
                version++;
            }
        }
    }

    private JSONObject read(JSONObject body, long now) throws Refusal, JSONException {
        String kind = body.optString("kind", "");
        if (!KINDS.contains(kind)) {
            throw new Refusal("kind", "kind must be one of: text, countdown, list, chart, drawing");
        }
        String id = body.has("id") ? body.optString("id", "") : "m" + (++made);
        if (!ID.matcher(id).matches()) {
            throw new Refusal("id", "id takes up to 32 small letters, digits and dashes");
        }
        JSONObject moment = new JSONObject().put("id", id).put("kind", kind).put("createdAt", now);
        moment.put("size", choice(body, "size", SIZES, "medium"));
        // Left out, the glass finds free room for it.
        if (body.has("height")) {
            moment.put("height", choice(body, "height", PanelPlace.HEIGHTS, null));
        }
        if (body.has("side")) {
            moment.put("side", choice(body, "side", PanelPlace.SIDES, null));
        }
        String title = line(body, "title", 40, false);
        if (!title.isEmpty()) {
            moment.put("title", title);
        }
        if (body.has("color")) {
            moment.put("color", color(body, "color"));
        }
        moment.put("motion", choice(body, "motion", MOTIONS, "none"));

        long until = -1;
        switch (kind) {
            case "text":
                moment.put("text", text(body, "text", 280, 6));
                break;
            case "countdown":
                long endsAt = body.optLong("endsAt", -1);
                if (endsAt <= now || endsAt > now + MOST_SECONDS * 1000L) {
                    throw new Refusal("endsAt", "endsAt must be a moment in the next six hours, in epoch milliseconds");
                }
                moment.put("endsAt", endsAt);
                until = endsAt + LINGER_SECONDS * 1000L;
                break;
            case "list":
                moment.put("rows", rows(body));
                break;
            case "chart":
                moment.put("values", values(body));
                moment.put("chart", choice(body, "chart", CHARTS, "bars"));
                break;
            default:
                moment.put("shapes", shapes(body));
        }
        if (body.has("seconds") || until < 0) {
            until = now + whole(body, "seconds", LEAST_SECONDS, MOST_SECONDS, USUAL_SECONDS) * 1000L;
        }
        return moment.put("until", until);
    }

    private static JSONArray rows(JSONObject body) throws Refusal, JSONException {
        JSONArray given = array(body, "rows", MOST_ROWS);
        JSONArray rows = new JSONArray();
        for (int index = 0; index < given.length(); index++) {
            JSONObject row = given.optJSONObject(index);
            if (row == null) {
                throw new Refusal("rows", "Each row is an object with text, and a label if it has one");
            }
            rows.put(new JSONObject()
                    .put("label", line(row, "label", 14, false))
                    .put("text", line(row, "text", 60, true)));
        }
        return rows;
    }

    private static JSONArray values(JSONObject body) throws Refusal, JSONException {
        JSONArray given = array(body, "values", MOST_VALUES);
        if (given.length() < 2) {
            throw new Refusal("values", "A chart needs at least two values");
        }
        JSONArray values = new JSONArray();
        for (int index = 0; index < given.length(); index++) {
            JSONObject value = given.optJSONObject(index);
            if (value == null) {
                throw new Refusal("values", "Each value is an object with a label and a value");
            }
            values.put(new JSONObject()
                    .put("label", line(value, "label", 8, false))
                    .put("value", number(value, "value", -1e6, 1e6, Double.NaN)));
        }
        return values;
    }

    private static JSONArray shapes(JSONObject body) throws Refusal, JSONException {
        JSONArray given = array(body, "shapes", MOST_SHAPES);
        JSONArray shapes = new JSONArray();
        for (int index = 0; index < given.length(); index++) {
            JSONObject source = given.optJSONObject(index);
            String kind = source == null ? "" : source.optString("shape", "");
            if (!SHAPES.contains(kind)) {
                throw new Refusal("shapes", "Each shape is an object whose shape is line, circle, rect, path or text");
            }
            JSONObject shape = new JSONObject().put("shape", kind);
            // A drawing is 100 units wide and 100 high; a little beyond its edge is cut off.
            switch (kind) {
                case "line":
                    copy(source, shape, "x1", "y1", "x2", "y2");
                    break;
                case "circle":
                    copy(source, shape, "x", "y");
                    shape.put("r", number(source, "r", 0.1, 100, Double.NaN));
                    break;
                case "rect":
                    copy(source, shape, "x", "y");
                    shape.put("w", number(source, "w", 0.1, 200, Double.NaN));
                    shape.put("h", number(source, "h", 0.1, 200, Double.NaN));
                    shape.put("round", number(source, "round", 0, 50, 0));
                    break;
                case "path":
                    String d = source.optString("d", "");
                    if (!PATH.matcher(d).matches()) {
                        throw new Refusal("shapes", "A path's d takes up to 800 characters of SVG path commands and numbers");
                    }
                    shape.put("d", d);
                    break;
                default:
                    copy(source, shape, "x", "y");
                    shape.put("text", line(source, "text", 40, true));
                    shape.put("size", number(source, "size", 2, 60, 10));
            }
            for (String paint : new String[] {"stroke", "fill"}) {
                if (source.has(paint)) {
                    shape.put(paint, "none".equals(source.optString(paint)) ? "none" : color(source, paint));
                }
            }
            if (source.has("width")) {
                shape.put("width", number(source, "width", 0.1, 20, Double.NaN));
            }
            shapes.put(shape);
        }
        return shapes;
    }

    private static void copy(JSONObject from, JSONObject to, String... names) throws Refusal, JSONException {
        for (String name : names) {
            to.put(name, number(from, name, -100, 200, Double.NaN));
        }
    }

    private static JSONArray array(JSONObject body, String name, int most) throws Refusal {
        JSONArray array = body.optJSONArray(name);
        if (array == null || array.length() == 0 || array.length() > most) {
            throw new Refusal(name, name + " is a list of 1 to " + most);
        }
        return array;
    }

    /** A number between the two ends; the fallback when it is left out, and required when the fallback is no number. */
    private static double number(JSONObject body, String name, double least, double most, double fallback)
            throws Refusal {
        Object value = body.opt(name);
        if (value == null && !Double.isNaN(fallback)) {
            return fallback;
        }
        double number = value instanceof Number ? ((Number) value).doubleValue() : Double.NaN;
        if (Double.isNaN(number) || Double.isInfinite(number) || number < least || number > most) {
            throw new Refusal(name, String.format(Locale.US, "%s must be a number from %s to %s", name, plain(least), plain(most)));
        }
        return number;
    }

    private static int whole(JSONObject body, String name, int least, int most, int fallback) throws Refusal {
        double number = number(body, name, least, Math.max(least, most), fallback < 0 ? Double.NaN : fallback);
        if (number != Math.rint(number)) {
            throw new Refusal(name, name + " must be a whole number");
        }
        return (int) number;
    }

    private static String choice(JSONObject body, String name, List<String> choices, String fallback) throws Refusal {
        String value = body.has(name) ? body.optString(name, "") : fallback;
        if (value == null || !choices.contains(value)) {
            throw new Refusal(name, name + " must be one of: " + choices.toString().replaceAll("[\\[\\]]", ""));
        }
        return value;
    }

    private static String plain(double number) {
        return number == Math.rint(number) ? String.valueOf((long) number) : String.valueOf(number);
    }

    private static String color(JSONObject body, String name) throws Refusal {
        String color = body.optString(name, "").toLowerCase(Locale.US);
        if (!COLOR.matcher(color).matches()) {
            throw new Refusal(name, name + " must be a colour as #rrggbb");
        }
        return color;
    }

    private static String line(JSONObject body, String name, int most, boolean required) throws Refusal {
        return words(body, name, most, 1, required);
    }

    private static String text(JSONObject body, String name, int most, int lines) throws Refusal {
        return words(body, name, most, lines, true);
    }

    private static String words(JSONObject body, String name, int most, int lines, boolean required) throws Refusal {
        Object value = body.opt(name);
        if (value == null && !required) {
            return "";
        }
        String text = value instanceof String ? ((String) value).trim() : null;
        int breaks = 0;
        for (int index = 0; text != null && index < text.length(); index++) {
            char letter = text.charAt(index);
            if (letter == '\n') {
                breaks++;
            } else if (Character.isISOControl(letter)) {
                text = null;
            }
        }
        if (text == null || text.length() > most || breaks >= lines || (required && text.isEmpty())) {
            throw new Refusal(name, name + " takes " + (required ? "1" : "0") + " to " + most + " characters"
                    + (lines > 1 ? " on up to " + lines + " lines" : " on one line"));
        }
        return text;
    }
}
