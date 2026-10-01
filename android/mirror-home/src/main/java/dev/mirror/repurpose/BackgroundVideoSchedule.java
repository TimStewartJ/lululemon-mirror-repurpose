package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * A daily timetable of background videos. Each slot starts at a local time of
 * day and shows its video until the next slot starts, wrapping past midnight,
 * so exactly one slot applies at any moment and there are no gaps to resolve.
 */
final class BackgroundVideoSchedule {
    static final int MAX_SLOTS = 8;

    private static final long MINUTE_MS = 60_000L;
    private static final long DAY_MS = UtcOffsetTimeline.DAY_MINUTES * MINUTE_MS;

    static final class Slot {
        final int startMinutes;
        final String videoId;

        Slot(int startMinutes, String videoId) {
            this.startMinutes = startMinutes;
            this.videoId = videoId;
        }

        String start() {
            return formatMinutes(startMinutes);
        }

        JSONObject toJson() throws JSONException {
            return new JSONObject().put("start", start()).put("videoId", videoId);
        }
    }

    final boolean enabled;
    final List<Slot> slots;

    private BackgroundVideoSchedule(boolean enabled, List<Slot> slots) {
        this.enabled = enabled;
        this.slots = Collections.unmodifiableList(slots);
    }

    static BackgroundVideoSchedule disabled() {
        return new BackgroundVideoSchedule(false, new ArrayList<>());
    }

    /** Strictly validates a client request; messages are safe to show to users. */
    static BackgroundVideoSchedule parse(JSONObject body) {
        if (body == null) {
            throw new IllegalArgumentException("A schedule is required");
        }
        Object rawSlots = body.opt("slots");
        if (rawSlots != null && !(rawSlots instanceof JSONArray)) {
            throw new IllegalArgumentException("Schedule times must be a list");
        }
        JSONArray items = (JSONArray) rawSlots;
        List<Slot> slots = new ArrayList<>();
        if (items != null) {
            if (items.length() > MAX_SLOTS) {
                throw new IllegalArgumentException(
                        "A schedule can have at most " + MAX_SLOTS + " times");
            }
            for (int index = 0; index < items.length(); index++) {
                JSONObject item = items.optJSONObject(index);
                if (item == null) {
                    throw new IllegalArgumentException("Each schedule time needs a start and a video");
                }
                int start = InputValidator.parseTimeMinutes(item.optString("start", ""));
                if (start < 0) {
                    throw new IllegalArgumentException("Schedule times must use 24-hour HH:MM");
                }
                String videoId = item.optString("videoId", "");
                if (!BackgroundVideoSelection.validId(videoId)) {
                    throw new IllegalArgumentException("Choose a video for every schedule time");
                }
                slots.add(new Slot(start, videoId));
            }
        }
        Collections.sort(slots, (first, second) -> Integer.compare(first.startMinutes, second.startMinutes));
        for (int index = 1; index < slots.size(); index++) {
            if (slots.get(index).startMinutes == slots.get(index - 1).startMinutes) {
                throw new IllegalArgumentException("Each schedule time must be different");
            }
        }
        boolean enabled = body.optBoolean("enabled", false);
        if (enabled && slots.isEmpty()) {
            throw new IllegalArgumentException("Add at least one time before turning the schedule on");
        }
        return new BackgroundVideoSchedule(enabled, slots);
    }

    /** Lenient read of persisted state: corruption disables rather than crashes. */
    static BackgroundVideoSchedule fromStorage(String serialized) {
        if (serialized == null || serialized.isEmpty()) {
            return disabled();
        }
        try {
            return parse(new JSONObject(serialized));
        } catch (JSONException | IllegalArgumentException error) {
            return disabled();
        }
    }

    JSONObject toJson() throws JSONException {
        JSONArray items = new JSONArray();
        for (Slot slot : slots) {
            items.put(slot.toJson());
        }
        return new JSONObject().put("enabled", enabled).put("slots", items);
    }

    boolean isActive() {
        return enabled && !slots.isEmpty();
    }

    Set<String> videoIds() {
        Set<String> result = new LinkedHashSet<>();
        for (Slot slot : slots) {
            result.add(slot.videoId);
        }
        return result;
    }

    List<String> startsFor(String videoId) {
        List<String> result = new ArrayList<>();
        for (Slot slot : slots) {
            if (slot.videoId.equals(videoId)) {
                result.add(slot.start());
            }
        }
        return result;
    }

    /** The slot showing at a minute of the day; before the first start, the
     *  last slot of the previous day is still running. */
    Slot slotAt(int minuteOfDay) {
        if (slots.isEmpty()) {
            return null;
        }
        Slot current = slots.get(slots.size() - 1);
        for (Slot slot : slots) {
            if (slot.startMinutes > minuteOfDay) {
                break;
            }
            current = slot;
        }
        return current;
    }

    Slot slotAfter(Slot current) {
        if (slots.isEmpty()) {
            return null;
        }
        int index = slots.indexOf(current);
        return slots.get((index + 1) % slots.size());
    }

    Slot slotAt(long nowMillis, UtcOffsetTimeline clock) {
        return slotAt(clock.minuteOfDayAt(nowMillis));
    }

    /** The first slot start strictly after {@code nowMillis}, or -1 without slots. */
    long nextChangeMillis(long nowMillis, UtcOffsetTimeline clock) {
        if (slots.isEmpty()) {
            return -1L;
        }
        // Walk one constant-offset stretch at a time: a daylight-saving change
        // moves every later start, and its jump can itself cross a start.
        long from = nowMillis;
        for (int stretch = 0; stretch <= UtcOffsetTimeline.MAX_TRANSITIONS; stretch++) {
            int offset = clock.offsetMinutesAt(from);
            long start = nextStartAfter(from, offset);
            long change = clock.nextChangeAfter(from);
            if (change < 0 || start < change) {
                return start;
            }
            if (restartsBetween(
                    UtcOffsetTimeline.minuteOfDay(change - 1, offset),
                    clock.minuteOfDayAt(change))) {
                return change;
            }
            from = change;
        }
        return -1L;
    }

    private long nextStartAfter(long nowMillis, int offsetMinutes) {
        long local = nowMillis + offsetMinutes * MINUTE_MS;
        long intoDay = local % DAY_MS;
        if (intoDay < 0) {
            intoDay += DAY_MS;
        }
        long dayStart = local - intoDay;
        long next = Long.MAX_VALUE;
        for (Slot slot : slots) {
            long start = dayStart + slot.startMinutes * MINUTE_MS;
            if (start <= local) {
                start += DAY_MS;
            }
            next = Math.min(next, start);
        }
        return next - offsetMinutes * MINUTE_MS;
    }

    /** Whether the local clock moving between two minutes of the day restarts a slot. */
    private boolean restartsBetween(int previousMinute, int currentMinute) {
        int forward = cyclicDistance(previousMinute, currentMinute);
        if (forward > UtcOffsetTimeline.DAY_MINUTES / 2) {
            // The clock moved back: only landing in a different slot is a change.
            return slotAt(currentMinute) != slotAt(previousMinute);
        }
        for (Slot slot : slots) {
            int distance = cyclicDistance(previousMinute, slot.startMinutes);
            if (distance >= 1 && distance <= forward) {
                return true;
            }
        }
        return false;
    }

    private static int cyclicDistance(int fromMinute, int toMinute) {
        int distance = (toMinute - fromMinute) % UtcOffsetTimeline.DAY_MINUTES;
        return distance < 0 ? distance + UtcOffsetTimeline.DAY_MINUTES : distance;
    }

    /**
     * What the glass should show: an unexpired manual hold, else the timetable;
     * with the schedule off, the manually selected video.
     */
    static String effectiveId(
            BackgroundVideoSchedule schedule,
            String activeId,
            String holdId,
            long holdUntilMillis,
            long nowMillis,
            UtcOffsetTimeline clock) {
        String selected = BackgroundVideoSelection.validId(activeId) ? activeId : "";
        if (schedule == null || !schedule.isActive()) {
            return selected;
        }
        if (holdActive(holdId, holdUntilMillis, nowMillis)) {
            return holdId;
        }
        return schedule.slotAt(nowMillis, clock).videoId;
    }

    static boolean holdActive(String holdId, long holdUntilMillis, long nowMillis) {
        return BackgroundVideoSelection.validId(holdId) && nowMillis < holdUntilMillis;
    }

    static String formatMinutes(int minuteOfDay) {
        return String.format(Locale.US, "%02d:%02d", minuteOfDay / 60, minuteOfDay % 60);
    }
}
