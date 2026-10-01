package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayDeque;

final class DashboardDiagnostics {
    private static final int MAX_CONSOLE_ENTRIES = 5;
    private static final int MAX_CONSOLE_TEXT = 300;
    private static final ArrayDeque<JSONObject> RECENT_CONSOLE_ERRORS = new ArrayDeque<>();
    private static long consoleErrors;
    private static long consoleWarnings;
    private static volatile String phase = "not-created";
    private static volatile String url = "";
    private static volatile String detail = "";
    private static volatile String pageProbe = "";
    private static volatile boolean pageComplete;
    private static volatile boolean rendererPresent;
    private static volatile String lastFailurePhase = "";
    private static volatile String lastFailureUrl = "";
    private static volatile String lastFailureDetail = "";
    private static volatile int launchAttempts;
    private static volatile String launchDetail = "";

    private DashboardDiagnostics() {
    }

    static void record(String nextPhase, String nextUrl, String nextDetail) {
        phase = nextPhase;
        url = nextUrl == null ? "" : nextUrl;
        detail = nextDetail == null ? "" : nextDetail;
        pageProbe = "";
        pageComplete = false;
        rendererPresent = false;
        if ("loading".equals(nextPhase) || "retrying".equals(nextPhase)) {
            lastFailurePhase = "";
            lastFailureUrl = "";
            lastFailureDetail = "";
        }
    }

    static void recordFailure(String nextPhase, String nextUrl, String nextDetail) {
        record(nextPhase, nextUrl, nextDetail);
        lastFailurePhase = phase;
        lastFailureUrl = url;
        lastFailureDetail = detail;
    }

    static void recordPageProbe(String value) {
        pageProbe = value == null ? "" : value;
        pageComplete = pageProbe.contains("complete|");
        rendererPresent = pageProbe.endsWith("|object\"");
    }

    static boolean activityCreated() {
        return !"not-created".equals(phase);
    }

    static void recordLaunchAttempt(int attempts, String nextDetail) {
        launchAttempts = attempts;
        launchDetail = nextDetail == null ? "" : nextDetail;
    }

    /**
     * Counts what the dashboard page reports on its console. An uncaught
     * script error arrives here, which is how a page that the Mirror's old
     * browser engine cannot run is noticed without looking at the glass.
     */
    static void recordConsole(String level, String message, String source, int line) {
        boolean error = "ERROR".equals(level);
        if (!error && !"WARNING".equals(level)) {
            return;
        }
        synchronized (RECENT_CONSOLE_ERRORS) {
            if (!error) {
                consoleWarnings++;
                return;
            }
            consoleErrors++;
            try {
                RECENT_CONSOLE_ERRORS.addLast(new JSONObject()
                        .put("at", System.currentTimeMillis())
                        .put("message", clip(message))
                        .put("source", clip(source))
                        .put("line", line));
            } catch (JSONException impossible) {
                return;
            }
            while (RECENT_CONSOLE_ERRORS.size() > MAX_CONSOLE_ENTRIES) {
                RECENT_CONSOLE_ERRORS.removeFirst();
            }
        }
    }

    static long consoleErrors() {
        synchronized (RECENT_CONSOLE_ERRORS) {
            return consoleErrors;
        }
    }

    static JSONObject snapshot() throws JSONException {
        JSONArray recent = new JSONArray();
        long errors;
        long warnings;
        synchronized (RECENT_CONSOLE_ERRORS) {
            errors = consoleErrors;
            warnings = consoleWarnings;
            for (JSONObject entry : RECENT_CONSOLE_ERRORS) {
                recent.put(entry);
            }
        }
        return new JSONObject()
                .put("phase", phase)
                .put("url", url)
                .put("detail", detail)
                .put("pageProbe", pageProbe)
                .put("pageComplete", pageComplete)
                .put("rendererPresent", rendererPresent)
                .put("lastFailurePhase", lastFailurePhase)
                .put("lastFailureUrl", lastFailureUrl)
                .put("lastFailureDetail", lastFailureDetail)
                .put("launchAttempts", launchAttempts)
                .put("launchDetail", launchDetail)
                .put("consoleErrors", errors)
                .put("consoleWarnings", warnings)
                .put("recentConsoleErrors", recent);
    }

    private static String clip(String value) {
        if (value == null) {
            return "";
        }
        return value.length() <= MAX_CONSOLE_TEXT
                ? value
                : value.substring(0, MAX_CONSOLE_TEXT - 3) + "...";
    }

    static JSONObject publicSnapshot() throws JSONException {
        return new JSONObject()
                .put("phase", phase)
                .put("pageComplete", pageComplete)
                .put("rendererPresent", rendererPresent)
                .put("hasLoadFailure", !lastFailurePhase.isEmpty())
                .put("launchAttempts", launchAttempts);
    }
}
