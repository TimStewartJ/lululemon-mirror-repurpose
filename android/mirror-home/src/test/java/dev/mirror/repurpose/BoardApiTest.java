package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Before;
import org.junit.Test;

import java.io.IOException;
import java.util.HashMap;
import java.util.Map;

public final class BoardApiTest {
    private static final long NOW = 1790960400000L; // 2026-10-02T17:00:00Z
    private static final String ITEMS = "/api/v1/board/items";
    private static final BoardApi.Caller PAIRED = new BoardApi.Caller(true, false, "Kitchen");
    private static final BoardApi.Caller STRANGER = new BoardApi.Caller(false, false, null);
    private static final BoardApi.Caller GLASS = new BoardApi.Caller(false, true, null);

    private BoardItems items;
    private BoardApi api;
    private boolean bodyRead;

    @Before
    public void start() {
        items = new BoardItems();
        api = new BoardApi(items);
        bodyRead = false;
    }

    @Test
    public void knowsItsOwnAddresses() {
        assertTrue(BoardApi.handles("/api/v1/board"));
        assertTrue(BoardApi.handles("/api/v1/board/"));
        assertTrue(BoardApi.handles("/api/v1/board/items/a"));
        assertFalse(BoardApi.handles("/api/v1/boards"));
        assertFalse(BoardApi.handles("/api/v1/notes"));
    }

    @Test
    public void theGuideNeedsNoToken() throws JSONException {
        BoardApi.Reply reply = call(STRANGER, "GET", "/api/v1/board/guide", null);

        assertEquals(200, reply.status);
        assertTrue(reply.body.getJSONArray("start").length() > 0);
        assertEquals(405, call(STRANGER, "POST", "/api/v1/board/guide", "{}").status);
    }

    @Test
    public void everythingElseNeedsOneAndSaysHowToGetIt() throws JSONException {
        String[][] requests = {
                {"GET", "/api/v1/board"},
                {"GET", ITEMS},
                {"POST", ITEMS},
                {"DELETE", ITEMS},
                {"GET", ITEMS + "/a"},
                {"PUT", ITEMS + "/a"},
                {"PATCH", ITEMS + "/a"},
                {"DELETE", ITEMS + "/a"},
        };
        for (String[] request : requests) {
            BoardApi.Reply reply = call(STRANGER, request[0], request[1], "{\"title\": \"T\"}");
            assertEquals(request[0] + " " + request[1], 401, reply.status);
            assertTrue(reply.body.getString("error").contains("Authorization: Bearer"));
            assertEquals("/api/v1/board/guide", reply.body.getString("guide"));
        }
        assertFalse("A refused request is never parsed", bodyRead);
        assertEquals(0, items.size());
    }

    @Test
    public void theGlassReadsTheSummaryOverLoopbackOnly() throws JSONException {
        assertEquals(200, call(GLASS, "GET", "/api/v1/board", null).status);
        assertEquals(401, call(GLASS, "GET", ITEMS, null).status);
        assertEquals(401, call(GLASS, "POST", ITEMS, "{\"title\": \"T\"}").status);
        assertEquals(401, call(GLASS, "DELETE", ITEMS + "?all=true", null).status);
    }

    @Test
    public void postAddsAnItemFromThePairedDevice() throws JSONException {
        BoardApi.Reply reply = call(PAIRED, "POST", ITEMS, "{\"title\": \"Water the plants\"}");

        assertEquals(201, reply.status);
        JSONObject item = reply.body.getJSONObject("item");
        assertEquals("Water the plants", item.getString("title"));
        assertEquals("Kitchen", item.getString("source"));
        assertEquals(1, reply.body.getLong("version"));
        assertFalse(reply.body.has("notice"));

        BoardApi.Reply read = call(PAIRED, "GET", ITEMS + "/" + item.getString("id"), null);
        assertEquals(200, read.status);
        assertEquals(item.toString(), read.body.getJSONObject("item").toString());
    }

    @Test
    public void putIsSafeToRepeat() throws JSONException {
        String body = "{\"kind\": \"todo\", \"title\": \"Call Mom\"}";

        BoardApi.Reply first = call(PAIRED, "PUT", ITEMS + "/call-mom", body);
        BoardApi.Reply second = call(PAIRED, "PUT", ITEMS + "/call-mom/", body);

        assertEquals(201, first.status);
        assertTrue(first.body.getBoolean("created"));
        assertEquals(200, second.status);
        assertFalse(second.body.getBoolean("created"));
        assertEquals(1, items.size());
        assertEquals("call-mom", second.body.getJSONObject("item").getString("id"));
    }

    @Test
    public void patchMarksDoneAndDeleteRemoves() throws JSONException {
        call(PAIRED, "PUT", ITEMS + "/bins", "{\"kind\": \"todo\", \"title\": \"Bins\"}");

        BoardApi.Reply done = call(PAIRED, "PATCH", ITEMS + "/bins", "{\"done\": true}");
        assertEquals(200, done.status);
        assertEquals("done", done.body.getJSONObject("item").getString("state"));
        assertEquals("Bins", done.body.getJSONObject("item").getString("title"));

        BoardApi.Reply removed = call(PAIRED, "DELETE", ITEMS + "/bins", null);
        assertEquals(200, removed.status);
        assertEquals(1, removed.body.getInt("deleted"));
        assertEquals(3, removed.body.getLong("version"));

        for (String method : new String[] {"GET", "PATCH", "DELETE"}) {
            BoardApi.Reply missing = call(PAIRED, method, ITEMS + "/bins", "{\"done\": true}");
            assertEquals(method, 404, missing.status);
            assertEquals("id", missing.body.getString("field"));
            assertTrue(missing.body.getString("error").contains("no item \"bins\""));
        }
    }

    @Test
    public void theSummaryIsWhatTheGlassLists() throws JSONException {
        call(PAIRED, "PUT", ITEMS + "/note", "{\"title\": \"Note\"}");
        call(PAIRED, "PUT", ITEMS + "/old",
                "{\"kind\": \"todo\", \"title\": \"Old\", \"done\": true}");
        long later = NOW + BoardItems.DONE_LINGER_MS;
        call(PAIRED, "PUT", ITEMS + "/late",
                "{\"kind\": \"todo\", \"title\": \"Late\", \"due\": " + (later - 1) + "}", later);

        BoardApi.Reply reply = api.handle(
                "GET", "/api/v1/board", new HashMap<String, String>(), null, GLASS, null, later);

        assertEquals("late note", ids(reply.body.getJSONArray("items")));
        assertEquals(later, reply.body.getLong("now"));
        assertEquals("2026-10-02T17:10:00Z", reply.body.getString("nowIso"));
        assertEquals(items.version(), reply.body.getLong("version"));
        assertTrue(reply.body.getJSONObject("glass").getBoolean("showsBoard"));
        assertFalse(reply.body.getJSONObject("glass").has("notice"));
        JSONObject counts = reply.body.getJSONObject("counts");
        assertEquals(3, counts.getInt("total"));
        assertEquals(2, counts.getInt("showing"));
        assertEquals(1, counts.getInt("overdue"));
        assertEquals(1, counts.getInt("done"));
        assertEquals(600, reply.body.getInt("doneLingerSeconds"));
        assertEquals(3600, reply.body.getInt("soonSeconds"));
        assertEquals("/api/v1/board/guide", reply.body.getString("guide"));
    }

    @Test
    public void writesSayWhenNothingShowsThem() throws JSONException {
        String notice = "No Board widget is visible on the Mirror.";

        BoardApi.Reply posted = api.handle(
                "POST", ITEMS, query(""), body("{\"title\": \"T\"}"), PAIRED, notice, NOW);
        BoardApi.Reply put = api.handle(
                "PUT", ITEMS + "/a", query(""), body("{\"title\": \"T\"}"), PAIRED, notice, NOW);
        BoardApi.Reply summary = api.handle(
                "GET", "/api/v1/board", query(""), null, PAIRED, notice, NOW);

        assertEquals(201, posted.status);
        assertEquals(notice, posted.body.getString("notice"));
        assertEquals(notice, put.body.getString("notice"));
        assertFalse(summary.body.getJSONObject("glass").getBoolean("showsBoard"));
        assertEquals(notice, summary.body.getJSONObject("glass").getString("notice"));
        assertEquals(2, summary.body.getJSONArray("items").length());
    }

    @Test
    public void listsInPagesAndWithFilters() throws JSONException {
        for (int index = 0; index < 7; index++) {
            call(PAIRED, "PUT", ITEMS + "/t" + index,
                    "{\"kind\": \"todo\", \"title\": \"T\", \"source\": \"tether\", \"done\": "
                            + (index % 2 == 1) + "}",
                    NOW + index);
        }
        call(PAIRED, "PUT", ITEMS + "/n", "{\"title\": \"N\"}", NOW + 9);

        BoardApi.Reply all = call(PAIRED, "GET", ITEMS, null);
        assertEquals(8, all.body.getInt("total"));
        assertEquals("2026-10-02T17:00:00Z", all.body.getString("nowIso"));
        assertEquals(50, all.body.getInt("limit"));
        assertEquals(0, all.body.getInt("offset"));
        assertTrue(all.body.isNull("nextOffset"));
        assertEquals("t0 t2 t4 t6 n t1 t3 t5", ids(all.body.getJSONArray("items")));

        StringBuilder walked = new StringBuilder();
        Object offset = Integer.valueOf(0);
        int requests = 0;
        while (offset != JSONObject.NULL) {
            BoardApi.Reply page = call(PAIRED, "GET", ITEMS + "?limit=3&offset=" + offset, null);
            assertEquals(8, page.body.getInt("total"));
            walked.append(walked.length() > 0 ? " " : "").append(ids(page.body.getJSONArray("items")));
            offset = page.body.get("nextOffset");
            requests++;
        }
        assertEquals(3, requests);
        assertEquals("t0 t2 t4 t6 n t1 t3 t5", walked.toString());

        assertEquals("t1 t3 t5", ids(call(PAIRED, "GET", ITEMS + "?source=tether&done=true", null)
                .body.getJSONArray("items")));
        assertEquals("n", ids(call(PAIRED, "GET", ITEMS + "?kind=note", null)
                .body.getJSONArray("items")));
        assertEquals("", ids(call(PAIRED, "GET", ITEMS + "?offset=20", null)
                .body.getJSONArray("items")));
    }

    @Test
    public void removesSeveralOnlyWhenToldWhich() throws JSONException {
        call(PAIRED, "PUT", ITEMS + "/a", "{\"title\": \"A\", \"source\": \"tether\"}");
        call(PAIRED, "PUT", ITEMS + "/b", "{\"kind\": \"todo\", \"title\": \"B\", \"done\": true}");
        call(PAIRED, "PUT", ITEMS + "/c", "{\"title\": \"C\"}");

        BoardApi.Reply vague = call(PAIRED, "DELETE", ITEMS, null);
        assertEquals(400, vague.status);
        assertTrue(vague.body.getString("error").contains("?all=true"));
        assertEquals(400, call(PAIRED, "DELETE", ITEMS + "?all=false", null).status);
        assertEquals(3, items.size());

        assertEquals(1, call(PAIRED, "DELETE", ITEMS + "?source=tether", null).body.getInt("deleted"));
        assertEquals(1, call(PAIRED, "DELETE", ITEMS + "?done=true", null).body.getInt("deleted"));
        assertEquals(0, call(PAIRED, "DELETE", ITEMS + "?kind=reminder", null).body.getInt("deleted"));
        assertEquals(1, call(PAIRED, "DELETE", ITEMS + "?all=true", null).body.getInt("deleted"));
        assertEquals(0, items.size());
    }

    @Test
    public void refusalsExplainThemselves() throws JSONException {
        assertRefusal(call(PAIRED, "POST", ITEMS, "{\"text\": \"Buy milk\"}"),
                400, "text", "Unknown field");
        assertRefusal(call(PAIRED, "POST", ITEMS, "not json"),
                400, null, "must be one JSON object");
        assertRefusal(call(PAIRED, "POST", ITEMS, null),
                400, null, "could not be read");
        assertRefusal(call(PAIRED, "PUT", ITEMS + "/bad id", "{\"title\": \"T\"}"),
                400, "id", "letters, digits");
        assertRefusal(call(PAIRED, "GET", ITEMS + "?state=open", null),
                400, "state", "Unknown query parameter \"state\"");
        assertRefusal(call(PAIRED, "GET", ITEMS + "?limit=0", null),
                400, "limit", "from 1 to 100");
        assertRefusal(call(PAIRED, "GET", ITEMS + "?offset=-1", null),
                400, "offset", "0 or more");
        assertRefusal(call(PAIRED, "GET", ITEMS + "?done=yes", null),
                400, "done", "true or false");
        assertRefusal(call(PAIRED, "GET", ITEMS + "?kind=task", null),
                400, "kind", "note, todo or reminder");
        assertRefusal(call(PAIRED, "GET", "/api/v1/board/lists", null),
                404, null, "Its paths are /api/v1/board, /api/v1/board/items");
        assertRefusal(call(PAIRED, "GET", ITEMS + "/a/b", null),
                404, null, "Its paths are");
        assertRefusal(call(PAIRED, "POST", ITEMS + "/a", "{\"title\": \"T\"}"),
                405, null, "POST is not available here. This path takes GET, PUT, PATCH or DELETE");
        assertRefusal(call(PAIRED, "PUT", ITEMS, "{\"title\": \"T\"}"),
                405, null, "This path takes GET, POST or DELETE");
        assertRefusal(call(PAIRED, "DELETE", "/api/v1/board", null),
                405, null, "This path takes GET");
        assertEquals(0, items.size());
    }

    @Test
    public void aFullBoardAnswersConflict() throws JSONException {
        for (int index = 0; index < BoardItems.MAX_ITEMS; index++) {
            assertEquals(201, call(PAIRED, "POST", ITEMS, "{\"title\": \"T\"}").status);
        }
        assertRefusal(call(PAIRED, "POST", ITEMS, "{\"title\": \"T\"}"), 409, null, "at most 100");
    }

    private static void assertRefusal(BoardApi.Reply reply, int status, String field, String text)
            throws JSONException {
        assertEquals(reply.body.toString(), status, reply.status);
        assertEquals(reply.body.toString(), field, reply.body.optString("field", null));
        assertTrue(reply.body.toString(), reply.body.getString("error").contains(text));
        assertEquals("/api/v1/board/guide", reply.body.getString("guide"));
    }

    private BoardApi.Reply call(BoardApi.Caller caller, String method, String target, String json) {
        return call(caller, method, target, json, NOW);
    }

    private BoardApi.Reply call(
            BoardApi.Caller caller, String method, String target, String json, long now) {
        int mark = target.indexOf('?');
        String uri = mark < 0 ? target : target.substring(0, mark);
        return api.handle(
                method, uri, query(mark < 0 ? "" : target.substring(mark + 1)), body(json), caller,
                null, now);
    }

    private BoardApi.BodyReader body(final String json) {
        return new BoardApi.BodyReader() {
            @Override
            public JSONObject read() throws IOException, JSONException {
                bodyRead = true;
                if (json == null) {
                    throw new IOException("Content-Length is required");
                }
                return new JSONObject(json);
            }
        };
    }

    static Map<String, String> query(String text) {
        Map<String, String> query = new HashMap<>();
        for (String pair : text.split("&")) {
            int equals = pair.indexOf('=');
            if (equals > 0) {
                query.put(pair.substring(0, equals), pair.substring(equals + 1));
            }
        }
        return query;
    }

    private static String ids(JSONArray items) throws JSONException {
        StringBuilder ids = new StringBuilder();
        for (int index = 0; index < items.length(); index++) {
            ids.append(index > 0 ? " " : "").append(items.getJSONObject(index).getString("id"));
        }
        return ids.toString();
    }
}
