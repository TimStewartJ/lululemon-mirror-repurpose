package dev.mirror.repurpose;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class AmbientVideoPolicyTest {
    @Test
    public void builtInDashboardPlaysWhileAwakeAndResumed() {
        AmbientVideoPolicy.State state =
                AmbientVideoPolicy.desiredState(true, false, true, false);

        assertTrue(state.enabled);
        assertTrue(state.playing);
    }

    @Test
    public void sleepingKeepsVideoPreparedButActivityPauseReleasesIt() {
        AmbientVideoPolicy.State sleeping =
                AmbientVideoPolicy.desiredState(true, false, true, true);
        AmbientVideoPolicy.State paused =
                AmbientVideoPolicy.desiredState(true, false, false, false);

        assertTrue(sleeping.enabled);
        assertFalse(sleeping.playing);
        assertFalse(paused.enabled);
        assertFalse(paused.playing);
    }

    @Test
    public void presentationDisablesAmbientDecoder() {
        AmbientVideoPolicy.State state =
                AmbientVideoPolicy.desiredState(true, true, true, false);

        assertFalse(state.enabled);
        assertFalse(state.playing);
    }

    @Test
    public void customDashboardDisablesAmbientVideo() {
        AmbientVideoPolicy.State state =
                AmbientVideoPolicy.desiredState(false, false, true, false);

        assertFalse(state.enabled);
        assertFalse(state.playing);
    }
}
