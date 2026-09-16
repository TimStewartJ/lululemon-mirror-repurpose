package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

import java.util.Arrays;
import java.util.Calendar;
import java.util.TimeZone;

public final class BackgroundVideoScheduleTest {
    private static final String MORNING = repeat('a');
    private static final String NIGHT = repeat('b');
    private static final String HELD = repeat('c');
    private static final TimeZone PACIFIC = TimeZone.getTimeZone("GMT-07:00");

    @Test
    public void slotsAreSortedAndEachShowsUntilTheNextStart() throws Exception {
        BackgroundVideoSchedule schedule = schedule(true, "19:00", NIGHT, "06:00", MORNING);

        assertEquals("06:00", schedule.slots.get(0).start());
        assertEquals(MORNING, schedule.slotAt(6 * 60).videoId);
        assertEquals(MORNING, schedule.slotAt(18 * 60 + 59).videoId);
        assertEquals(NIGHT, schedule.slotAt(19 * 60).videoId);
        assertEquals(NIGHT, schedule.slotAt(23 * 60 + 59).videoId);
    }

    @Test
    public void beforeTheFirstStartThePreviousNightContinues() throws Exception {
        BackgroundVideoSchedule schedule = schedule(true, "06:00", MORNING, "19:00", NIGHT);

        assertEquals(NIGHT, schedule.slotAt(0).videoId);
        assertEquals(NIGHT, schedule.slotAt(5 * 60 + 59).videoId);
        assertEquals(MORNING, schedule.slotAfter(schedule.slotAt(0)).videoId);
    }

    @Test
    public void nextChangeIsTheNextStartInTheMirrorsLocalOffset() throws Exception {
        BackgroundVideoSchedule schedule = schedule(true, "06:00", MORNING, "19:00", NIGHT);

        assertEquals(at(2026, 9, 15, 19, 0), schedule.nextChangeMillis(at(2026, 9, 15, 12, 30), PACIFIC));
        assertEquals(at(2026, 9, 16, 6, 0), schedule.nextChangeMillis(at(2026, 9, 15, 23, 2), PACIFIC));
        assertEquals(at(2026, 9, 16, 6, 0), schedule.nextChangeMillis(at(2026, 9, 16, 1, 0), PACIFIC));
        assertEquals(at(2026, 9, 16, 19, 0), schedule.nextChangeMillis(at(2026, 9, 16, 6, 0), PACIFIC));
    }

    @Test
    public void aSingleTimeRepeatsDailyAndEmptyHasNoChange() throws Exception {
        BackgroundVideoSchedule single = schedule(true, "08:15", MORNING);

        assertEquals(MORNING, single.slotAt(0).videoId);
        assertEquals(at(2026, 9, 16, 8, 15), single.nextChangeMillis(at(2026, 9, 15, 9, 0), PACIFIC));
        assertEquals(-1L, BackgroundVideoSchedule.disabled().nextChangeMillis(0L, PACIFIC));
        assertNull(BackgroundVideoSchedule.disabled().slotAt(600));
    }

    @Test
    public void effectiveVideoPrefersHoldThenTimetableThenManualSelection() throws Exception {
        BackgroundVideoSchedule schedule = schedule(true, "06:00", MORNING, "19:00", NIGHT);
        long evening = at(2026, 9, 15, 23, 2);
        long nextMorning = at(2026, 9, 16, 6, 0);

        assertEquals(NIGHT, BackgroundVideoSchedule.effectiveId(schedule, HELD, "", 0L, evening, PACIFIC));
        assertEquals(HELD, BackgroundVideoSchedule.effectiveId(schedule, HELD, HELD, nextMorning, evening, PACIFIC));
        assertEquals(MORNING, BackgroundVideoSchedule.effectiveId(schedule, HELD, HELD, nextMorning, nextMorning, PACIFIC));
        assertEquals(HELD, BackgroundVideoSchedule.effectiveId(
                schedule(false, "06:00", MORNING), HELD, NIGHT, nextMorning, evening, PACIFIC));
        assertEquals("", BackgroundVideoSchedule.effectiveId(null, "../x", "", 0L, evening, PACIFIC));
    }

    @Test
    public void strictParsingRejectsInvalidRequestsWithUserMessages() throws Exception {
        assertRejected(new JSONObject().put("enabled", true).put("slots", new JSONArray()), "at least one time");
        assertRejected(body(true, "6:00", MORNING), "HH:MM");
        assertRejected(body(true, "24:00", MORNING), "HH:MM");
        assertRejected(body(true, "06:00", "not-a-video"), "Choose a video");
        assertRejected(body(true, "06:00", MORNING, "06:00", NIGHT), "must be different");
        assertRejected(new JSONObject().put("enabled", true).put("slots", "06:00"), "must be a list");
        JSONArray many = new JSONArray();
        for (int hour = 0; hour <= BackgroundVideoSchedule.MAX_SLOTS; hour++) {
            many.put(new JSONObject().put("start", String.format("%02d:00", hour)).put("videoId", MORNING));
        }
        assertRejected(new JSONObject().put("enabled", true).put("slots", many), "at most");
    }

    @Test
    public void disabledScheduleMayKeepTimesForLater() throws Exception {
        BackgroundVideoSchedule schedule = schedule(false, "06:00", MORNING, "19:00", NIGHT);

        assertFalse(schedule.isActive());
        assertEquals(2, schedule.slots.size());
        assertFalse(BackgroundVideoSchedule.parse(new JSONObject()).isActive());
    }

    @Test
    public void storageRoundTripsAndCorruptionDisables() throws Exception {
        BackgroundVideoSchedule schedule = schedule(true, "19:00", NIGHT, "06:00", MORNING);
        BackgroundVideoSchedule restored =
                BackgroundVideoSchedule.fromStorage(schedule.toJson().toString());

        assertTrue(restored.isActive());
        assertEquals("06:00", restored.slots.get(0).start());
        assertEquals(NIGHT, restored.slots.get(1).videoId);
        assertFalse(BackgroundVideoSchedule.fromStorage("{broken").isActive());
        assertFalse(BackgroundVideoSchedule.fromStorage("").isActive());
        assertFalse(BackgroundVideoSchedule.fromStorage(null).isActive());
    }

    @Test
    public void reportsScheduledVideosAndTheirStarts() throws Exception {
        BackgroundVideoSchedule schedule =
                schedule(true, "06:00", MORNING, "12:00", NIGHT, "19:00", MORNING);

        assertEquals(Arrays.asList(MORNING, NIGHT), Arrays.asList(schedule.videoIds().toArray()));
        assertEquals(Arrays.asList("06:00", "19:00"), schedule.startsFor(MORNING));
        assertTrue(schedule.startsFor(HELD).isEmpty());
    }

    @Test
    public void holdIsActiveOnlyForValidUnexpiredVideos() {
        assertTrue(BackgroundVideoSchedule.holdActive(HELD, 10L, 9L));
        assertFalse(BackgroundVideoSchedule.holdActive(HELD, 10L, 10L));
        assertFalse(BackgroundVideoSchedule.holdActive("", 10L, 9L));
    }

    private static void assertRejected(JSONObject body, String message) {
        try {
            BackgroundVideoSchedule.parse(body);
            fail("Expected rejection containing: " + message);
        } catch (IllegalArgumentException error) {
            assertTrue(error.getMessage(), error.getMessage().contains(message));
        }
    }

    private static BackgroundVideoSchedule schedule(boolean enabled, String... pairs) throws Exception {
        return BackgroundVideoSchedule.parse(body(enabled, pairs));
    }

    private static JSONObject body(boolean enabled, String... pairs) throws Exception {
        JSONArray slots = new JSONArray();
        for (int index = 0; index < pairs.length; index += 2) {
            slots.put(new JSONObject().put("start", pairs[index]).put("videoId", pairs[index + 1]));
        }
        return new JSONObject().put("enabled", enabled).put("slots", slots);
    }

    private static long at(int year, int month, int day, int hour, int minute) {
        Calendar calendar = Calendar.getInstance(PACIFIC);
        calendar.clear();
        calendar.set(year, month - 1, day, hour, minute, 0);
        return calendar.getTimeInMillis();
    }

    private static String repeat(char value) {
        StringBuilder result = new StringBuilder();
        for (int index = 0; index < 64; index++) {
            result.append(value);
        }
        return result.toString();
    }
}
