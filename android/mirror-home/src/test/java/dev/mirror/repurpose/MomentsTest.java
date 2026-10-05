package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Before;
import org.junit.Test;

public class MomentsTest {
    private static final long NOW = 1_791_000_000_000L;
    private final Moments moments = Moments.getInstance();

    @Before
    public void emptyGlass() {
        moments.clear();
    }

    private static JSONObject box(String kind) throws Exception {
        return new JSONObject().put("kind", kind);
    }

    private static JSONObject words(String id, String text) throws Exception {
        return box("text").put("id", id).put("text", text);
    }

    private void refused(JSONObject body, String field) throws Exception {
        long before = moments.version(NOW);
        try {
            moments.put(body, NOW);
            fail("taken: " + body);
        } catch (Moments.Refusal refusal) {
            assertEquals(refusal.getMessage(), field, refusal.field);
        }
        assertEquals("a refused moment changes nothing", before, moments.version(NOW));
    }

    @Test
    public void aMomentStaysForItsSecondsAndThenIsGone() throws Exception {
        long before = moments.version(NOW);
        JSONObject taken = moments.put(words("hello", "Good luck today").put("seconds", 30).put("title", "For Sam"), NOW);
        assertEquals("hello", taken.getString("id"));
        assertEquals(NOW + 30_000L, taken.getLong("until"));
        assertEquals("none", taken.getString("motion"));
        // Where it goes is for the glass, unless a height or a side is named.
        assertEquals("medium", taken.getString("size"));
        assertFalse(taken.has("height"));
        JSONObject placed = moments.put(words("there", "Up here").put("size", "large").put("height", "top").put("side", "left"), NOW);
        assertEquals("large top left", placed.getString("size") + " " + placed.getString("height") + " " + placed.getString("side"));
        moments.remove("there", NOW);
        assertEquals(1, moments.document(NOW + 29_999L).getJSONArray("moments").length());
        long shown = moments.version(NOW + 29_999L);
        assertTrue(shown > before);
        // When its time is up it leaves, and the version says that something changed.
        assertEquals(0, moments.document(NOW + 30_000L).getJSONArray("moments").length());
        assertTrue(moments.version(NOW + 30_000L) > shown);
    }

    @Test
    public void aMomentWithoutSecondsStaysTheUsualTimeAndGetsAName() throws Exception {
        JSONObject taken = moments.put(box("text").put("text", "Hello"), NOW);
        assertTrue(taken.getString("id").matches("m\\d+"));
        assertEquals(NOW + Moments.USUAL_SECONDS * 1000L, taken.getLong("until"));
    }

    @Test
    public void theSameIdTakesThePlaceOfTheOneShowing() throws Exception {
        assertFalse(moments.shows("score", NOW));
        moments.put(words("score", "2 : 1"), NOW);
        assertTrue(moments.shows("score", NOW));
        moments.put(words("other", "Go"), NOW);
        moments.put(words("score", "3 : 1"), NOW + 1000L);
        JSONArray shown = moments.document(NOW + 1000L).getJSONArray("moments");
        assertEquals(2, shown.length());
        assertEquals("3 : 1", shown.getJSONObject(0).getString("text"));
        assertTrue(moments.remove("score", NOW + 1000L));
        assertFalse(moments.remove("score", NOW + 1000L));
        // One whose time is up is not there to be replaced.
        assertFalse(moments.shows("other", NOW + Moments.USUAL_SECONDS * 1000L));
        assertEquals(0, moments.clear());
    }

    @Test
    public void oneMoreThanTheGlassHoldsTakesThePlaceOfTheOldest() throws Exception {
        for (int index = 0; index <= Moments.LIMIT; index++) {
            moments.put(words("n" + index, "Number " + index), NOW);
        }
        JSONArray shown = moments.document(NOW).getJSONArray("moments");
        assertEquals(Moments.LIMIT, shown.length());
        assertEquals("n1", shown.getJSONObject(0).getString("id"));
    }

    @Test
    public void aCountdownStaysUntilItHasRunOutAndALittleLonger() throws Exception {
        JSONObject taken = moments.put(box("countdown").put("title", "Tea").put("endsAt", NOW + 240_000L), NOW);
        assertEquals(NOW + 240_000L, taken.getLong("endsAt"));
        assertEquals(NOW + 240_000L + Moments.LINGER_SECONDS * 1000L, taken.getLong("until"));
        assertEquals(NOW, taken.getLong("createdAt"));
        // Told how long to stay, it stays that long.
        assertEquals(NOW + 60_000L, moments.put(box("countdown").put("endsAt", NOW + 240_000L).put("seconds", 60), NOW).getLong("until"));
        refused(box("countdown"), "endsAt");
        refused(box("countdown").put("endsAt", NOW), "endsAt");
        refused(box("countdown").put("endsAt", NOW + 7 * 3_600_000L), "endsAt");
    }

    @Test
    public void aListAChartAndADrawingAreTakenAsDescribed() throws Exception {
        JSONObject list = moments.put(box("list").put("rows", new JSONArray()
                .put(new JSONObject().put("label", "1").put("text", "Boil the water"))
                .put(new JSONObject().put("text", "Steep four minutes"))), NOW);
        assertEquals("", list.getJSONArray("rows").getJSONObject(1).getString("label"));

        JSONObject chart = moments.put(box("chart").put("chart", "line").put("values", new JSONArray()
                .put(new JSONObject().put("label", "9 PM").put("value", 75))
                .put(new JSONObject().put("label", "10 PM").put("value", 72.5))), NOW);
        assertEquals("line", chart.getString("chart"));
        assertEquals(72.5, chart.getJSONArray("values").getJSONObject(1).getDouble("value"), 0);

        JSONObject drawing = moments.put(box("drawing").put("color", "#FFD9A0").put("motion", "pulse").put("shapes", new JSONArray()
                .put(new JSONObject().put("shape", "circle").put("x", 50).put("y", 50).put("r", 30).put("fill", "none"))
                .put(new JSONObject().put("shape", "line").put("x1", 10).put("y1", 10).put("x2", 90).put("y2", 90).put("width", 2))
                .put(new JSONObject().put("shape", "rect").put("x", 20).put("y", 20).put("w", 60).put("h", 60))
                .put(new JSONObject().put("shape", "path").put("d", "M50 30 C20 0 0 40 50 80 C100 40 80 0 50 30 Z").put("fill", "#ff5577"))
                .put(new JSONObject().put("shape", "text").put("x", 50).put("y", 95).put("text", "home"))), NOW);
        assertEquals("#ffd9a0", drawing.getString("color"));
        JSONArray shapes = drawing.getJSONArray("shapes");
        assertEquals(5, shapes.length());
        assertEquals("none", shapes.getJSONObject(0).getString("fill"));
        assertEquals(0, shapes.getJSONObject(2).getDouble("round"), 0);
        assertEquals("#ff5577", shapes.getJSONObject(3).getString("fill"));
        assertEquals(10, shapes.getJSONObject(4).getDouble("size"), 0);
    }

    @Test
    public void whatTheGlassCannotDrawIsRefusedWithTheFieldAtFault() throws Exception {
        refused(new JSONObject().put("kind", "video"), "kind");
        refused(words("Not An Id", "Hello"), "id");
        refused(box("text"), "text");
        refused(box("text").put("text", "a\u0007b"), "text");
        refused(box("text").put("text", "1\n2\n3\n4\n5\n6\n7"), "text");
        refused(words("a", "Hello").put("title", "x\ny"), "title");
        refused(words("a", "Hello").put("seconds", 2), "seconds");
        refused(words("a", "Hello").put("seconds", 7 * 3600), "seconds");
        refused(words("a", "Hello").put("seconds", 10.5), "seconds");
        refused(words("a", "Hello").put("color", "red"), "color");
        refused(words("a", "Hello").put("motion", "explode"), "motion");
        refused(words("a", "Hello").put("size", "huge"), "size");
        refused(words("a", "Hello").put("height", "ceiling"), "height");
        refused(words("a", "Hello").put("side", "middle"), "side");
        refused(box("list"), "rows");
        refused(box("list").put("rows", new JSONArray().put("tea")), "rows");
        refused(box("chart").put("values", new JSONArray().put(new JSONObject().put("label", "a").put("value", 1))), "values");
        refused(box("chart").put("values", new JSONArray()
                .put(new JSONObject().put("label", "a").put("value", 1))
                .put(new JSONObject().put("label", "b").put("value", "two"))), "value");
        refused(box("drawing").put("shapes", new JSONArray().put(new JSONObject().put("shape", "image"))), "shapes");
        refused(box("drawing").put("shapes", new JSONArray().put(new JSONObject().put("shape", "circle").put("x", 50).put("y", 50))), "r");
        // A path is numbers and path commands; nothing that could be more than a line.
        refused(box("drawing").put("shapes", new JSONArray()
                .put(new JSONObject().put("shape", "path").put("d", "M0 0\"/><script>alert(1)</script>"))), "shapes");
        refused(box("drawing").put("shapes", new JSONArray()
                .put(new JSONObject().put("shape", "circle").put("x", 50).put("y", 50).put("r", 5).put("fill", "url(#x)"))), "fill");
    }
}
