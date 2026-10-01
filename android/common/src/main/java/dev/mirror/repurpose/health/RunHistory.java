package dev.mirror.repurpose.health;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** What a process can tell about how its predecessor ended, and why it crashed. */
public final class RunHistory {
    public static final String END_CRASH = "crash";
    public static final String END_UPDATE = "update";
    public static final String END_REBOOT = "reboot";
    public static final String END_KILLED = "killed";

    /** A process ended this soon after it started never did any work. */
    static final long EARLY_STOP_MS = 10_000L;

    static final int MAX_TRACE_LINES = 48;
    static final int MAX_FRAMES_PER_THROWABLE = 12;
    static final int MAX_CAUSES = 4;
    static final int MAX_LINE_LENGTH = 300;

    private RunHistory() {
    }

    /**
     * Whether the device restarted between two runs. The kernel gives every
     * boot its own identifier, which settles it. Without one the only sign is
     * a monotonic clock that went backwards, which misses a restart that
     * follows a short run.
     */
    public static boolean rebooted(
            String previousBootId,
            String currentBootId,
            long previousLastSeenElapsed,
            long currentElapsed) {
        if (known(previousBootId) && known(currentBootId)) {
            return !previousBootId.equals(currentBootId);
        }
        return currentElapsed < previousLastSeenElapsed;
    }

    /**
     * How the previous process ended. A recorded crash is certain; otherwise a
     * different version means an update, a restarted device explains itself,
     * and anything else is the system (or a native fault) ending the process
     * without warning.
     */
    public static String previousEnd(
            boolean crashRecorded,
            long previousVersionCode,
            long currentVersionCode,
            boolean rebooted) {
        if (crashRecorded) {
            return END_CRASH;
        }
        if (previousVersionCode != currentVersionCode) {
            return END_UPDATE;
        }
        return rebooted ? END_REBOOT : END_KILLED;
    }

    /**
     * Whether the previous process was ended before it did any work. Android 6
     * does this once whenever it replaces a HOME app: it starts the new
     * version at once and stops it again as the installation completes. Such
     * a process is counted rather than reported as the previous run, so the
     * report keeps saying why the app really restarted.
     */
    public static boolean stoppedWhileStarting(
            String end,
            long previousStartedElapsed,
            long currentElapsed) {
        return END_KILLED.equals(end)
                && previousStartedElapsed >= 0
                && currentElapsed >= previousStartedElapsed
                && currentElapsed - previousStartedElapsed < EARLY_STOP_MS;
    }

    private static boolean known(String bootId) {
        return bootId != null && !bootId.isEmpty();
    }

    /** A bounded description of an uncaught exception that survives the process. */
    public static JSONObject describeCrash(
            Throwable error,
            String threadName,
            long atMillis,
            long runId,
            String versionName,
            long versionCode,
            long processUptimeMillis) throws JSONException {
        JSONArray trace = new JSONArray();
        Throwable current = error;
        for (int depth = 0; current != null && depth <= MAX_CAUSES; depth++) {
            if (trace.length() >= MAX_TRACE_LINES) {
                break;
            }
            trace.put(shorten((depth == 0 ? "" : "Caused by: ") + current));
            StackTraceElement[] frames = current.getStackTrace();
            for (int index = 0; index < frames.length; index++) {
                if (index >= MAX_FRAMES_PER_THROWABLE || trace.length() >= MAX_TRACE_LINES) {
                    trace.put("... " + (frames.length - index) + " more");
                    break;
                }
                trace.put(shorten("at " + frames[index]));
            }
            Throwable cause = current.getCause();
            current = cause == current ? null : cause;
        }
        String message = error.getMessage();
        return new JSONObject()
                .put("at", atMillis)
                .put("runId", runId)
                .put("thread", threadName == null ? "" : threadName)
                .put("exception", error.getClass().getName())
                .put("message", message == null ? "" : shorten(message))
                .put("versionName", versionName == null ? "" : versionName)
                .put("versionCode", versionCode)
                .put("processUptimeSeconds", Math.max(0L, processUptimeMillis) / 1000L)
                .put("trace", trace);
    }

    static String shorten(String line) {
        return line.length() <= MAX_LINE_LENGTH
                ? line
                : line.substring(0, MAX_LINE_LENGTH - 3) + "...";
    }
}
