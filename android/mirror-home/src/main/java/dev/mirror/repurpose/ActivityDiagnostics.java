package dev.mirror.repurpose;

import android.os.SystemClock;

import org.json.JSONException;
import org.json.JSONObject;

/**
 * Whether the dashboard is actually in front. Home keeps answering its API
 * while a system prompt covers it, so "responding" does not mean "showing";
 * this records the difference for the health report.
 */
final class ActivityDiagnostics {
    private static volatile boolean created;
    private static volatile boolean resumed;
    private static volatile boolean focused;
    private static volatile int creates;
    private static volatile int pauses;
    private static volatile int focusLosses;
    private static volatile long lastResumedAt;
    private static volatile long lastPausedAt;
    private static volatile long pausedSinceElapsed = -1L;
    private static volatile long unfocusedSinceElapsed = -1L;

    private ActivityDiagnostics() {
    }

    static void created() {
        created = true;
        creates++;
    }

    static void destroyed() {
        created = false;
        resumed = false;
        focused = false;
    }

    static void resumed() {
        resumed = true;
        lastResumedAt = System.currentTimeMillis();
        pausedSinceElapsed = -1L;
    }

    static void paused() {
        paused(SystemClock.elapsedRealtime());
    }

    static void paused(long elapsedRealtime) {
        resumed = false;
        pauses++;
        lastPausedAt = System.currentTimeMillis();
        pausedSinceElapsed = elapsedRealtime;
    }

    static void focusChanged(boolean hasFocus) {
        focusChanged(hasFocus, SystemClock.elapsedRealtime());
    }

    static void focusChanged(boolean hasFocus, long elapsedRealtime) {
        if (focused && !hasFocus) {
            focusLosses++;
        }
        focused = hasFocus;
        if (hasFocus) {
            unfocusedSinceElapsed = -1L;
        } else if (unfocusedSinceElapsed < 0) {
            // A repeated report must not restart the count.
            unfocusedSinceElapsed = elapsedRealtime;
        }
    }

    /** In front and receiving input: nothing is drawn over the dashboard. */
    static boolean showing() {
        return resumed && focused;
    }

    static JSONObject snapshot() throws JSONException {
        return snapshot(SystemClock.elapsedRealtime());
    }

    static JSONObject snapshot(long elapsed) throws JSONException {
        long pausedSince = pausedSinceElapsed;
        long unfocusedSince = unfocusedSinceElapsed;
        return new JSONObject()
                .put("created", created)
                .put("resumed", resumed)
                .put("focused", focused)
                .put("showing", showing())
                .put("creates", creates)
                .put("pauses", pauses)
                .put("focusLosses", focusLosses)
                .put("lastResumedAt", lastResumedAt == 0 ? JSONObject.NULL : lastResumedAt)
                .put("lastPausedAt", lastPausedAt == 0 ? JSONObject.NULL : lastPausedAt)
                .put(
                        "pausedForSeconds",
                        resumed || pausedSince < 0 ? 0L : (elapsed - pausedSince) / 1000L)
                .put(
                        "unfocusedForSeconds",
                        focused || unfocusedSince < 0 ? 0L : (elapsed - unfocusedSince) / 1000L);
    }
}
