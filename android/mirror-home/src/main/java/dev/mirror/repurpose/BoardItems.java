package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.Iterator;
import java.util.List;

/* The board: notes, to-dos and reminders that programs on the home network
   put on the Mirror for people to see. Pure Java so every rule is
   unit-testable; BoardStore handles persistence and BoardApi the HTTP shape.

   Three ideas keep it dependable beside a household:
   - an item says what it is (kind) and the Mirror decides how to draw it, so
     no caller ever sends markup;
   - everything leaves by itself unless its sender says otherwise, so a
     program that posts and forgets leaves nothing stale on the glass;
   - what a caller reads back (state, showing) is worked out here, at the
     moment of asking, from the same rules the glass follows. */
public final class BoardItems {
    public static final int MAX_ITEMS = 100;
    public static final int MAX_TITLE_LENGTH = 120;
    public static final int MAX_BODY_LENGTH = 500;
    public static final int MAX_SOURCE_LENGTH = 40;
    public static final int MAX_ID_LENGTH = 64;
    /** How long an item stays when its sender gives no expiry. */
    public static final long DEFAULT_LIFETIME_MS = 24L * 60 * 60 * 1000;
    public static final long MAX_TTL_SECONDS = 366L * 24 * 60 * 60;
    /** How long a finished item stays on the glass, struck through. */
    public static final long DONE_LINGER_MS = 10L * 60 * 1000;
    /** How far ahead of its time an item counts as coming up. */
    public static final long SOON_MS = 60L * 60 * 1000;

    public static final String KIND_NOTE = "note";
    public static final String KIND_TODO = "todo";
    public static final String KIND_REMINDER = "reminder";
    static final String[] KINDS = {KIND_NOTE, KIND_TODO, KIND_REMINDER};
    static final String[] PRIORITIES = {"low", "normal", "high"};
    static final String[] STATES = {"open", "soon", "overdue", "done"};
    static final String[] WRITABLE_FIELDS = {
            "kind", "title", "body", "due", "done", "priority", "expiresAt", "ttlSeconds", "source"
    };
    /* Sent back by the Mirror; accepted and ignored in a request so that what
       was read can be written again unchanged. */
    static final String[] READ_ONLY_FIELDS = {
            "id", "createdAt", "updatedAt", "doneAt", "state", "showing", "dueIso", "expiresAtIso"
    };

    private static final String ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";
    private static final int GENERATED_ID_LENGTH = 8;

    /** Which items a listing or a bulk removal is about; null fields match everything. */
    public static final class Filter {
        public final String kind;
        public final String source;
        public final Boolean done;

        public Filter(String kind, String source, Boolean done) {
            this.kind = kind;
            this.source = source;
            this.done = done;
        }

        boolean matches(JSONObject item) {
            return (kind == null || kind.equals(item.optString("kind")))
                    && (source == null || source.equals(item.optString("source")))
                    && (done == null || done == item.optBoolean("done"));
        }
    }

    /** The outcome of a write addressed to an id the caller chose. */
    public static final class Saved {
        public final JSONObject item;
        public final boolean created;

        Saved(JSONObject item, boolean created) {
            this.item = item;
            this.created = created;
        }
    }

    private final List<JSONObject> items = new ArrayList<>();
    private final SecureRandom random = new SecureRandom();
    private long version;
    private boolean unsaved;

    public BoardItems() {
    }

    public static BoardItems parse(String serialized) {
        BoardItems board = new BoardItems();
        if (serialized == null || serialized.isEmpty()) {
            return board;
        }
        try {
            JSONObject root = new JSONObject(serialized);
            board.version = Math.max(0, root.optLong("version", 0));
            JSONArray array = root.optJSONArray("items");
            for (int index = 0; array != null && index < array.length(); index++) {
                JSONObject item = restored(array.optJSONObject(index));
                if (item != null && board.indexOf(item.optString("id")) < 0) {
                    board.items.add(item);
                }
                if (board.items.size() >= MAX_ITEMS) {
                    break;
                }
            }
        } catch (JSONException ignored) {
            // A corrupt store yields an empty board rather than a crash.
        }
        return board;
    }

    public String serialize() {
        try {
            JSONArray array = new JSONArray();
            for (JSONObject item : items) {
                array.put(new JSONObject(item.toString()));
            }
            return new JSONObject().put("version", version).put("items", array).toString();
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to serialize the board", impossible);
        }
    }

    /** A counter that moves whenever the board's contents do. */
    public long version() {
        return version;
    }

    /** Whether something changed since the last call; the store saves when it did. */
    public boolean takeUnsaved() {
        boolean result = unsaved;
        unsaved = false;
        return result;
    }

    public int size() {
        return items.size();
    }

    /** Drops what has expired. Every read and write starts here. */
    public void expire(long now) {
        boolean removed = false;
        for (Iterator<JSONObject> iterator = items.iterator(); iterator.hasNext(); ) {
            JSONObject item = iterator.next();
            if (item.has("expiresAt") && item.optLong("expiresAt") <= now) {
                iterator.remove();
                removed = true;
            }
        }
        if (removed) {
            changed();
        }
    }

    /** A new item under an id the Mirror picks. */
    public JSONObject create(JSONObject request, String defaultSource, long now) {
        expire(now);
        if (request.has("id")) {
            throw BoardError.invalid(
                    "id",
                    "POST picks the id. To choose it yourself, PUT "
                            + BoardApi.ITEMS_PATH + "/{id}");
        }
        requireRoom();
        JSONObject item = build(newId(), null, request, false, defaultSource, now);
        items.add(item);
        changed();
        return view(item, now);
    }

    /** Creates the item with this id, or replaces it whole. */
    public Saved put(String id, JSONObject request, String defaultSource, long now) {
        expire(now);
        requireValidId(id);
        requireSameId(id, request);
        int index = indexOf(id);
        if (index < 0) {
            requireRoom();
        }
        JSONObject item = build(
                id, index < 0 ? null : items.get(index), request, false, defaultSource, now);
        if (index < 0) {
            items.add(item);
        } else {
            items.set(index, item);
        }
        changed();
        return new Saved(view(item, now), index < 0);
    }

    /** Changes the fields the request names and leaves the rest; null when there is no such item. */
    public JSONObject patch(String id, JSONObject request, long now) {
        expire(now);
        requireSameId(id, request);
        int index = indexOf(id);
        if (index < 0) {
            return null;
        }
        JSONObject item = build(id, items.get(index), request, true, null, now);
        items.set(index, item);
        changed();
        return view(item, now);
    }

    public JSONObject find(String id, long now) {
        expire(now);
        int index = indexOf(id);
        return index < 0 ? null : view(items.get(index), now);
    }

    public boolean delete(String id, long now) {
        expire(now);
        int index = indexOf(id);
        if (index < 0) {
            return false;
        }
        items.remove(index);
        changed();
        return true;
    }

    public int deleteMatching(Filter filter, long now) {
        expire(now);
        int removed = 0;
        for (Iterator<JSONObject> iterator = items.iterator(); iterator.hasNext(); ) {
            if (filter.matches(iterator.next())) {
                iterator.remove();
                removed++;
            }
        }
        if (removed > 0) {
            changed();
        }
        return removed;
    }

    /** Every matching item in the order the glass lists them. */
    public List<JSONObject> list(Filter filter, long now) {
        expire(now);
        List<JSONObject> ordered = new ArrayList<>();
        for (JSONObject item : items) {
            if (filter == null || filter.matches(item)) {
                ordered.add(item);
            }
        }
        Collections.sort(ordered, displayOrder(now));
        List<JSONObject> views = new ArrayList<>(ordered.size());
        for (JSONObject item : ordered) {
            views.add(view(item, now));
        }
        return views;
    }

    /** What the board widget lists right now. */
    public List<JSONObject> showing(long now) {
        List<JSONObject> views = new ArrayList<>();
        for (JSONObject item : list(null, now)) {
            if (item.optBoolean("showing")) {
                views.add(item);
            }
        }
        return views;
    }

    public JSONObject counts(long now) {
        expire(now);
        int showing = 0;
        int[] byState = new int[STATES.length];
        for (JSONObject item : items) {
            String state = state(item, now);
            for (int index = 0; index < STATES.length; index++) {
                if (STATES[index].equals(state)) {
                    byState[index]++;
                }
            }
            if (showing(item, now)) {
                showing++;
            }
        }
        try {
            JSONObject counts = new JSONObject()
                    .put("total", items.size())
                    .put("showing", showing);
            for (int index = 0; index < STATES.length; index++) {
                counts.put(STATES[index], byState[index]);
            }
            return counts;
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to count the board", impossible);
        }
    }

    public static boolean validId(String id) {
        return id != null && id.matches("[A-Za-z0-9][A-Za-z0-9._-]{0," + (MAX_ID_LENGTH - 1) + "}");
    }

    /* ---- Rules ---- */

    private static final class Draft {
        String kind = KIND_NOTE;
        String title;
        String body = "";
        String priority = "normal";
        String source;
        Long due;
        Long doneAt;
        Long expiresAt;
        boolean done;
        boolean autoExpiry = true;
        long createdAt;
    }

    /* One path for create, replace and change, so the three cannot drift:
       start from the stored item (change) or from defaults (create, replace),
       lay the request's fields over it, then check the whole. */
    private static JSONObject build(
            String id,
            JSONObject existing,
            JSONObject request,
            boolean merge,
            String defaultSource,
            long now) {
        rejectUnknownFields(request);
        Draft draft = new Draft();
        draft.createdAt = existing == null ? now : existing.optLong("createdAt", now);
        draft.source = cleanDefaultSource(defaultSource);
        boolean wasDone = existing != null && existing.optBoolean("done");
        if (wasDone && existing.has("doneAt")) {
            draft.doneAt = existing.optLong("doneAt");
        }
        if (merge) {
            draft.kind = existing.optString("kind", KIND_NOTE);
            draft.title = existing.optString("title");
            draft.body = existing.optString("body", "");
            draft.priority = existing.optString("priority", "normal");
            draft.source = existing.optString("source", draft.source);
            draft.due = existing.has("due") ? Long.valueOf(existing.optLong("due")) : null;
            draft.done = wasDone;
            draft.autoExpiry = existing.optBoolean("autoExpiry");
            draft.expiresAt = existing.has("expiresAt")
                    ? Long.valueOf(existing.optLong("expiresAt"))
                    : null;
        }

        if (request.has("kind")) {
            draft.kind = oneOf(request.opt("kind"), KINDS, "kind");
        }
        if (request.has("title")) {
            draft.title = title(request.opt("title"));
        } else if (!merge) {
            throw BoardError.invalid("title", "title is required: the line the Mirror shows");
        }
        if (request.has("body")) {
            draft.body = body(request.opt("body"));
        }
        if (request.has("priority")) {
            draft.priority = oneOf(request.opt("priority"), PRIORITIES, "priority");
        }
        if (request.has("source") && !request.isNull("source")) {
            draft.source = source(request.opt("source"));
        }
        if (request.has("due")) {
            draft.due = request.isNull("due")
                    ? null
                    : Long.valueOf(BoardTime.parse(request.opt("due"), "due"));
        }
        if (request.has("done")) {
            Object done = request.opt("done");
            if (!(done instanceof Boolean)) {
                throw BoardError.invalid("done", "done must be true or false");
            }
            draft.done = (Boolean) done;
        }
        applyExpiry(draft, request, now);

        if (KIND_NOTE.equals(draft.kind)) {
            if (draft.due != null) {
                throw BoardError.invalid(
                        "due",
                        "A note has no due time. Make it a \"reminder\" or a \"todo\", or leave"
                                + " due out");
            }
            if (draft.done) {
                throw BoardError.invalid(
                        "done", "A note cannot be done. Make it a \"todo\", or delete the note");
            }
        }
        if (KIND_REMINDER.equals(draft.kind) && draft.due == null) {
            throw BoardError.invalid(
                    "due",
                    "A reminder needs due, the moment it is about: " + BoardTime.formats());
        }
        if (!draft.done) {
            draft.doneAt = null;
        } else if (draft.doneAt == null) {
            draft.doneAt = now;
        }
        if (draft.autoExpiry) {
            long from = draft.due == null ? now : Math.max(now, draft.due);
            draft.expiresAt = from + DEFAULT_LIFETIME_MS;
        }

        try {
            JSONObject item = new JSONObject()
                    .put("id", id)
                    .put("kind", draft.kind)
                    .put("title", draft.title)
                    .put("body", draft.body)
                    .put("priority", draft.priority)
                    .put("source", draft.source)
                    .put("done", draft.done)
                    .put("autoExpiry", draft.autoExpiry)
                    .put("createdAt", draft.createdAt)
                    .put("updatedAt", now);
            if (draft.due != null) {
                item.put("due", draft.due.longValue());
            }
            if (draft.doneAt != null) {
                item.put("doneAt", draft.doneAt.longValue());
            }
            if (draft.expiresAt != null) {
                item.put("expiresAt", draft.expiresAt.longValue());
            }
            return item;
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to build a board item", impossible);
        }
    }

    private static void applyExpiry(Draft draft, JSONObject request, long now) {
        boolean hasExpiry = request.has("expiresAt");
        boolean hasTtl = request.has("ttlSeconds") && !request.isNull("ttlSeconds");
        if (hasExpiry && hasTtl) {
            throw BoardError.invalid(
                    "ttlSeconds", "Give expiresAt or ttlSeconds, not both: they say the same thing");
        }
        if (hasTtl) {
            Object ttl = request.opt("ttlSeconds");
            double seconds = ttl instanceof Number ? ((Number) ttl).doubleValue() : Double.NaN;
            if (Double.isNaN(seconds) || seconds != Math.rint(seconds)
                    || seconds < 1 || seconds > MAX_TTL_SECONDS) {
                throw BoardError.invalid(
                        "ttlSeconds",
                        "ttlSeconds must be a whole number of seconds from 1 to " + MAX_TTL_SECONDS);
            }
            draft.expiresAt = now + (long) seconds * 1000L;
            draft.autoExpiry = false;
        } else if (hasExpiry) {
            Object expiry = request.opt("expiresAt");
            if (request.isNull("expiresAt") || "never".equals(expiry)) {
                draft.expiresAt = null;
            } else {
                long at = BoardTime.parse(expiry, "expiresAt");
                if (at <= now) {
                    throw BoardError.invalid(
                            "expiresAt",
                            "expiresAt is already past; the Mirror's clock reads "
                                    + BoardTime.iso(now));
                }
                draft.expiresAt = at;
            }
            draft.autoExpiry = false;
        }
    }

    private static void rejectUnknownFields(JSONObject request) {
        Iterator<String> keys = request.keys();
        while (keys.hasNext()) {
            String key = keys.next();
            if (!contains(WRITABLE_FIELDS, key) && !contains(READ_ONLY_FIELDS, key)) {
                throw BoardError.invalid(
                        key,
                        "Unknown field \"" + key + "\". An item takes " + joined(WRITABLE_FIELDS));
            }
        }
    }

    private static String title(Object value) {
        if (!(value instanceof String)) {
            throw BoardError.invalid("title", "title must be text");
        }
        String title = ((String) value).trim();
        if (title.isEmpty()) {
            throw BoardError.invalid("title", "title cannot be empty");
        }
        if (title.length() > MAX_TITLE_LENGTH) {
            throw BoardError.invalid(
                    "title",
                    "title holds up to " + MAX_TITLE_LENGTH + " characters; put the rest in body");
        }
        if (containsControlCharacter(title, false)) {
            throw BoardError.invalid("title", "title is one line; put further lines in body");
        }
        return title;
    }

    private static String body(Object value) {
        if (value == null || value == JSONObject.NULL) {
            return "";
        }
        if (!(value instanceof String)) {
            throw BoardError.invalid("body", "body must be text");
        }
        String body = ((String) value).replace("\r\n", "\n").replace('\r', '\n').trim();
        if (body.length() > MAX_BODY_LENGTH) {
            throw BoardError.invalid("body", "body holds up to " + MAX_BODY_LENGTH + " characters");
        }
        if (containsControlCharacter(body, true)) {
            throw BoardError.invalid("body", "body cannot contain control characters");
        }
        return body;
    }

    private static String source(Object value) {
        if (!(value instanceof String)) {
            throw BoardError.invalid("source", "source must be text");
        }
        String source = ((String) value).trim();
        if (source.isEmpty() || source.length() > MAX_SOURCE_LENGTH
                || containsControlCharacter(source, false)) {
            throw BoardError.invalid(
                    "source",
                    "source is a short name for who posted this, up to " + MAX_SOURCE_LENGTH
                            + " characters");
        }
        return source;
    }

    private static String cleanDefaultSource(String name) {
        if (name == null) {
            return "unknown";
        }
        StringBuilder clean = new StringBuilder();
        for (int index = 0; index < name.length() && clean.length() < MAX_SOURCE_LENGTH; index++) {
            char character = name.charAt(index);
            if (!Character.isISOControl(character)) {
                clean.append(character);
            }
        }
        String trimmed = clean.toString().trim();
        return trimmed.isEmpty() ? "unknown" : trimmed;
    }

    private static String oneOf(Object value, String[] options, String field) {
        if (value instanceof String && contains(options, (String) value)) {
            return (String) value;
        }
        throw BoardError.invalid(field, field + " must be one of " + joined(options));
    }

    private void requireRoom() {
        if (items.size() >= MAX_ITEMS) {
            throw new BoardError(
                    BoardError.CONFLICT,
                    null,
                    "The board holds at most " + MAX_ITEMS + " items. Remove some first, for "
                            + "example DELETE " + BoardApi.ITEMS_PATH + "?done=true");
        }
    }

    private static void requireValidId(String id) {
        if (!validId(id)) {
            throw BoardError.invalid(
                    "id",
                    "An id is 1 to " + MAX_ID_LENGTH + " letters, digits, dots, dashes or "
                            + "underscores, and starts with a letter or digit");
        }
    }

    private static void requireSameId(String id, JSONObject request) {
        if (request.has("id") && !id.equals(request.optString("id"))) {
            throw BoardError.invalid("id", "The id in the body is not the id in the path");
        }
    }

    /* ---- What a caller reads back ---- */

    static String state(JSONObject item, long now) {
        if (item.optBoolean("done")) {
            return "done";
        }
        if (!item.has("due")) {
            return "open";
        }
        long due = item.optLong("due");
        if (due <= now) {
            return "overdue";
        }
        return due - now <= SOON_MS ? "soon" : "open";
    }

    static boolean showing(JSONObject item, long now) {
        return !item.optBoolean("done") || now - item.optLong("doneAt", now) < DONE_LINGER_MS;
    }

    /* Times go out as milliseconds, like the rest of the control API, and the
       two a caller sets also as text, for a reader that does not do sums. */
    private static JSONObject view(JSONObject item, long now) {
        try {
            return new JSONObject()
                    .put("id", item.getString("id"))
                    .put("kind", item.getString("kind"))
                    .put("title", item.getString("title"))
                    .put("body", item.optString("body", ""))
                    .put("due", item.has("due") ? Long.valueOf(item.getLong("due")) : JSONObject.NULL)
                    .put("dueIso", item.has("due")
                            ? BoardTime.iso(item.getLong("due"))
                            : JSONObject.NULL)
                    .put("done", item.optBoolean("done"))
                    .put("doneAt", item.has("doneAt")
                            ? Long.valueOf(item.getLong("doneAt"))
                            : JSONObject.NULL)
                    .put("priority", item.optString("priority", "normal"))
                    .put("expiresAt", item.has("expiresAt")
                            ? Long.valueOf(item.getLong("expiresAt"))
                            : JSONObject.NULL)
                    .put("expiresAtIso", item.has("expiresAt")
                            ? BoardTime.iso(item.getLong("expiresAt"))
                            : JSONObject.NULL)
                    .put("source", item.optString("source", "unknown"))
                    .put("createdAt", item.optLong("createdAt"))
                    .put("updatedAt", item.optLong("updatedAt"))
                    .put("state", state(item, now))
                    .put("showing", showing(item, now));
        } catch (JSONException impossible) {
            throw new IllegalStateException("Stored board item is invalid", impossible);
        }
    }

    /* What needs attention first, then what matters most, then what comes
       soonest, then the order things were posted in; finished things last. */
    private static Comparator<JSONObject> displayOrder(final long now) {
        return new Comparator<JSONObject>() {
            @Override
            public int compare(JSONObject left, JSONObject right) {
                int result = Integer.compare(urgency(left, now), urgency(right, now));
                if (result == 0) {
                    result = Integer.compare(importance(left), importance(right));
                }
                if (result == 0) {
                    result = Long.compare(
                            left.optLong("due", Long.MAX_VALUE),
                            right.optLong("due", Long.MAX_VALUE));
                }
                if (result == 0) {
                    result = Long.compare(left.optLong("createdAt"), right.optLong("createdAt"));
                }
                return result != 0
                        ? result
                        : left.optString("id").compareTo(right.optString("id"));
            }
        };
    }

    private static int urgency(JSONObject item, long now) {
        String state = state(item, now);
        if ("overdue".equals(state)) {
            return 0;
        }
        if ("soon".equals(state)) {
            return 1;
        }
        return "done".equals(state) ? 3 : 2;
    }

    private static int importance(JSONObject item) {
        String priority = item.optString("priority", "normal");
        if ("high".equals(priority)) {
            return 0;
        }
        return "low".equals(priority) ? 2 : 1;
    }

    /* ---- Plumbing ---- */

    private static JSONObject restored(JSONObject item) throws JSONException {
        if (item == null
                || !validId(item.optString("id", ""))
                || !contains(KINDS, item.optString("kind", ""))
                || item.optString("title", "").trim().isEmpty()
                || !contains(PRIORITIES, item.optString("priority", ""))) {
            return null;
        }
        return new JSONObject(item.toString());
    }

    private void changed() {
        version++;
        unsaved = true;
    }

    private int indexOf(String id) {
        if (id == null) {
            return -1;
        }
        for (int index = 0; index < items.size(); index++) {
            if (id.equals(items.get(index).optString("id"))) {
                return index;
            }
        }
        return -1;
    }

    private String newId() {
        while (true) {
            StringBuilder builder = new StringBuilder(GENERATED_ID_LENGTH);
            for (int index = 0; index < GENERATED_ID_LENGTH; index++) {
                builder.append(ID_ALPHABET.charAt(random.nextInt(ID_ALPHABET.length())));
            }
            String candidate = builder.toString();
            if (indexOf(candidate) < 0) {
                return candidate;
            }
        }
    }

    static boolean contains(String[] options, String value) {
        for (String option : options) {
            if (option.equals(value)) {
                return true;
            }
        }
        return false;
    }

    static String joined(String[] values) {
        StringBuilder builder = new StringBuilder();
        for (int index = 0; index < values.length; index++) {
            if (index > 0) {
                builder.append(index == values.length - 1 ? " or " : ", ");
            }
            builder.append(values[index]);
        }
        return builder.toString();
    }

    private static boolean containsControlCharacter(String value, boolean allowLineBreaks) {
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (Character.isISOControl(character) && !(allowLineBreaks && character == '\n')) {
                return true;
            }
        }
        return false;
    }
}
