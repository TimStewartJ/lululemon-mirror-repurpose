package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public final class DashboardDiagnosticsTest {
    private static final String PAGE = "http://127.0.0.1:8787/dashboard/custom.js";

    @Test
    public void countsErrorsAndWarningsAndIgnoresOrdinaryLogging() throws Exception {
        JSONObject before = DashboardDiagnostics.snapshot();

        DashboardDiagnostics.recordConsole("ERROR", "Uncaught SyntaxError: Unexpected token =>", PAGE, 12);
        DashboardDiagnostics.recordConsole("WARNING", "Synchronous XMLHttpRequest is deprecated", PAGE, 40);
        DashboardDiagnostics.recordConsole("WARNING", "again", PAGE, 41);
        DashboardDiagnostics.recordConsole("LOG", "refreshed", PAGE, 50);
        DashboardDiagnostics.recordConsole("TIP", "tip", PAGE, 51);
        DashboardDiagnostics.recordConsole("DEBUG", "debug", PAGE, 52);
        JSONObject after = DashboardDiagnostics.snapshot();

        assertEquals(before.getLong("consoleErrors") + 1, after.getLong("consoleErrors"));
        assertEquals(before.getLong("consoleWarnings") + 2, after.getLong("consoleWarnings"));
        assertEquals(after.getLong("consoleErrors"), DashboardDiagnostics.consoleErrors());
        JSONArray recent = after.getJSONArray("recentConsoleErrors");
        JSONObject last = recent.getJSONObject(recent.length() - 1);
        assertEquals("Uncaught SyntaxError: Unexpected token =>", last.getString("message"));
        assertEquals(PAGE, last.getString("source"));
        assertEquals(12, last.getInt("line"));
        assertTrue(last.getLong("at") > 0);
    }

    @Test
    public void keepsOnlyTheMostRecentErrors() throws Exception {
        long before = DashboardDiagnostics.consoleErrors();

        for (int index = 1; index <= 8; index++) {
            DashboardDiagnostics.recordConsole("ERROR", "failure " + index, PAGE, index);
        }
        JSONObject snapshot = DashboardDiagnostics.snapshot();
        JSONArray recent = snapshot.getJSONArray("recentConsoleErrors");

        assertEquals(before + 8, snapshot.getLong("consoleErrors"));
        assertEquals(5, recent.length());
        assertEquals("failure 4", recent.getJSONObject(0).getString("message"));
        assertEquals("failure 8", recent.getJSONObject(4).getString("message"));
    }

    @Test
    public void shortensLongMessagesAndToleratesMissingOnes() throws Exception {
        StringBuilder longMessage = new StringBuilder();
        for (int index = 0; index < 2_000; index++) {
            longMessage.append('e');
        }

        DashboardDiagnostics.recordConsole("ERROR", longMessage.toString(), null, 0);
        JSONArray recent = DashboardDiagnostics.snapshot().getJSONArray("recentConsoleErrors");
        JSONObject clipped = recent.getJSONObject(recent.length() - 1);

        assertEquals(300, clipped.getString("message").length());
        assertTrue(clipped.getString("message").endsWith("..."));
        assertEquals("", clipped.getString("source"));

        DashboardDiagnostics.recordConsole("ERROR", null, null, 0);
        recent = DashboardDiagnostics.snapshot().getJSONArray("recentConsoleErrors");
        assertEquals("", recent.getJSONObject(recent.length() - 1).getString("message"));
    }

    @Test
    public void thePublicSummaryNamesNoPageOrError() throws Exception {
        DashboardDiagnostics.recordConsole("ERROR", "secret detail", PAGE, 1);

        String summary = DashboardDiagnostics.publicSnapshot().toString();

        assertEquals(-1, summary.indexOf("secret detail"));
        assertEquals(-1, summary.indexOf("consoleErrors"));
        assertEquals(-1, summary.indexOf("url"));
    }
}
