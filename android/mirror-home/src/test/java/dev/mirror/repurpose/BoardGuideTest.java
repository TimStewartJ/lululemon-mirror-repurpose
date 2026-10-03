package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

import java.util.HashMap;
import java.util.Map;

/* The guide is the documentation a program meets first, so it is held to the
   running code: its examples are sent, its requests are routed, and its
   fields are the ones the rules accept. */
public final class BoardGuideTest {
    private static final long NOW = 1790960400000L; // 2026-10-02T17:00:00Z
    private static final BoardApi.Caller PAIRED = new BoardApi.Caller(true, false, "YOUR-NAME");
    private static final BoardApi.Caller STRANGER = new BoardApi.Caller(false, false, null);

    @Test
    public void everyExampleWorksAsWritten() throws JSONException {
        BoardItems items = new BoardItems();
        BoardApi api = new BoardApi(items);
        JSONArray examples = BoardGuide.build().getJSONArray("examples");
        assertTrue(examples.length() >= 5);

        for (int index = 0; index < examples.length(); index++) {
            JSONObject example = examples.getJSONObject(index);
            BoardApi.Reply reply = send(api, PAIRED, example.getString("method"),
                    example.getString("path"), example.optJSONObject("body"));
            assertTrue(
                    example.getString("does") + ": " + reply.body,
                    reply.status == 200 || reply.status == 201);
            assertFalse(example.getString("does").isEmpty());
        }
        // The last example clears what the others posted.
        assertEquals(0, items.size());
    }

    @Test
    public void everyRequestItListsIsRouted() throws JSONException {
        JSONArray requests = BoardGuide.build().getJSONArray("requests");
        Map<String, Boolean> seen = new HashMap<>();

        for (int index = 0; index < requests.length(); index++) {
            JSONObject request = requests.getJSONObject(index);
            String method = request.getString("method");
            String path = request.getString("path").replace("{id}", "example");
            BoardApi api = new BoardApi(new BoardItems());
            send(api, PAIRED, "PUT", "/api/v1/board/items/example", title());

            int paired = send(api, PAIRED, method, path, title()).status;
            int stranger = send(api, STRANGER, method, path, title()).status;

            String name = method + " " + request.getString("path");
            assertTrue(name + " answered " + paired, paired != 401 && paired != 404 && paired != 405);
            assertEquals(name, request.getBoolean("needsToken") ? 401 : 200, stranger);
            assertFalse(name, request.getString("does").isEmpty());
            seen.put(name, true);
        }
        for (String name : new String[] {
                "GET /api/v1/board/guide", "GET /api/v1/board", "GET /api/v1/board/items",
                "POST /api/v1/board/items", "DELETE /api/v1/board/items",
                "GET /api/v1/board/items/{id}", "PUT /api/v1/board/items/{id}",
                "PATCH /api/v1/board/items/{id}", "DELETE /api/v1/board/items/{id}"}) {
            assertTrue(name + " is missing from the guide", seen.containsKey(name));
        }
        assertEquals(9, seen.size());
    }

    @Test
    public void describesExactlyTheFieldsTheRulesKnow() throws JSONException {
        JSONArray fields = BoardGuide.build().getJSONArray("item");
        Map<String, JSONObject> described = new HashMap<>();
        for (int index = 0; index < fields.length(); index++) {
            JSONObject field = fields.getJSONObject(index);
            assertFalse(field.getString("meaning").isEmpty());
            assertFalse(field.getString("type").isEmpty());
            described.put(field.getString("name"), field);
        }

        for (String name : BoardItems.WRITABLE_FIELDS) {
            assertNotNull(name, described.get(name));
            assertTrue(name, described.get(name).getBoolean("writable"));
        }
        for (String name : BoardItems.READ_ONLY_FIELDS) {
            assertNotNull(name, described.get(name));
            assertFalse(name, described.get(name).getBoolean("writable"));
        }
        assertEquals(
                BoardItems.WRITABLE_FIELDS.length + BoardItems.READ_ONLY_FIELDS.length,
                described.size());

        // What an item looks like when read is what the guide describes, less the write-only one.
        JSONObject item = new BoardItems().create(title(), "a", NOW);
        assertEquals(described.size() - 1, item.length());
        for (java.util.Iterator<String> names = item.keys(); names.hasNext(); ) {
            String name = names.next();
            assertNotNull(name, described.get(name));
        }
        assertFalse(item.has("ttlSeconds"));
    }

    @Test
    public void namesEveryKindAndStateAndTheRealLimits() throws JSONException {
        JSONObject guide = BoardGuide.build();

        assertEquals(BoardItems.KINDS.length, guide.getJSONObject("kinds").length());
        for (String kind : BoardItems.KINDS) {
            assertFalse(guide.getJSONObject("kinds").getString(kind).isEmpty());
        }
        assertEquals(BoardItems.STATES.length, guide.getJSONObject("states").length());
        for (String state : BoardItems.STATES) {
            assertFalse(guide.getJSONObject("states").getString(state).isEmpty());
        }
        JSONObject limits = guide.getJSONObject("limits");
        assertEquals(100, limits.getInt("items"));
        assertEquals(120, limits.getInt("titleCharacters"));
        assertEquals(500, limits.getInt("bodyCharacters"));
        assertTrue(guide.getJSONArray("lifetime").getString(0).contains("24 hours"));
        assertTrue(guide.getJSONArray("lifetime").getString(3).contains("10 minutes"));
        assertTrue(guide.getJSONObject("states").getString("soon").contains("60 minutes"));
        for (String section : new String[] {"board", "order", "changes", "errors"}) {
            assertFalse(section, guide.getString(section).isEmpty());
        }
        assertTrue(guide.getJSONArray("start").getString(0).contains("POST /api/v1/pair"));
        assertTrue(guide.getJSONArray("glass").length() > 0);
    }

    private static JSONObject title() throws JSONException {
        return new JSONObject().put("title", "Water the plants");
    }

    private static BoardApi.Reply send(
            BoardApi api, BoardApi.Caller caller, String method, String target, final JSONObject body) {
        int mark = target.indexOf('?');
        return api.handle(
                method,
                mark < 0 ? target : target.substring(0, mark),
                BoardApiTest.query(mark < 0 ? "" : target.substring(mark + 1)),
                new BoardApi.BodyReader() {
                    @Override
                    public JSONObject read() {
                        return body == null ? new JSONObject() : body;
                    }
                },
                caller,
                null,
                NOW);
    }
}
