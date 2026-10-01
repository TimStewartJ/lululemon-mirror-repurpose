package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public final class UtcOffsetTimelineTest {
    /* 2026-11-01 09:00 UTC: Pacific daylight time ends at 02:00 local. */
    private static final long FALL_BACK = 1793523600000L;
    /* 2027-03-14 10:00 UTC: Pacific daylight time begins at 02:00 local. */
    private static final long SPRING_FORWARD = 1805018400000L;

    @Test
    public void offsetFollowsEachChangeFromItsExactInstant() throws Exception {
        UtcOffsetTimeline pacific = pacific();

        assertEquals(-420, pacific.offsetMinutesAt(0L));
        assertEquals(-420, pacific.offsetMinutesAt(FALL_BACK - 1));
        assertEquals(-480, pacific.offsetMinutesAt(FALL_BACK));
        assertEquals(-480, pacific.offsetMinutesAt(SPRING_FORWARD - 1));
        assertEquals(-420, pacific.offsetMinutesAt(SPRING_FORWARD));
        assertEquals(FALL_BACK, pacific.nextChangeAfter(FALL_BACK - 1));
        assertEquals(SPRING_FORWARD, pacific.nextChangeAfter(FALL_BACK));
        assertEquals(-1L, pacific.nextChangeAfter(SPRING_FORWARD));
    }

    @Test
    public void minuteOfDayUsesTheOffsetInForce() throws Exception {
        UtcOffsetTimeline pacific = pacific();

        assertEquals(60 + 59, pacific.minuteOfDayAt(FALL_BACK - 60_000L));
        assertEquals(60, pacific.minuteOfDayAt(FALL_BACK));
        assertEquals(60 + 59, pacific.minuteOfDayAt(SPRING_FORWARD - 60_000L));
        assertEquals(3 * 60, pacific.minuteOfDayAt(SPRING_FORWARD));
        assertEquals(23 * 60 + 59, UtcOffsetTimeline.minuteOfDay(-60_000L, 0));
        assertEquals(5 * 60 + 45, UtcOffsetTimeline.minuteOfDay(0L, 345));
    }

    @Test
    public void fixedOffsetNeverChanges() {
        UtcOffsetTimeline nepal = UtcOffsetTimeline.fixed(345);

        assertEquals(345, nepal.offsetMinutesAt(FALL_BACK));
        assertEquals(-1L, nepal.nextChangeAfter(0L));
        assertEquals(0, nepal.changeCount());
        assertEquals("[]", nepal.serializeChanges());
    }

    @Test
    public void changesThatKeepTheOffsetAreDropped() throws Exception {
        UtcOffsetTimeline timeline = UtcOffsetTimeline.parse(-420, new JSONArray()
                .put(change(1000L, -420))
                .put(change(2000L, -480))
                .put(change(3000L, -480)));

        assertEquals(1, timeline.changeCount());
        assertEquals(2000L, timeline.nextChangeAfter(0L));
    }

    @Test
    public void strictParsingRejectsMalformedClientChanges() throws Exception {
        assertRejected(900, new JSONArray(), "Invalid UTC offset");
        assertRejected(0, new JSONArray().put("soon"), "needs a time and an offset");
        assertRejected(0, new JSONArray().put(change(1000L, 900)), "Invalid UTC offset");
        assertRejected(0, new JSONArray().put(change(0L, 60)), "time order");
        assertRejected(
                0,
                new JSONArray().put(change(2000L, 60)).put(change(2000L, 0)),
                "time order");
        assertRejected(
                0,
                new JSONArray().put(new JSONObject().put("at", 1000.5).put("utcOffsetMinutes", 60)),
                "whole numbers");
        assertRejected(
                0,
                new JSONArray().put(new JSONObject().put("at", 1000L).put("utcOffsetMinutes", "60")),
                "whole numbers");
        assertRejected(
                0,
                new JSONArray().put(new JSONObject().put("at", 1000L)),
                "whole numbers");
        JSONArray many = new JSONArray();
        for (int index = 0; index <= UtcOffsetTimeline.MAX_TRANSITIONS; index++) {
            many.put(change(1000L + index, index % 2 == 0 ? 60 : 0));
        }
        assertRejected(0, many, "At most");
    }

    @Test
    public void nullChangesMeanAFixedOffset() {
        assertEquals(0, UtcOffsetTimeline.parse(-300, null).changeCount());
    }

    @Test
    public void storageRoundTripsAndCorruptionReadsAsAbsent() throws Exception {
        UtcOffsetTimeline pacific = pacific();
        UtcOffsetTimeline restored =
                UtcOffsetTimeline.fromStorage(-420, pacific.serializeChanges());

        assertEquals(2, restored.changeCount());
        assertEquals(-480, restored.offsetMinutesAt(FALL_BACK));
        assertNull(UtcOffsetTimeline.fromStorage(-420, null));
        assertNull(UtcOffsetTimeline.fromStorage(-420, "{broken"));
        assertNull(UtcOffsetTimeline.fromStorage(-420, "[{\"at\":5}]"));
    }

    @Test
    public void clientChangesWinThenTheBundledZoneThenTheSavedOffset() throws Exception {
        UtcOffsetTimeline bundled = pacific();
        String stored = UtcOffsetTimeline.fixed(-420).serializeChanges();

        assertEquals(0, UtcOffsetTimeline.resolve(-420, stored, bundled).changeCount());
        assertEquals("client", UtcOffsetTimeline.source(-420, stored, bundled));

        assertSame(bundled, UtcOffsetTimeline.resolve(-420, null, bundled));
        assertSame(bundled, UtcOffsetTimeline.resolve(-480, null, bundled));
        assertSame(bundled, UtcOffsetTimeline.resolve(-420, "{broken", bundled));
        assertEquals("bundled", UtcOffsetTimeline.source(-480, null, bundled));

        // A saved offset the zone never uses means the label is wrong; keep the offset.
        UtcOffsetTimeline mismatch = UtcOffsetTimeline.resolve(60, null, bundled);
        assertEquals(60, mismatch.offsetMinutesAt(FALL_BACK));
        assertEquals(0, mismatch.changeCount());
        assertEquals("fixed", UtcOffsetTimeline.source(60, null, bundled));
        assertEquals("fixed", UtcOffsetTimeline.source(-420, null, null));
        assertEquals(-420, UtcOffsetTimeline.resolve(-420, null, null).offsetMinutesAt(0L));
    }

    @Test
    public void upcomingChangesAreListedSoonestFirstUpToALimit() throws Exception {
        UtcOffsetTimeline pacific = pacific();

        JSONArray all = pacific.changesJson(0L, 8);
        assertEquals(2, all.length());
        assertEquals(FALL_BACK, all.getJSONObject(0).getLong("at"));
        assertEquals(-480, all.getJSONObject(0).getInt("utcOffsetMinutes"));
        assertEquals(1, pacific.changesJson(0L, 1).length());
        assertEquals(
                SPRING_FORWARD,
                pacific.changesJson(FALL_BACK, 8).getJSONObject(0).getLong("at"));
        assertEquals(0, pacific.changesJson(SPRING_FORWARD, 8).length());
    }

    @Test
    public void knowsWhichOffsetsAZoneUses() throws Exception {
        UtcOffsetTimeline pacific = pacific();

        assertTrue(pacific.usesOffset(-420));
        assertTrue(pacific.usesOffset(-480));
        assertFalse(pacific.usesOffset(-360));
        assertTrue(UtcOffsetTimeline.validOffset(840));
        assertFalse(UtcOffsetTimeline.validOffset(-841));
    }

    @Test
    public void formatsFixedOffsetZoneIds() {
        assertEquals("GMT-07:00", UtcOffsetTimeline.gmtId(-420));
        assertEquals("GMT+05:45", UtcOffsetTimeline.gmtId(345));
        assertEquals("GMT+00:00", UtcOffsetTimeline.gmtId(0));
    }

    static UtcOffsetTimeline pacific() throws Exception {
        return UtcOffsetTimeline.parse(-420, new JSONArray()
                .put(change(FALL_BACK, -480))
                .put(change(SPRING_FORWARD, -420)));
    }

    private static JSONObject change(long at, int offsetMinutes) throws Exception {
        return new JSONObject().put("at", at).put("utcOffsetMinutes", offsetMinutes);
    }

    private static void assertRejected(int base, JSONArray changes, String message) {
        try {
            UtcOffsetTimeline.parse(base, changes);
            fail("Expected rejection containing: " + message);
        } catch (IllegalArgumentException error) {
            assertTrue(error.getMessage(), error.getMessage().contains(message));
        }
    }
}
