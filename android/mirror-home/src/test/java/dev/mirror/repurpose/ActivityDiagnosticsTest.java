package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Before;
import org.junit.Test;

public final class ActivityDiagnosticsTest {
    @Before
    public void startInFront() {
        ActivityDiagnostics.destroyed();
        ActivityDiagnostics.created();
        ActivityDiagnostics.resumed();
        ActivityDiagnostics.focusChanged(true, 1_000);
    }

    @Test
    public void showingNeedsBothResumeAndFocus() throws Exception {
        assertTrue(ActivityDiagnostics.showing());
        JSONObject snapshot = ActivityDiagnostics.snapshot(2_000);
        assertTrue(snapshot.getBoolean("created"));
        assertTrue(snapshot.getBoolean("resumed"));
        assertTrue(snapshot.getBoolean("focused"));
        assertTrue(snapshot.getBoolean("showing"));
        assertEquals(0, snapshot.getLong("pausedForSeconds"));
        assertEquals(0, snapshot.getLong("unfocusedForSeconds"));

        ActivityDiagnostics.focusChanged(false, 3_000);
        assertFalse(ActivityDiagnostics.showing());

        ActivityDiagnostics.focusChanged(true, 4_000);
        ActivityDiagnostics.paused(5_000);
        assertFalse(ActivityDiagnostics.showing());
    }

    @Test
    public void aPromptOverTheDashboardIsTimedFromWhenItAppeared() throws Exception {
        int lossesBefore = ActivityDiagnostics.snapshot(1_000).getInt("focusLosses");

        ActivityDiagnostics.focusChanged(false, 10_000);
        JSONObject covered = ActivityDiagnostics.snapshot(5_410_000);

        assertTrue(covered.getBoolean("resumed"));
        assertFalse(covered.getBoolean("focused"));
        assertFalse(covered.getBoolean("showing"));
        assertEquals(5_400, covered.getLong("unfocusedForSeconds"));
        assertEquals(0, covered.getLong("pausedForSeconds"));
        assertEquals(lossesBefore + 1, covered.getInt("focusLosses"));

        ActivityDiagnostics.focusChanged(true, 5_500_000);
        JSONObject back = ActivityDiagnostics.snapshot(5_600_000);
        assertTrue(back.getBoolean("showing"));
        assertEquals(0, back.getLong("unfocusedForSeconds"));
    }

    @Test
    public void aRepeatedFocusLossDoesNotRestartTheCount() throws Exception {
        int lossesBefore = ActivityDiagnostics.snapshot(1_000).getInt("focusLosses");

        ActivityDiagnostics.focusChanged(false, 10_000);
        ActivityDiagnostics.focusChanged(false, 60_000);
        JSONObject covered = ActivityDiagnostics.snapshot(130_000);

        assertEquals(120, covered.getLong("unfocusedForSeconds"));
        assertEquals(lossesBefore + 1, covered.getInt("focusLosses"));
    }

    @Test
    public void aPausedDashboardIsTimedUntilItResumes() throws Exception {
        int pausesBefore = ActivityDiagnostics.snapshot(1_000).getInt("pauses");

        ActivityDiagnostics.paused(20_000);
        JSONObject paused = ActivityDiagnostics.snapshot(140_000);

        assertFalse(paused.getBoolean("resumed"));
        assertFalse(paused.getBoolean("showing"));
        assertEquals(120, paused.getLong("pausedForSeconds"));
        assertEquals(pausesBefore + 1, paused.getInt("pauses"));

        ActivityDiagnostics.resumed();
        JSONObject resumed = ActivityDiagnostics.snapshot(150_000);
        assertTrue(resumed.getBoolean("showing"));
        assertEquals(0, resumed.getLong("pausedForSeconds"));
    }

    @Test
    public void aDestroyedActivityIsNotShowingAndKeepsCounting() throws Exception {
        ActivityDiagnostics.paused(30_000);
        ActivityDiagnostics.destroyed();
        JSONObject gone = ActivityDiagnostics.snapshot(90_000);

        assertFalse(gone.getBoolean("created"));
        assertFalse(gone.getBoolean("focused"));
        assertFalse(gone.getBoolean("showing"));
        assertEquals(60, gone.getLong("pausedForSeconds"));
    }

    @Test
    public void everyCreationIsCounted() throws Exception {
        int createsBefore = ActivityDiagnostics.snapshot(1_000).getInt("creates");

        ActivityDiagnostics.destroyed();
        ActivityDiagnostics.created();

        assertEquals(createsBefore + 1, ActivityDiagnostics.snapshot(2_000).getInt("creates"));
    }
}
