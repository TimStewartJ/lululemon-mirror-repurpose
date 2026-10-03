package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

import java.util.List;

public final class BoardItemsTest {
    private static final long NOW = 1790960400000L; // 2026-10-02T17:00:00Z
    private static final long MINUTE = 60_000L;
    private static final long HOUR = 60 * MINUTE;
    private static final long DAY = 24 * HOUR;

    @Test
    public void aTitleIsEnoughAndTheRestIsFilledIn() throws JSONException {
        BoardItems board = new BoardItems();

        JSONObject item = board.create(json("{'title': '  Dinner is in the oven  '}"), "Kitchen", NOW);

        assertTrue(BoardItems.validId(item.getString("id")));
        assertEquals("note", item.getString("kind"));
        assertEquals("Dinner is in the oven", item.getString("title"));
        assertEquals("", item.getString("body"));
        assertTrue(item.isNull("due"));
        assertTrue(item.isNull("dueIso"));
        assertEquals("2026-10-03T17:00:00Z", item.getString("expiresAtIso"));
        assertFalse(item.getBoolean("done"));
        assertTrue(item.isNull("doneAt"));
        assertEquals("normal", item.getString("priority"));
        assertEquals(NOW + DAY, item.getLong("expiresAt"));
        assertEquals("Kitchen", item.getString("source"));
        assertEquals(NOW, item.getLong("createdAt"));
        assertEquals(NOW, item.getLong("updatedAt"));
        assertEquals("open", item.getString("state"));
        assertTrue(item.getBoolean("showing"));
        assertFalse(item.has("autoExpiry"));
        assertEquals(1, board.version());
    }

    @Test
    public void everyFieldIsKeptAsGiven() throws JSONException {
        BoardItems board = new BoardItems();

        JSONObject item = board.create(
                json("{'kind': 'reminder', 'title': 'Leave for the dentist',"
                        + " 'body': 'Bring the card\\r\\nand the form', 'due': '2026-10-03T09:00:00-07:00',"
                        + " 'priority': 'high', 'expiresAt': '2026-10-03T18:00:00Z', 'source': 'calendar'}"),
                "Kitchen",
                NOW);

        assertEquals("reminder", item.getString("kind"));
        assertEquals("Bring the card\nand the form", item.getString("body"));
        assertEquals(1791043200000L, item.getLong("due"));
        assertEquals("2026-10-03T16:00:00Z", item.getString("dueIso"));
        assertEquals("high", item.getString("priority"));
        assertEquals(1791050400000L, item.getLong("expiresAt"));
        assertEquals("calendar", item.getString("source"));
    }

    @Test
    public void itemsLeaveByThemselves() throws JSONException {
        BoardItems board = new BoardItems();
        String note = board.create(json("{'title': 'Note'}"), "a", NOW).getString("id");
        String brief = board.create(json("{'title': 'Brief', 'ttlSeconds': 90}"), "a", NOW)
                .getString("id");
        String kept = board.create(json("{'title': 'Kept', 'expiresAt': null}"), "a", NOW)
                .getString("id");
        String never = board.create(json("{'title': 'Never', 'expiresAt': 'never'}"), "a", NOW)
                .getString("id");
        long version = board.version();

        assertEquals(NOW + 90_000, board.find(brief, NOW).getLong("expiresAt"));
        assertTrue(board.find(kept, NOW).isNull("expiresAt"));
        assertTrue(board.find(kept, NOW).isNull("expiresAtIso"));
        assertTrue(board.find(never, NOW).isNull("expiresAt"));

        assertNull(board.find(brief, NOW + 90_000));
        assertTrue(board.version() > version);
        assertEquals(3, board.size());
        assertNull(board.find(note, NOW + DAY));
        assertEquals(2, board.list(null, NOW + 400 * DAY).size());
    }

    @Test
    public void aReminderOutlivesItsMomentByADay() throws JSONException {
        BoardItems board = new BoardItems();
        long due = NOW + 3 * DAY;

        JSONObject item = board.create(
                json("{'kind': 'reminder', 'title': 'Bins'}").put("due", due), "a", NOW);

        assertEquals(due + DAY, item.getLong("expiresAt"));
    }

    @Test
    public void writingAgainRenewsTheDefaultLifetimeOnly() throws JSONException {
        BoardItems board = new BoardItems();
        board.put("auto", json("{'kind': 'todo', 'title': 'Plants'}"), "a", NOW);
        board.put("fixed", json("{'kind': 'todo', 'title': 'Bins', 'ttlSeconds': 7200}"), "a", NOW);
        long later = NOW + HOUR;

        JSONObject auto = board.patch("auto", json("{'done': true}"), later);
        JSONObject fixed = board.patch("fixed", json("{'done': true}"), later);

        assertEquals(later + DAY, auto.getLong("expiresAt"));
        assertEquals(NOW + 2 * HOUR, fixed.getLong("expiresAt"));

        JSONObject moved = board.patch("auto", new JSONObject().put("due", later + 5 * DAY), later);
        assertEquals(later + 6 * DAY, moved.getLong("expiresAt"));
    }

    @Test
    public void putCreatesThenReplacesWhole() throws JSONException {
        BoardItems board = new BoardItems();

        BoardItems.Saved first = board.put(
                "tether.task-12",
                json("{'kind': 'todo', 'title': 'Call Mom', 'body': 'After dinner',"
                        + " 'priority': 'high', 'done': true}"),
                "Tether",
                NOW);
        BoardItems.Saved second = board.put(
                "tether.task-12", json("{'kind': 'todo', 'title': 'Call Mum'}"), "Tether", NOW + HOUR);

        assertTrue(first.created);
        assertEquals(NOW, first.item.getLong("doneAt"));
        assertFalse(second.created);
        assertEquals(1, board.size());
        assertEquals("Call Mum", second.item.getString("title"));
        assertEquals("", second.item.getString("body"));
        assertEquals("normal", second.item.getString("priority"));
        assertFalse(second.item.getBoolean("done"));
        assertTrue(second.item.isNull("doneAt"));
        assertEquals(NOW, second.item.getLong("createdAt"));
        assertEquals(NOW + HOUR, second.item.getLong("updatedAt"));
    }

    @Test
    public void whatWasReadCanBeWrittenBackUnchanged() throws JSONException {
        BoardItems board = new BoardItems();
        board.put(
                "a",
                json("{'kind': 'todo', 'title': 'Plants', 'body': 'Not the cactus', 'done': true,"
                        + " 'due': '2026-10-03T09:00:00-07:00', 'priority': 'low'}"),
                "Kitchen",
                NOW);
        JSONObject read = board.find("a", NOW + MINUTE);

        JSONObject written = board.put("a", read, "Someone else", NOW + 2 * MINUTE).item;

        for (String field : new String[] {
                "id", "kind", "title", "body", "due", "done", "doneAt", "priority", "expiresAt",
                "source", "createdAt"}) {
            assertEquals(field, String.valueOf(read.get(field)), String.valueOf(written.get(field)));
        }
    }

    @Test
    public void patchChangesOnlyWhatItNames() throws JSONException {
        BoardItems board = new BoardItems();
        board.put(
                "a",
                json("{'kind': 'todo', 'title': 'Plants', 'body': 'Not the cactus',"
                        + " 'priority': 'high', 'source': 'garden'}"),
                "Kitchen",
                NOW);

        JSONObject done = board.patch("a", json("{'done': true}"), NOW + MINUTE);
        JSONObject renamed = board.patch("a", json("{'title': 'Water plants'}"), NOW + 2 * MINUTE);
        JSONObject reopened = board.patch("a", json("{'done': false, 'body': null}"), NOW + HOUR);

        assertEquals("Plants", done.getString("title"));
        assertEquals("Not the cactus", done.getString("body"));
        assertEquals("high", done.getString("priority"));
        assertEquals("garden", done.getString("source"));
        assertEquals("done", done.getString("state"));
        assertEquals(NOW + MINUTE, done.getLong("doneAt"));
        assertEquals("Water plants", renamed.getString("title"));
        assertEquals(NOW + MINUTE, renamed.getLong("doneAt"));
        assertFalse(reopened.getBoolean("done"));
        assertTrue(reopened.isNull("doneAt"));
        assertEquals("", reopened.getString("body"));
        assertNull(board.patch("missing", json("{'done': true}"), NOW));
    }

    @Test
    public void stateFollowsTheClock() throws JSONException {
        BoardItems board = new BoardItems();
        board.put(
                "r",
                json("{'kind': 'reminder', 'title': 'Call', 'expiresAt': null}")
                        .put("due", NOW + 2 * HOUR),
                "a",
                NOW);

        assertEquals("open", board.find("r", NOW).getString("state"));
        assertEquals("open", board.find("r", NOW + HOUR - 1).getString("state"));
        assertEquals("soon", board.find("r", NOW + HOUR).getString("state"));
        assertEquals("soon", board.find("r", NOW + 2 * HOUR - 1).getString("state"));
        assertEquals("overdue", board.find("r", NOW + 2 * HOUR).getString("state"));

        board.patch("r", json("{'done': true}"), NOW + 3 * HOUR);
        assertEquals("done", board.find("r", NOW + 3 * HOUR).getString("state"));
    }

    @Test
    public void aDoneItemLeavesTheGlassButNotTheBoard() throws JSONException {
        BoardItems board = new BoardItems();
        board.put("t", json("{'kind': 'todo', 'title': 'Bins', 'expiresAt': null}"), "a", NOW);
        board.put("n", json("{'title': 'Note', 'expiresAt': null}"), "a", NOW);
        board.patch("t", json("{'done': true}"), NOW);
        long lingered = NOW + BoardItems.DONE_LINGER_MS;

        assertTrue(board.find("t", lingered - 1).getBoolean("showing"));
        assertEquals(2, board.showing(lingered - 1).size());
        assertFalse(board.find("t", lingered).getBoolean("showing"));
        assertEquals(1, board.showing(lingered).size());
        assertEquals(2, board.list(null, lingered).size());

        JSONObject counts = board.counts(lingered);
        assertEquals(2, counts.getInt("total"));
        assertEquals(1, counts.getInt("showing"));
        assertEquals(1, counts.getInt("open"));
        assertEquals(1, counts.getInt("done"));
        assertEquals(0, counts.getInt("overdue"));
        assertEquals(0, counts.getInt("soon"));
    }

    @Test
    public void listsWhatNeedsAttentionFirst() throws JSONException {
        BoardItems board = new BoardItems();
        board.put("plain-1", json("{'title': 'Plain one'}"), "a", NOW);
        board.put("low", json("{'title': 'Low', 'priority': 'low'}"), "a", NOW + 1);
        board.put("done", json("{'kind': 'todo', 'title': 'Done', 'done': true, 'priority': 'high'}"),
                "a", NOW + 2);
        board.put("later", json("{'kind': 'todo', 'title': 'Later'}").put("due", NOW + 5 * HOUR),
                "a", NOW + 3);
        board.put("soon", json("{'kind': 'reminder', 'title': 'Soon'}").put("due", NOW + HOUR / 2),
                "a", NOW + 4);
        board.put("high", json("{'title': 'High', 'priority': 'high'}"), "a", NOW + 5);
        board.put("overdue", json("{'kind': 'todo', 'title': 'Overdue'}").put("due", NOW - HOUR),
                "a", NOW + 6);
        board.put("overdue-high",
                json("{'kind': 'todo', 'title': 'Overdue, high', 'priority': 'high'}")
                        .put("due", NOW - MINUTE),
                "a", NOW + 7);
        board.put("plain-2", json("{'title': 'Plain two'}"), "a", NOW + 8);

        assertEquals(
                "overdue-high overdue soon high later plain-1 plain-2 low done",
                ids(board.list(null, NOW + 9)));
    }

    @Test
    public void filtersAndBulkRemoval() throws JSONException {
        BoardItems board = new BoardItems();
        board.put("a", json("{'kind': 'todo', 'title': 'A', 'source': 'tether'}"), "x", NOW);
        board.put("b", json("{'kind': 'todo', 'title': 'B', 'source': 'tether', 'done': true}"),
                "x", NOW + 1);
        board.put("c", json("{'title': 'C', 'source': 'calendar'}"), "x", NOW + 2);

        assertEquals("a b", ids(board.list(new BoardItems.Filter("todo", null, null), NOW + 3)));
        assertEquals("c", ids(board.list(new BoardItems.Filter(null, "calendar", null), NOW + 3)));
        assertEquals("a c", ids(board.list(new BoardItems.Filter(null, null, false), NOW + 3)));
        assertEquals("b", ids(board.list(new BoardItems.Filter("todo", "tether", true), NOW + 3)));

        long version = board.version();
        assertEquals(0, board.deleteMatching(new BoardItems.Filter("reminder", null, null), NOW + 3));
        assertEquals(version, board.version());
        assertEquals(2, board.deleteMatching(new BoardItems.Filter(null, "tether", null), NOW + 3));
        assertEquals("c", ids(board.list(null, NOW + 3)));
        assertTrue(board.delete("c", NOW + 3));
        assertFalse(board.delete("c", NOW + 3));
        assertEquals(1, board.deleteMatching(
                new BoardItems.Filter(null, null, null), reseeded(board)));
    }

    @Test
    public void kindsHaveTheirOwnRules() {
        expectRefusal("due", "A note has no due time",
                "{'title': 'N', 'due': '2026-10-03T09:00:00Z'}");
        expectRefusal("done", "A note cannot be done", "{'title': 'N', 'done': true}");
        expectRefusal("due", "A reminder needs due", "{'kind': 'reminder', 'title': 'R'}");
        expectRefusal("due", "A reminder needs due",
                "{'kind': 'reminder', 'title': 'R', 'due': null}");
    }

    @Test
    public void patchCannotBreakAKindsRules() throws JSONException {
        BoardItems board = new BoardItems();
        board.put("r", json("{'kind': 'reminder', 'title': 'R'}").put("due", NOW + HOUR), "a", NOW);

        for (String change : new String[] {"{'due': null}", "{'kind': 'note'}"}) {
            try {
                board.patch("r", json(change), NOW);
                fail("Expected a refusal of " + change);
            } catch (BoardError refusal) {
                assertEquals("due", refusal.field);
            }
        }
        assertEquals("todo", board.patch("r", json("{'kind': 'todo', 'due': null}"), NOW)
                .getString("kind"));
    }

    @Test
    public void refusalsNameTheFieldAndTheRemedy() {
        expectRefusal("title", "title is required", "{'kind': 'todo'}");
        expectRefusal("title", "cannot be empty", "{'title': '   '}");
        expectRefusal("title", "must be text", "{'title': 12}");
        expectRefusal("title", "one line", "{'title': 'Two\\nlines'}");
        expectRefusal("title", "up to 120", "{'title': '" + repeat('x', 121) + "'}");
        expectRefusal("body", "up to 500", "{'title': 'T', 'body': '" + repeat('x', 501) + "'}");
        expectRefusal("body", "control characters", "{'title': 'T', 'body': 'Tab\\there'}");
        expectRefusal("kind", "note, todo or reminder", "{'title': 'T', 'kind': 'task'}");
        expectRefusal("priority", "low, normal or high", "{'title': 'T', 'priority': 'urgent'}");
        expectRefusal("priority", "low, normal or high", "{'title': 'T', 'priority': 1}");
        expectRefusal("done", "true or false", "{'kind': 'todo', 'title': 'T', 'done': 'yes'}");
        expectRefusal("source", "short name", "{'title': 'T', 'source': '" + repeat('s', 41) + "'}");
        expectRefusal("text", "Unknown field \"text\". An item takes kind, title",
                "{'text': 'Buy milk'}");
        expectRefusal("ttlSeconds", "not both",
                "{'title': 'T', 'ttlSeconds': 60, 'expiresAt': null}");
        expectRefusal("ttlSeconds", "from 1 to", "{'title': 'T', 'ttlSeconds': 0}");
        expectRefusal("ttlSeconds", "from 1 to", "{'title': 'T', 'ttlSeconds': 1.5}");
        expectRefusal("ttlSeconds", "from 1 to", "{'title': 'T', 'ttlSeconds': 'soon'}");
        expectRefusal("expiresAt", "already past; the Mirror's clock reads 2026-10-02T17:00:00Z",
                "{'title': 'T', 'expiresAt': '2026-10-02T16:59:59Z'}");
        expectRefusal("expiresAt", "offset", "{'title': 'T', 'expiresAt': '2026-10-04T09:00'}");
        expectRefusal("due", "looks like seconds",
                "{'kind': 'todo', 'title': 'T', 'due': 1791043200}");
        expectRefusal("id", "POST picks the id", "{'id': 'mine', 'title': 'T'}");
    }

    @Test
    public void idsChosenByTheCallerAreChecked() throws JSONException {
        BoardItems board = new BoardItems();
        JSONObject item = json("{'title': 'T'}");

        for (String id : new String[] {"a", "Task_12.b-c", repeat('a', 64), "0"}) {
            assertTrue(id, board.put(id, item, "a", NOW).created);
        }
        for (String id : new String[] {"", "-a", ".a", "a b", "a/b", "é", repeat('a', 65)}) {
            try {
                board.put(id, item, "a", NOW);
                fail("Expected a refusal of id " + id);
            } catch (BoardError refusal) {
                assertEquals("id", refusal.field);
            }
        }
        try {
            board.put("a", json("{'id': 'b', 'title': 'T'}"), "a", NOW);
            fail("Expected a refusal of a mismatched id");
        } catch (BoardError refusal) {
            assertTrue(refusal.getMessage().contains("not the id in the path"));
        }
    }

    @Test
    public void aFullBoardSaysHowToMakeRoom() throws JSONException {
        BoardItems board = new BoardItems();
        board.put("brief", json("{'title': 'Brief', 'ttlSeconds': 60}"), "a", NOW);
        board.put("lasting", json("{'title': 'Lasting'}"), "a", NOW);
        while (board.size() < BoardItems.MAX_ITEMS) {
            board.create(json("{'title': 'Item'}"), "a", NOW);
        }

        try {
            board.create(json("{'title': 'One too many'}"), "a", NOW);
            fail("Expected a full board");
        } catch (BoardError refusal) {
            assertEquals(409, refusal.status);
            assertTrue(refusal.getMessage().contains("at most 100 items"));
            assertTrue(refusal.getMessage().contains("DELETE /api/v1/board/items?done=true"));
        }
        // Replacing needs no room, and an expired item makes some.
        assertFalse(board.put("lasting", json("{'title': 'Same'}"), "a", NOW).created);
        assertNotEquals("", board.create(json("{'title': 'Fits now'}"), "a", NOW + 61_000)
                .getString("id"));
    }

    @Test
    public void theSourceDefaultsToWhoeverIsAsking() throws JSONException {
        BoardItems board = new BoardItems();

        assertEquals("Kitchen iPad",
                board.create(json("{'title': 'T'}"), " Kitchen iPad ", NOW).getString("source"));
        assertEquals("unknown", board.create(json("{'title': 'T'}"), null, NOW).getString("source"));
        assertEquals("unknown", board.create(json("{'title': 'T'}"), "  ", NOW).getString("source"));
        assertEquals("Kitchen",
                board.create(json("{'title': 'T', 'source': null}"), "Kitchen", NOW)
                        .getString("source"));
        assertEquals(40, board.create(json("{'title': 'T'}"), repeat('n', 60), NOW)
                .getString("source").length());
    }

    @Test
    public void survivesARestart() throws JSONException {
        BoardItems board = new BoardItems();
        board.put("a", json("{'kind': 'todo', 'title': 'Plants', 'done': true}"), "Kitchen", NOW);
        board.put("b", json("{'title': 'Kept', 'expiresAt': null}"), "Kitchen", NOW);
        assertTrue(board.takeUnsaved());
        assertFalse(board.takeUnsaved());

        BoardItems restored = BoardItems.parse(board.serialize());

        assertEquals(board.version(), restored.version());
        assertFalse(restored.takeUnsaved());
        assertEquals(
                board.list(null, NOW + MINUTE).toString(), restored.list(null, NOW + MINUTE).toString());
        // The renewing lifetime is remembered too.
        assertEquals(NOW + HOUR + DAY,
                restored.patch("a", json("{'done': false}"), NOW + HOUR).getLong("expiresAt"));
        assertTrue(restored.patch("b", json("{'title': 'Still kept'}"), NOW + HOUR).isNull("expiresAt"));
    }

    @Test
    public void aDamagedStoreYieldsWhatCanBeRead() {
        assertEquals(0, BoardItems.parse("not json").size());
        assertEquals(0, BoardItems.parse("").size());
        assertEquals(0, BoardItems.parse(null).size());

        BoardItems board = BoardItems.parse("{\"version\": 7, \"items\": ["
                + "{\"id\": \"ok\", \"kind\": \"note\", \"title\": \"Fine\", \"priority\": \"normal\"},"
                + "{\"id\": \"ok\", \"kind\": \"note\", \"title\": \"Twice\", \"priority\": \"normal\"},"
                + "{\"id\": \"bad id\", \"kind\": \"note\", \"title\": \"x\", \"priority\": \"normal\"},"
                + "{\"id\": \"k\", \"kind\": \"task\", \"title\": \"x\", \"priority\": \"normal\"},"
                + "{\"id\": \"t\", \"kind\": \"note\", \"title\": \" \", \"priority\": \"normal\"},"
                + "\"junk\"]}");

        assertEquals(1, board.size());
        assertEquals(7, board.version());
        assertEquals("Fine", board.find("ok", NOW).optString("title"));
    }

    private static long reseeded(BoardItems board) throws JSONException {
        board.create(json("{'title': 'Seed'}"), "a", NOW + 4);
        return NOW + 5;
    }

    private static void expectRefusal(String field, String expected, String request) {
        try {
            new BoardItems().create(json(request), "a", NOW);
            fail("Expected a refusal of " + request);
        } catch (BoardError refusal) {
            assertEquals(request, 400, refusal.status);
            assertEquals(request, field, refusal.field);
            assertTrue(refusal.getMessage(), refusal.getMessage().contains(expected));
        }
    }

    private static String ids(List<JSONObject> items) {
        StringBuilder ids = new StringBuilder();
        for (JSONObject item : items) {
            ids.append(ids.length() > 0 ? " " : "").append(item.optString("id"));
        }
        return ids.toString();
    }

    private static String repeat(char character, int count) {
        StringBuilder builder = new StringBuilder();
        for (int index = 0; index < count; index++) {
            builder.append(character);
        }
        return builder.toString();
    }

    static JSONObject json(String text) {
        try {
            return new JSONObject(text.replace('\'', '"'));
        } catch (JSONException error) {
            throw new IllegalArgumentException(text, error);
        }
    }
}
