package dev.mirror.repurpose;

import org.json.JSONException;
import org.json.JSONObject;

/** Requests the control API failed to handle, kept for the health report. */
final class ApiDiagnostics {
    private static long unhandledErrors;
    private static JSONObject lastUnhandled;

    private ApiDiagnostics() {
    }

    static synchronized void recordUnhandled(String method, String path, Throwable error) {
        unhandledErrors++;
        StackTraceElement[] frames = error.getStackTrace();
        try {
            lastUnhandled = new JSONObject()
                    .put("at", System.currentTimeMillis())
                    .put("method", method == null ? "" : method)
                    .put("path", path == null ? "" : path)
                    .put("exception", error.getClass().getName())
                    .put("where", frames.length == 0 ? "" : frames[0].toString());
        } catch (JSONException impossible) {
            lastUnhandled = null;
        }
    }

    static synchronized JSONObject snapshot() throws JSONException {
        return new JSONObject()
                .put("unhandledErrors", unhandledErrors)
                .put("lastUnhandled", lastUnhandled == null ? JSONObject.NULL : lastUnhandled);
    }
}
