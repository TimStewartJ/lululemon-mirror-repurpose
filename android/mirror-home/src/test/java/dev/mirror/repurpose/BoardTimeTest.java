package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.junit.Test;

public final class BoardTimeTest {
    private static final long NINE_PACIFIC = 1791043200000L; // 2026-10-03T16:00:00Z

    @Test
    public void readsIsoTimesWithAnyOffset() {
        assertEquals(NINE_PACIFIC, BoardTime.parse("2026-10-03T09:00:00-07:00", "due"));
        assertEquals(NINE_PACIFIC, BoardTime.parse("2026-10-03T16:00:00Z", "due"));
        assertEquals(NINE_PACIFIC, BoardTime.parse("2026-10-03T16:00Z", "due"));
        assertEquals(NINE_PACIFIC, BoardTime.parse("2026-10-03 18:00:00+0200", "due"));
        assertEquals(NINE_PACIFIC, BoardTime.parse("2026-10-03T21:30:00+05:30", "due"));
        assertEquals(NINE_PACIFIC, BoardTime.parse(" 2026-10-03T12:00:00-04 ", "due"));
        assertEquals(NINE_PACIFIC + 250, BoardTime.parse("2026-10-03T16:00:00.25Z", "due"));
        assertEquals(NINE_PACIFIC + 123, BoardTime.parse("2026-10-03T16:00:00.123456Z", "due"));
    }

    @Test
    public void readsLeapDaysAndYearEnds() {
        assertEquals(1709164800000L, BoardTime.parse("2024-02-29T00:00:00Z", "due"));
        assertEquals(1798761599000L, BoardTime.parse("2026-12-31T23:59:59Z", "due"));
        assertEquals(1798761600000L, BoardTime.parse("2027-01-01T00:00:00Z", "due"));
    }

    @Test
    public void readsMillisecondsAsNumbers() {
        assertEquals(NINE_PACIFIC, BoardTime.parse(Long.valueOf(NINE_PACIFIC), "due"));
        assertEquals(NINE_PACIFIC, BoardTime.parse(Double.valueOf(NINE_PACIFIC), "due"));
    }

    @Test
    public void writesIsoInUtc() {
        assertEquals("2026-10-03T16:00:00Z", BoardTime.iso(NINE_PACIFIC));
        assertEquals("2024-02-29T00:00:00Z", BoardTime.iso(1709164800000L));
        assertEquals("2026-12-31T23:59:59Z", BoardTime.iso(1798761599999L));
        assertEquals("1970-01-01T00:00:00Z", BoardTime.iso(0));
        assertEquals("1969-12-31T23:59:59Z", BoardTime.iso(-1000));
    }

    @Test
    public void isoRoundTripsAcrossTheAcceptedRange() {
        for (long millis = BoardTime.EARLIEST; millis < BoardTime.LATEST; millis += 86_399_000L * 37) {
            long whole = millis / 1000 * 1000;
            assertEquals(whole, BoardTime.parse(BoardTime.iso(whole), "due"));
        }
    }

    @Test
    public void saysWhatIsWrongWithATime() {
        assertRefusal("2026-10-03T09:00:00", "offset");
        assertRefusal("tomorrow at nine", "ISO 8601");
        assertRefusal("2026-02-30T09:00:00Z", "not a real date");
        assertRefusal("2026-10-03T24:00:00Z", "not a real date");
        assertRefusal("2026-10-03T09:00:00+15:00", "impossible UTC offset");
        assertRefusal(Long.valueOf(1791043200L), "looks like seconds");
        assertRefusal(Double.valueOf(1791043200000.5), "whole number");
        assertRefusal("1999-01-01T00:00:00Z", "between 2020 and 2100");
        assertRefusal(Long.valueOf(4102444800000L), "between 2020 and 2100");
        assertRefusal(Boolean.TRUE, "must be a time");
    }

    private static void assertRefusal(Object value, String expected) {
        try {
            BoardTime.parse(value, "due");
            fail("Expected a refusal of " + value);
        } catch (BoardError refusal) {
            assertEquals(400, refusal.status);
            assertEquals("due", refusal.field);
            assertTrue(refusal.getMessage(), refusal.getMessage().contains(expected));
            assertTrue(refusal.getMessage(), refusal.getMessage().startsWith("due "));
        }
    }
}
