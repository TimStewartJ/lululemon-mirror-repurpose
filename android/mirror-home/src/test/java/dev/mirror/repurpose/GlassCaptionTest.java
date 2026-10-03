package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.json.JSONArray;
import org.junit.Test;

import java.util.List;

public class GlassCaptionTest {
    private static String letters(int count) {
        return new String(new char[count]).replace('\0', 'a');
    }

    @Test
    public void rowsAreReadAsLabelAndText() throws Exception {
        List<GlassCaption.Row> rows = GlassCaption.rows(new JSONArray(
                "[{\"label\":\" Weather \",\"text\":\" 72\u00b0 and clear \"},{\"text\":\"No label\"},"
                        + "{\"label\":null,\"text\":\"Nor here\"}]"), true);
        assertEquals(3, rows.size());
        assertEquals("Weather", rows.get(0).label);
        assertEquals("72\u00b0 and clear", rows.get(0).text);
        assertEquals("", rows.get(1).label);
        assertEquals("", rows.get(2).label);
    }

    @Test
    public void noRowsAreNoRows() throws Exception {
        assertTrue(GlassCaption.rows(null, true).isEmpty());
        assertTrue(GlassCaption.rows(new JSONArray(), true).isEmpty());
    }

    @Test
    public void whoeverSendsRowsToBeShownIsToldWhatIsWrongWithThem() throws Exception {
        String[] broken = {
                "[{\"label\":\"a\",\"text\":\"\"}]",
                "[{\"label\":\"a\"}]",
                "[{\"label\":\"a\",\"text\":7}]",
                "[{\"label\":7,\"text\":\"b\"}]",
                "[\"words\"]",
                "[{\"label\":\"" + letters(GlassCaption.MAX_LABEL + 1) + "\",\"text\":\"b\"}]",
                "[{\"label\":\"a\",\"text\":\"" + letters(GlassCaption.MAX_ROW_TEXT + 1) + "\"}]",
                "[{\"label\":\"a\",\"text\":\"two\\nlines\"}]",
        };
        for (String rows : broken) {
            try {
                GlassCaption.rows(new JSONArray(rows), true);
                fail(rows);
            } catch (IllegalArgumentException expected) {
                assertTrue(expected.getMessage(), expected.getMessage().contains("row"));
            }
        }
        JSONArray many = new JSONArray();
        for (int row = 0; row <= GlassCaption.MAX_ROWS; row++) {
            many.put(new org.json.JSONObject().put("label", "a").put("text", "b"));
        }
        try {
            GlassCaption.rows(many, true);
            fail("too many rows");
        } catch (IllegalArgumentException expected) {
            assertTrue(expected.getMessage().contains("at most " + GlassCaption.MAX_ROWS));
        }
    }

    @Test
    public void anAnswerWithRowsThatAreTooMuchIsCutAndShown() throws Exception {
        // A companion's answer is worth more cut short than not shown at all.
        JSONArray sent = new JSONArray("[{\"label\":\"" + letters(30) + "\",\"text\":\"" + letters(200) + "\"},"
                + "{\"label\":\"a\",\"text\":\"\"},{\"label\":4,\"text\":\"two\\nlines\"},7]");
        for (int row = 0; row < 8; row++) {
            sent.put(new org.json.JSONObject().put("label", "n").put("text", "row " + row));
        }
        List<GlassCaption.Row> rows = GlassCaption.rows(sent, false);
        assertEquals(GlassCaption.MAX_ROWS, rows.size());
        assertEquals(GlassCaption.MAX_LABEL, rows.get(0).label.length());
        assertEquals(GlassCaption.MAX_ROW_TEXT, rows.get(0).text.length());
        assertTrue(rows.get(0).text.endsWith("\u2026"));
        assertEquals("", rows.get(1).label);
        assertEquals("two lines", rows.get(1).text);
        assertEquals("row 0", rows.get(2).text);
    }
}
