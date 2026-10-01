package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.Calendar;
import java.util.TimeZone;

public final class ZoneOffsetTableTest {
    private static final long MINUTE_MS = 60_000L;

    @Test
    public void shippedTableFollowsDaylightSavingInFamiliarZones() throws Exception {
        ZoneOffsetTable table = shipped();
        // The year after the table was written is always inside its window.
        int year = yearAfterShippedTableStarts();

        // United States: second Sunday of March and first Sunday of November, 02:00 local.
        long springForward = sundayUtc(year, Calendar.MARCH, 2, 10, 0);
        long fallBack = sundayUtc(year, Calendar.NOVEMBER, 1, 9, 0);
        UtcOffsetTimeline pacific = table.timelineFor("America/Los_Angeles");
        assertEquals(-480, pacific.offsetMinutesAt(springForward - MINUTE_MS));
        assertEquals(-420, pacific.offsetMinutesAt(springForward));
        assertEquals(-420, pacific.offsetMinutesAt(fallBack - MINUTE_MS));
        assertEquals(-480, pacific.offsetMinutesAt(fallBack));
        assertEquals(fallBack, pacific.nextChangeAfter(springForward));

        // European Union: last Sunday of March and of October, 01:00 UTC.
        long summerTime = sundayUtc(year, Calendar.MARCH, -1, 1, 0);
        long winterTime = sundayUtc(year, Calendar.OCTOBER, -1, 1, 0);
        UtcOffsetTimeline berlin = table.timelineFor("Europe/Berlin");
        assertEquals(60, berlin.offsetMinutesAt(summerTime - MINUTE_MS));
        assertEquals(120, berlin.offsetMinutesAt(summerTime));
        assertEquals(120, berlin.offsetMinutesAt(winterTime - MINUTE_MS));
        assertEquals(60, berlin.offsetMinutesAt(winterTime));

        // Lord Howe Island moves its clock by half an hour.
        UtcOffsetTimeline lordHowe = table.timelineFor("Australia/Lord_Howe");
        assertTrue(lordHowe.usesOffset(630));
        assertTrue(lordHowe.usesOffset(660));
        assertTrue(lordHowe.changeCount() >= 2);
    }

    @Test
    public void shippedTableKeepsZonesWithoutDaylightSavingFixed() throws Exception {
        ZoneOffsetTable table = shipped();

        assertFixed(table.timelineFor("Asia/Kolkata"), 330);
        assertFixed(table.timelineFor("Asia/Kathmandu"), 345);
        assertFixed(table.timelineFor("America/Phoenix"), -420);
        assertFixed(table.timelineFor("UTC"), 0);
        assertFixed(table.timelineFor("Etc/GMT+8"), -480);
    }

    @Test
    public void shippedTableCoversAliasesAndReportsItsSource() throws Exception {
        ZoneOffsetTable table = shipped();

        assertSame(table.timelineFor("America/Los_Angeles"), table.timelineFor("US/Pacific"));
        assertSame(table.timelineFor("Asia/Kolkata"), table.timelineFor("Asia/Calcutta"));
        assertTrue(table.size() > 400);
        assertTrue(table.tzdata(), table.tzdata().matches("20[0-9]{2}[a-z]|system"));
        assertNull(table.timelineFor("Mars/Olympus_Mons"));
        assertNull(table.timelineFor(null));
    }

    @Test
    public void parsesRulesSharedBetweenZones() throws Exception {
        ZoneOffsetTable table = ZoneOffsetTable.parse(
                "{\"version\":1,\"tzdata\":\"2026d\",\"from\":0,\"until\":100,"
                        + "\"rules\":[[0,[]],[-420,[[10,-480],[20,-420]]]],"
                        + "\"zones\":{\"A/One\":1,\"A/Two\":1,\"B/Three\":0}}");

        assertEquals(3, table.size());
        assertEquals("2026d", table.tzdata());
        assertSame(table.timelineFor("A/One"), table.timelineFor("A/Two"));
        assertEquals(-420, table.timelineFor("A/One").offsetMinutesAt(10 * MINUTE_MS - 1));
        assertEquals(-480, table.timelineFor("A/One").offsetMinutesAt(10 * MINUTE_MS));
        assertEquals(-420, table.timelineFor("A/One").offsetMinutesAt(20 * MINUTE_MS));
        assertEquals(0, table.timelineFor("B/Three").changeCount());
    }

    @Test
    public void rejectsTablesItCannotTrust() {
        assertRejected("{\"version\":2,\"rules\":[],\"zones\":{}}");
        assertRejected("{\"version\":1,\"rules\":[[0,[]]],\"zones\":{\"A/One\":1}}");
        assertRejected("{\"version\":1,\"rules\":[[0,[[20,60],[10,0]]]],\"zones\":{}}");
        assertRejected("{\"version\":1,\"rules\":[[0,[[10,900]]]],\"zones\":{}}");
        assertRejected("{\"version\":1,\"rules\":[[900,[]]],\"zones\":{}}");
        assertRejected("not json");
        assertEquals(0, ZoneOffsetTable.empty().size());
    }

    private static ZoneOffsetTable shipped() throws Exception {
        return ZoneOffsetTable.parse(shippedJson());
    }

    private static String shippedJson() throws Exception {
        byte[] bytes = Files.readAllBytes(
                Paths.get("src", "main", "assets", ZoneOffsetTable.ASSET_NAME));
        return new String(bytes, StandardCharsets.UTF_8);
    }

    private static int yearAfterShippedTableStarts() throws Exception {
        Calendar calendar = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        calendar.setTimeInMillis(new JSONObject(shippedJson()).getLong("from") * MINUTE_MS);
        return calendar.get(Calendar.YEAR) + 1;
    }

    private static void assertFixed(UtcOffsetTimeline timeline, int offsetMinutes) {
        assertNotNull(timeline);
        assertEquals(0, timeline.changeCount());
        assertEquals(offsetMinutes, timeline.offsetMinutesAt(0L));
    }

    private static void assertRejected(String json) {
        try {
            ZoneOffsetTable.parse(json);
            fail("Expected the table to be rejected: " + json);
        } catch (JSONException expected) {
            // Rejected as required.
        }
    }

    /** The nth Sunday of a month (or the last, for -1) at a UTC time of day. */
    private static long sundayUtc(int year, int month, int nth, int hour, int minute) {
        Calendar calendar = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        calendar.clear();
        calendar.set(year, month, 1, hour, minute, 0);
        int toSunday = (Calendar.SUNDAY - calendar.get(Calendar.DAY_OF_WEEK) + 7) % 7;
        calendar.add(Calendar.DAY_OF_MONTH, toSunday);
        if (nth > 0) {
            calendar.add(Calendar.DAY_OF_MONTH, 7 * (nth - 1));
            return calendar.getTimeInMillis();
        }
        while (true) {
            Calendar next = (Calendar) calendar.clone();
            next.add(Calendar.DAY_OF_MONTH, 7);
            if (next.get(Calendar.MONTH) != month) {
                return calendar.getTimeInMillis();
            }
            calendar = next;
        }
    }
}
