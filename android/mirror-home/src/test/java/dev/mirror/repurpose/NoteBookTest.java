package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.json.JSONObject;
import org.junit.Test;

public final class NoteBookTest {
    @Test
    public void addsNewestFirstAndRoundTrips() {
        NoteBook book = new NoteBook();
        JSONObject first = book.add("Buy milk", 1000);
        JSONObject second = book.add("  Call Mom\r\nafter dinner  ", 2000);

        assertEquals(2, book.size());
        assertEquals(second.optString("id"), book.toJson().optJSONObject(0).optString("id"));
        assertEquals("Call Mom\nafter dinner", second.optString("text"));
        assertEquals(2000, second.optLong("updatedAt"));
        assertNotEquals(first.optString("id"), second.optString("id"));
        assertTrue(NoteBook.validId(first.optString("id")));

        NoteBook restored = NoteBook.parse(book.serialize());
        assertEquals(2, restored.size());
        assertEquals("Buy milk", restored.find(first.optString("id")).optString("text"));
    }

    @Test
    public void updatesAndDeletesById() {
        NoteBook book = new NoteBook();
        String id = book.add("Draft", 10).optString("id");

        JSONObject updated = book.update(id, "Final", 20);
        assertEquals("Final", updated.optString("text"));
        assertEquals(10, updated.optLong("createdAt"));
        assertEquals(20, updated.optLong("updatedAt"));
        assertNull(book.update("missing", "Nope", 30));

        assertTrue(book.delete(id));
        assertFalse(book.delete(id));
        assertEquals(0, book.size());
    }

    @Test
    public void rejectsEmptyOversizedAndControlText() {
        NoteBook book = new NoteBook();
        expectRejection(book, null);
        expectRejection(book, "   \n  ");
        expectRejection(book, "Tab\tcharacters");
        StringBuilder oversized = new StringBuilder();
        for (int index = 0; index <= NoteBook.MAX_TEXT_LENGTH; index++) {
            oversized.append('x');
        }
        expectRejection(book, oversized.toString());
        assertEquals(
                NoteBook.MAX_TEXT_LENGTH,
                book.add(oversized.substring(1), 1).optString("text").length());
    }

    @Test
    public void capsTheNumberOfNotes() {
        NoteBook book = new NoteBook();
        for (int index = 0; index < NoteBook.MAX_NOTES; index++) {
            book.add("Note " + index, index);
        }
        expectRejection(book, "One too many");
        assertEquals(NoteBook.MAX_NOTES, book.size());
    }

    @Test
    public void parseSkipsCorruptEntries() {
        NoteBook book = NoteBook.parse("[{\"id\":\"ok1\",\"text\":\"Fine\",\"createdAt\":5},"
                + "{\"id\":\"Bad Id\",\"text\":\"x\"},"
                + "{\"id\":\"ok2\",\"text\":\"\"},"
                + "\"junk\"]");
        assertEquals(1, book.size());
        assertEquals(5, book.find("ok1").optLong("updatedAt"));
        assertEquals(0, NoteBook.parse("not json").size());
        assertEquals(0, NoteBook.parse("").size());
    }

    private static void expectRejection(NoteBook book, String text) {
        try {
            book.add(text, 1);
            fail("Expected rejection of: " + text);
        } catch (IllegalArgumentException expected) {
            // The rule fired.
        }
    }
}
