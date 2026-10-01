package dev.mirror.repurpose.health;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public final class RunHistoryTest {
    @Test
    public void aRecordedCrashOutranksEveryOtherExplanation() {
        assertEquals(RunHistory.END_CRASH, RunHistory.previousEnd(true, 71, 72, true));
        assertEquals(RunHistory.END_CRASH, RunHistory.previousEnd(true, 72, 72, false));
    }

    @Test
    public void aNewVersionMeansTheAppWasUpdated() {
        assertEquals(RunHistory.END_UPDATE, RunHistory.previousEnd(false, 71, 72, false));
        // An update followed by a restart is still reported as the update.
        assertEquals(RunHistory.END_UPDATE, RunHistory.previousEnd(false, 71, 72, true));
    }

    @Test
    public void aRestartedDeviceExplainsItself() {
        assertEquals(RunHistory.END_REBOOT, RunHistory.previousEnd(false, 72, 72, true));
    }

    @Test
    public void otherwiseTheProcessWasEndedWithoutWarning() {
        assertEquals(RunHistory.END_KILLED, RunHistory.previousEnd(false, 72, 72, false));
    }

    @Test
    public void theBootIdentifierSettlesWhetherTheDeviceRestarted() {
        // The same boot, whatever the clocks say.
        assertFalse(RunHistory.rebooted("boot-a", "boot-a", 9_000, 100));
        // A restart soon after boot: the monotonic clock did not go backwards.
        assertTrue(RunHistory.rebooted("boot-a", "boot-b", 25_000, 27_000));
    }

    @Test
    public void withoutABootIdentifierTheMonotonicClockDecides() {
        assertTrue(RunHistory.rebooted(null, null, 9_000, 100));
        assertFalse(RunHistory.rebooted(null, null, 100, 9_000));
        assertTrue(RunHistory.rebooted("", "boot-b", 9_000, 100));
        assertFalse(RunHistory.rebooted("boot-a", null, 100, 100));
    }

    @Test
    public void aProcessStoppedSecondsAfterStartingIsNotARun() {
        // Measured on a Mirror during an update: stopped 490 ms after starting.
        assertTrue(RunHistory.stoppedWhileStarting(
                RunHistory.END_KILLED, 526_000_000L, 526_000_490L));
        assertTrue(RunHistory.stoppedWhileStarting(
                RunHistory.END_KILLED, 1_000, 1_000 + RunHistory.EARLY_STOP_MS - 1));
    }

    @Test
    public void anyOtherEndingIsReportedAsThePreviousRun() {
        long started = 60_000;
        assertFalse(RunHistory.stoppedWhileStarting(
                RunHistory.END_KILLED, started, started + RunHistory.EARLY_STOP_MS));
        assertFalse(RunHistory.stoppedWhileStarting(RunHistory.END_CRASH, started, started + 400));
        assertFalse(RunHistory.stoppedWhileStarting(RunHistory.END_UPDATE, started, started + 400));
        assertFalse(RunHistory.stoppedWhileStarting(RunHistory.END_REBOOT, started, started + 400));
        // Recorded by a version that did not note when the process started.
        assertFalse(RunHistory.stoppedWhileStarting(RunHistory.END_KILLED, -1, 400));
        // A clock that went backwards is never a short run.
        assertFalse(RunHistory.stoppedWhileStarting(RunHistory.END_KILLED, started, started - 1));
    }

    @Test
    public void describesACrashWithItsCauses() throws Exception {
        Exception failure = new IllegalStateException(
                "Unable to initialize APK validation",
                new java.io.IOException("No space left on device"));

        JSONObject crash = RunHistory.describeCrash(
                failure, "main", 1_790_000_000_000L, 7, "1.0.1", 2, 3_500);

        assertEquals(1_790_000_000_000L, crash.getLong("at"));
        assertEquals(7, crash.getLong("runId"));
        assertEquals("main", crash.getString("thread"));
        assertEquals("java.lang.IllegalStateException", crash.getString("exception"));
        assertEquals("Unable to initialize APK validation", crash.getString("message"));
        assertEquals("1.0.1", crash.getString("versionName"));
        assertEquals(2, crash.getLong("versionCode"));
        assertEquals(3, crash.getLong("processUptimeSeconds"));
        JSONArray trace = crash.getJSONArray("trace");
        assertEquals(
                "java.lang.IllegalStateException: Unable to initialize APK validation",
                trace.getString(0));
        assertTrue(trace.getString(1), trace.getString(1).startsWith("at "));
        assertTrue(trace.toString(), trace.toString().contains(
                "Caused by: java.io.IOException: No space left on device"));
    }

    @Test
    public void aCrashRecordStaysSmallHoweverDeepTheFailure() throws Exception {
        StringBuilder longMessage = new StringBuilder();
        for (int index = 0; index < 5_000; index++) {
            longMessage.append('x');
        }
        Throwable failure = new RuntimeException("root cause");
        for (int depth = 0; depth < 20; depth++) {
            failure = new RuntimeException("layer " + depth, failure);
        }
        failure = new RuntimeException(longMessage.toString(), failure);

        JSONObject crash = RunHistory.describeCrash(failure, null, 0, 1, null, 1, -5);
        JSONArray trace = crash.getJSONArray("trace");

        assertTrue("lines: " + trace.length(), trace.length() <= RunHistory.MAX_TRACE_LINES + 1);
        for (int index = 0; index < trace.length(); index++) {
            assertTrue(trace.getString(index).length() <= RunHistory.MAX_LINE_LENGTH);
        }
        assertEquals(RunHistory.MAX_LINE_LENGTH, crash.getString("message").length());
        assertTrue(crash.toString().length() < 16 * 1024);
        assertEquals("", crash.getString("thread"));
        assertEquals("", crash.getString("versionName"));
        assertEquals(0, crash.getLong("processUptimeSeconds"));
    }

    @Test
    public void aSelfReferencingCauseDoesNotLoop() throws Exception {
        Throwable failure = new RuntimeException("alone") {
            @Override
            public synchronized Throwable getCause() {
                return this;
            }
        };

        JSONObject crash = RunHistory.describeCrash(failure, "worker", 0, 1, "1", 1, 0);

        assertTrue(crash.getJSONArray("trace").length() <= RunHistory.MAX_FRAMES_PER_THROWABLE + 2);
        assertEquals("alone", crash.getString("message"));
    }

    @Test
    public void shortensOnlyLinesThatAreTooLong() {
        assertEquals("short", RunHistory.shorten("short"));
        StringBuilder exact = new StringBuilder();
        for (int index = 0; index < RunHistory.MAX_LINE_LENGTH; index++) {
            exact.append('a');
        }
        assertEquals(exact.toString(), RunHistory.shorten(exact.toString()));
        String shortened = RunHistory.shorten(exact + "b");
        assertEquals(RunHistory.MAX_LINE_LENGTH, shortened.length());
        assertTrue(shortened.endsWith("..."));
    }
}
