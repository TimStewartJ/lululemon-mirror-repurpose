package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public final class ApiDiagnosticsTest {
    @Test
    public void remembersTheLastRequestItCouldNotHandle() throws Exception {
        long before = ApiDiagnostics.snapshot().getLong("unhandledErrors");

        ApiDiagnostics.recordUnhandled("GET", "/api/v1/status", new NullPointerException("brightness"));
        ApiDiagnostics.recordUnhandled("PUT", "/api/v1/automation", new IllegalStateException("later"));
        JSONObject snapshot = ApiDiagnostics.snapshot();
        JSONObject last = snapshot.getJSONObject("lastUnhandled");

        assertEquals(before + 2, snapshot.getLong("unhandledErrors"));
        assertEquals("PUT", last.getString("method"));
        assertEquals("/api/v1/automation", last.getString("path"));
        assertEquals("java.lang.IllegalStateException", last.getString("exception"));
        assertTrue(last.getString("where"), last.getString("where").contains("ApiDiagnosticsTest"));
        assertTrue(last.getLong("at") > 0);
        // The message may quote request data, so it is not kept.
        assertEquals(-1, last.toString().indexOf("later"));
    }

    @Test
    public void toleratesAnErrorWithoutDetails() throws Exception {
        Throwable bare = new RuntimeException() {
            @Override
            public StackTraceElement[] getStackTrace() {
                return new StackTraceElement[0];
            }
        };

        ApiDiagnostics.recordUnhandled(null, null, bare);
        JSONObject last = ApiDiagnostics.snapshot().getJSONObject("lastUnhandled");

        assertEquals("", last.getString("method"));
        assertEquals("", last.getString("path"));
        assertEquals("", last.getString("where"));
    }
}
