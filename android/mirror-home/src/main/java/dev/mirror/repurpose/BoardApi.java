package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.util.List;
import java.util.Map;

/* The board's HTTP shape, kept apart from the web server so that every route,
   status and message is unit-tested: ControlServer hands a request over and
   sends back whatever comes out.

   Written for callers that have never seen the documentation. Each refusal
   says what was wrong, names the field, and points at the guide, which the
   Mirror serves without a token so that a program can read how to begin. */
public final class BoardApi {
    public static final String ROOT = "/api/v1/board";
    public static final String GUIDE_PATH = ROOT + "/guide";
    public static final String ITEMS_PATH = ROOT + "/items";
    public static final int DEFAULT_LIMIT = 50;

    /** Hands over the request body when a route needs it; a request that is refused first never asks. */
    public interface BodyReader {
        JSONObject read() throws IOException, JSONException;
    }

    public static final class Caller {
        final boolean authorized;
        final boolean loopback;
        final String name;

        /** name: the paired device's name, used as an item's source when it gives none. */
        public Caller(boolean authorized, boolean loopback, String name) {
            this.authorized = authorized;
            this.loopback = loopback;
            this.name = name;
        }
    }

    public static final class Reply {
        public final int status;
        public final JSONObject body;

        Reply(int status, JSONObject body) {
            this.status = status;
            this.body = body;
        }
    }

    private static final String[] LIST_PARAMETERS = {"kind", "source", "done", "limit", "offset"};
    private static final String[] REMOVE_PARAMETERS = {"kind", "source", "done", "all"};

    private final BoardItems items;

    public BoardApi(BoardItems items) {
        this.items = items;
    }

    public static boolean handles(String uri) {
        return ROOT.equals(uri) || uri.startsWith(ROOT + "/");
    }

    /**
     * Answers one request.
     *
     * @param glassNotice why the board is not on the glass at the moment, or
     *     null when a Board widget is showing
     */
    public Reply handle(
            String method,
            String uri,
            Map<String, String> query,
            BodyReader body,
            Caller caller,
            String glassNotice,
            long now) {
        try {
            return route(method, trimmed(uri), query, body, caller, glassNotice, now);
        } catch (BoardError refusal) {
            return refusal(refusal);
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to answer a board request", impossible);
        }
    }

    private Reply route(
            String method,
            String uri,
            Map<String, String> query,
            BodyReader body,
            Caller caller,
            String glassNotice,
            long now) throws JSONException {
        if (GUIDE_PATH.equals(uri)) {
            requireMethod(method, "GET");
            return new Reply(200, BoardGuide.build());
        }
        boolean summary = ROOT.equals(uri);
        boolean collection = ITEMS_PATH.equals(uri);
        String id = uri.startsWith(ITEMS_PATH + "/")
                ? uri.substring(ITEMS_PATH.length() + 1)
                : null;
        if (!summary && !collection && (id == null || id.isEmpty() || id.contains("/"))) {
            throw new BoardError(
                    BoardError.NOT_FOUND,
                    null,
                    "The board has no " + uri + ". Its paths are " + ROOT + ", " + ITEMS_PATH
                            + ", " + ITEMS_PATH + "/{id} and " + GUIDE_PATH);
        }
        // The glass reads the summary over loopback; everything else needs a paired device.
        if (!caller.authorized && !(summary && caller.loopback && "GET".equals(method))) {
            throw new BoardError(
                    BoardError.UNAUTHORIZED,
                    null,
                    "The board needs a paired device's token: send the header \"Authorization: "
                            + "Bearer <token>\". " + GUIDE_PATH + " explains how to get one");
        }
        if (summary) {
            requireMethod(method, "GET");
            return new Reply(200, summary(glassNotice, now));
        }
        if (collection) {
            requireMethod(method, "GET", "POST", "DELETE");
            if ("GET".equals(method)) {
                return new Reply(200, page(query, now));
            }
            if ("DELETE".equals(method)) {
                return new Reply(200, removeMatching(query, now));
            }
            JSONObject item = items.create(read(body), caller.name, now);
            return new Reply(201, withNotice(saved(item), glassNotice));
        }
        requireMethod(method, "GET", "PUT", "PATCH", "DELETE");
        if ("PUT".equals(method)) {
            BoardItems.Saved result = items.put(id, read(body), caller.name, now);
            return new Reply(
                    result.created ? 201 : 200,
                    withNotice(saved(result.item).put("created", result.created), glassNotice));
        }
        JSONObject item;
        if ("PATCH".equals(method)) {
            item = items.patch(id, read(body), now);
        } else if ("DELETE".equals(method)) {
            item = items.delete(id, now) ? new JSONObject() : null;
        } else {
            item = items.find(id, now);
        }
        if (item == null) {
            throw new BoardError(
                    BoardError.NOT_FOUND,
                    "id",
                    "The board has no item \"" + id + "\". It may have expired or been removed; "
                            + "GET " + ITEMS_PATH + " lists what is there");
        }
        if ("DELETE".equals(method)) {
            return new Reply(200, new JSONObject().put("deleted", 1).put("version", items.version()));
        }
        return new Reply(200, saved(item));
    }

    private JSONObject summary(String glassNotice, long now) throws JSONException {
        List<JSONObject> showing = items.showing(now);
        JSONObject glass = new JSONObject().put("showsBoard", glassNotice == null);
        if (glassNotice != null) {
            glass.put("notice", glassNotice);
        }
        return new JSONObject()
                .put("version", items.version())
                .put("now", now)
                .put("nowIso", BoardTime.iso(now))
                .put("glass", glass)
                .put("counts", items.counts(now))
                .put("items", new JSONArray(showing))
                .put("doneLingerSeconds", BoardItems.DONE_LINGER_MS / 1000L)
                .put("soonSeconds", BoardItems.SOON_MS / 1000L)
                .put("guide", GUIDE_PATH);
    }

    private JSONObject page(Map<String, String> query, long now) throws JSONException {
        requireKnown(query, LIST_PARAMETERS);
        int limit = wholeNumber(query, "limit", DEFAULT_LIMIT, 1, BoardItems.MAX_ITEMS);
        int offset = wholeNumber(query, "offset", 0, 0, Integer.MAX_VALUE);
        List<JSONObject> matching = items.list(filter(query), now);
        JSONArray slice = new JSONArray();
        int end = (int) Math.min(matching.size(), (long) offset + limit);
        for (int index = offset; index < end; index++) {
            slice.put(matching.get(index));
        }
        return new JSONObject()
                .put("items", slice)
                .put("total", matching.size())
                .put("offset", offset)
                .put("limit", limit)
                .put("nextOffset", end < matching.size() ? Integer.valueOf(end) : JSONObject.NULL)
                .put("version", items.version())
                .put("now", now)
                .put("nowIso", BoardTime.iso(now));
    }

    private JSONObject removeMatching(Map<String, String> query, long now) throws JSONException {
        requireKnown(query, REMOVE_PARAMETERS);
        BoardItems.Filter filter = filter(query);
        boolean everything = flag(query, "all") == Boolean.TRUE;
        if (filter.kind == null && filter.source == null && filter.done == null && !everything) {
            throw BoardError.invalid(
                    null,
                    "Say which items to remove: ?source=NAME, ?kind=todo, ?done=true, a "
                            + "combination, or ?all=true for the whole board");
        }
        return new JSONObject()
                .put("deleted", items.deleteMatching(filter, now))
                .put("version", items.version());
    }

    private JSONObject saved(JSONObject item) throws JSONException {
        return new JSONObject().put("item", item).put("version", items.version());
    }

    private static JSONObject withNotice(JSONObject reply, String glassNotice) throws JSONException {
        return glassNotice == null ? reply : reply.put("notice", glassNotice);
    }

    private static BoardItems.Filter filter(Map<String, String> query) {
        String kind = query.get("kind");
        if (kind != null && !BoardItems.contains(BoardItems.KINDS, kind)) {
            throw BoardError.invalid(
                    "kind", "kind must be one of " + BoardItems.joined(BoardItems.KINDS));
        }
        return new BoardItems.Filter(kind, query.get("source"), flag(query, "done"));
    }

    private static Boolean flag(Map<String, String> query, String name) {
        String value = query.get(name);
        if (value == null) {
            return null;
        }
        if ("true".equals(value) || "false".equals(value)) {
            return Boolean.valueOf(value);
        }
        throw BoardError.invalid(name, name + " must be true or false");
    }

    private static int wholeNumber(
            Map<String, String> query, String name, int fallback, int minimum, int maximum) {
        String value = query.get(name);
        if (value == null) {
            return fallback;
        }
        try {
            int number = Integer.parseInt(value);
            if (number >= minimum && number <= maximum) {
                return number;
            }
        } catch (NumberFormatException notANumber) {
            // Falls through to the refusal below.
        }
        throw BoardError.invalid(
                name,
                maximum == Integer.MAX_VALUE
                        ? name + " must be a whole number, " + minimum + " or more"
                        : name + " must be a whole number from " + minimum + " to " + maximum);
    }

    private static void requireKnown(Map<String, String> query, String[] known) {
        for (String name : query.keySet()) {
            if (!BoardItems.contains(known, name)) {
                throw BoardError.invalid(
                        name,
                        "Unknown query parameter \"" + name + "\". This request takes "
                                + BoardItems.joined(known));
            }
        }
    }

    private static void requireMethod(String method, String... allowed) {
        if (!BoardItems.contains(allowed, method)) {
            throw new BoardError(
                    BoardError.METHOD_NOT_ALLOWED,
                    null,
                    method + " is not available here. This path takes "
                            + BoardItems.joined(allowed));
        }
    }

    private static JSONObject read(BodyReader body) {
        try {
            return body.read();
        } catch (JSONException notJson) {
            throw BoardError.invalid(
                    null,
                    "The request body must be one JSON object, for example {\"title\": \"Water "
                            + "the plants\"}");
        } catch (IOException unreadable) {
            throw BoardError.invalid(
                    null,
                    "The request body could not be read. Send JSON of at most 64 KB with a "
                            + "Content-Length header");
        }
    }

    private static Reply refusal(BoardError refusal) {
        try {
            JSONObject body = new JSONObject().put("error", refusal.getMessage());
            if (refusal.field != null) {
                body.put("field", refusal.field);
            }
            return new Reply(refusal.status, body.put("guide", GUIDE_PATH));
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to describe a refusal", impossible);
        }
    }

    private static String trimmed(String uri) {
        return uri.length() > ROOT.length() && uri.endsWith("/")
                ? uri.substring(0, uri.length() - 1)
                : uri;
    }
}
