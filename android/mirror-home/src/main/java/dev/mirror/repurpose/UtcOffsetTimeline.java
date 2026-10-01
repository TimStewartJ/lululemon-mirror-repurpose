package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.Arrays;
import java.util.Locale;

/**
 * The Mirror's local time: a UTC offset that changes at known instants.
 * The vendor firmware has no usable time-zone rules, so daylight-saving
 * changes come from a paired browser or from the bundled zone table.
 */
final class UtcOffsetTimeline {
    static final int MAX_TRANSITIONS = 64;
    static final int MAX_OFFSET_MINUTES = 14 * 60;
    static final int DAY_MINUTES = 24 * 60;

    private static final long MINUTE_MS = 60_000L;
    private static final long DAY_MS = DAY_MINUTES * MINUTE_MS;

    private final int baseOffsetMinutes;
    private final long[] changeAt;
    private final int[] changeOffsetMinutes;

    UtcOffsetTimeline(int baseOffsetMinutes, long[] changeAt, int[] changeOffsetMinutes) {
        this.baseOffsetMinutes = baseOffsetMinutes;
        this.changeAt = changeAt;
        this.changeOffsetMinutes = changeOffsetMinutes;
    }

    static UtcOffsetTimeline fixed(int offsetMinutes) {
        return new UtcOffsetTimeline(offsetMinutes, new long[0], new int[0]);
    }

    /** Strictly validates a client request; messages are safe to show to users. */
    static UtcOffsetTimeline parse(int baseOffsetMinutes, JSONArray changes) {
        if (!validOffset(baseOffsetMinutes)) {
            throw new IllegalArgumentException("Invalid UTC offset");
        }
        if (changes == null) {
            return fixed(baseOffsetMinutes);
        }
        if (changes.length() > MAX_TRANSITIONS) {
            throw new IllegalArgumentException(
                    "At most " + MAX_TRANSITIONS + " UTC offset changes are supported");
        }
        long[] at = new long[changes.length()];
        int[] offsets = new int[changes.length()];
        int count = 0;
        long previousAt = 0L;
        int previousOffset = baseOffsetMinutes;
        for (int index = 0; index < changes.length(); index++) {
            JSONObject change = changes.optJSONObject(index);
            if (change == null) {
                throw new IllegalArgumentException(
                        "Each UTC offset change needs a time and an offset");
            }
            long when = wholeNumber(change.opt("at"), "UTC offset change times");
            long offset = wholeNumber(change.opt("utcOffsetMinutes"), "UTC offsets");
            if (when <= previousAt) {
                throw new IllegalArgumentException(
                        "UTC offset changes must be in time order");
            }
            if (offset < -MAX_OFFSET_MINUTES || offset > MAX_OFFSET_MINUTES) {
                throw new IllegalArgumentException("Invalid UTC offset");
            }
            previousAt = when;
            if (offset == previousOffset) {
                continue;
            }
            at[count] = when;
            offsets[count] = (int) offset;
            previousOffset = (int) offset;
            count++;
        }
        return new UtcOffsetTimeline(
                baseOffsetMinutes,
                Arrays.copyOf(at, count),
                Arrays.copyOf(offsets, count));
    }

    /** Lenient read of persisted changes: absent or corrupt yields null. */
    static UtcOffsetTimeline fromStorage(int baseOffsetMinutes, String serializedChanges) {
        if (serializedChanges == null) {
            return null;
        }
        try {
            return parse(baseOffsetMinutes, new JSONArray(serializedChanges));
        } catch (JSONException | IllegalArgumentException error) {
            return null;
        }
    }

    /**
     * Chooses what the clock follows: changes a browser supplied, else the
     * bundled table for the saved zone when it agrees with the saved offset,
     * else the saved offset alone.
     */
    static UtcOffsetTimeline resolve(
            int storedOffsetMinutes,
            String storedChanges,
            UtcOffsetTimeline bundled) {
        UtcOffsetTimeline supplied = fromStorage(storedOffsetMinutes, storedChanges);
        if (supplied != null) {
            return supplied;
        }
        if (bundled != null && bundled.usesOffset(storedOffsetMinutes)) {
            return bundled;
        }
        return fixed(storedOffsetMinutes);
    }

    static boolean validOffset(int minutes) {
        return minutes >= -MAX_OFFSET_MINUTES && minutes <= MAX_OFFSET_MINUTES;
    }

    /** Names the branch {@link #resolve} takes: "client", "bundled" or "fixed". */
    static String source(
            int storedOffsetMinutes,
            String storedChanges,
            UtcOffsetTimeline bundled) {
        if (fromStorage(storedOffsetMinutes, storedChanges) != null) {
            return "client";
        }
        return bundled != null && bundled.usesOffset(storedOffsetMinutes) ? "bundled" : "fixed";
    }

    static String gmtId(int offsetMinutes) {
        int absolute = Math.abs(offsetMinutes);
        return String.format(
                Locale.US,
                "GMT%s%02d:%02d",
                offsetMinutes >= 0 ? "+" : "-",
                absolute / 60,
                absolute % 60);
    }

    int offsetMinutesAt(long epochMillis) {
        int offset = baseOffsetMinutes;
        for (int index = 0; index < changeAt.length && changeAt[index] <= epochMillis; index++) {
            offset = changeOffsetMinutes[index];
        }
        return offset;
    }

    /** The first change strictly after {@code epochMillis}, or -1 when none is known. */
    long nextChangeAfter(long epochMillis) {
        for (long at : changeAt) {
            if (at > epochMillis) {
                return at;
            }
        }
        return -1L;
    }

    int minuteOfDayAt(long epochMillis) {
        return minuteOfDay(epochMillis, offsetMinutesAt(epochMillis));
    }

    static int minuteOfDay(long epochMillis, int offsetMinutes) {
        long local = (epochMillis + offsetMinutes * MINUTE_MS) % DAY_MS;
        if (local < 0) {
            local += DAY_MS;
        }
        return (int) (local / MINUTE_MS);
    }

    /** Whether the zone ever uses this offset, at the start or after any change. */
    boolean usesOffset(int offsetMinutes) {
        if (baseOffsetMinutes == offsetMinutes) {
            return true;
        }
        for (int offset : changeOffsetMinutes) {
            if (offset == offsetMinutes) {
                return true;
            }
        }
        return false;
    }

    int changeCount() {
        return changeAt.length;
    }

    /** Every change, for persisting what a browser supplied. */
    String serializeChanges() {
        try {
            return changesJson(Long.MIN_VALUE, Integer.MAX_VALUE).toString();
        } catch (JSONException impossible) {
            throw new IllegalStateException("Unable to serialize UTC offset changes", impossible);
        }
    }

    /** Up to {@code limit} changes strictly after {@code afterMillis}, soonest first. */
    JSONArray changesJson(long afterMillis, int limit) throws JSONException {
        JSONArray result = new JSONArray();
        for (int index = 0; index < changeAt.length && result.length() < limit; index++) {
            if (changeAt[index] > afterMillis) {
                result.put(new JSONObject()
                        .put("at", changeAt[index])
                        .put("utcOffsetMinutes", changeOffsetMinutes[index]));
            }
        }
        return result;
    }

    private static long wholeNumber(Object value, String label) {
        if (value instanceof Number) {
            double number = ((Number) value).doubleValue();
            long whole = ((Number) value).longValue();
            if (number == (double) whole) {
                return whole;
            }
        }
        throw new IllegalArgumentException(label + " must be whole numbers");
    }
}
