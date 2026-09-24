package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class DisplayFadePolicyTest {
    @Test
    public void overlayIsOpaqueOnlyWhenFullyAsleep() {
        assertEquals(1f, DisplayFadePolicy.overlayAlpha(0f), 0f);
        assertEquals(0.25f, DisplayFadePolicy.overlayAlpha(0.75f), 1e-6f);
        assertEquals(0f, DisplayFadePolicy.overlayAlpha(1f), 0f);
        assertEquals(1f, DisplayFadePolicy.overlayAlpha(Float.NaN), 0f);
    }

    @Test
    public void backlightScalesToTrueZero() {
        assertEquals(0, DisplayFadePolicy.backlightLevel(180, 0f));
        assertEquals(90, DisplayFadePolicy.backlightLevel(180, 0.5f));
        assertEquals(180, DisplayFadePolicy.backlightLevel(180, 1f));
        assertEquals(255, DisplayFadePolicy.backlightLevel(400, 2f));
        assertEquals(0, DisplayFadePolicy.backlightLevel(-5, 1f));
    }

    @Test
    public void windowBrightnessSurvivesAndroidTruncation() {
        assertEquals(0f, DisplayFadePolicy.windowBrightness(0), 0f);
        for (int level = 1; level <= DisplayFadePolicy.MAX_BACKLIGHT; level++) {
            float value = DisplayFadePolicy.windowBrightness(level);
            assertTrue(value <= 1f);
            assertEquals(level, (int) (value * DisplayFadePolicy.MAX_BACKLIGHT));
        }
    }

    @Test
    public void reversalsKeepAConsistentPace() {
        assertEquals(DisplayFadePolicy.SLEEP_FADE_MS, DisplayFadePolicy.duration(1f, 0f));
        assertEquals(DisplayFadePolicy.WAKE_FADE_MS, DisplayFadePolicy.duration(0f, 1f));
        assertEquals(DisplayFadePolicy.WAKE_FADE_MS / 4, DisplayFadePolicy.duration(0.75f, 1f));
        assertEquals(0L, DisplayFadePolicy.duration(1f, 1f));
    }

    @Test
    public void fullScaleWakeStaysWithinTheSystemRamp() {
        double peakLevelsPerSecond = DisplayFadePolicy.MAX_BACKLIGHT
                * (Math.PI / 2d)
                / (DisplayFadePolicy.WAKE_FADE_MS / 1000d);
        assertTrue(peakLevelsPerSecond <= 201d);
    }

    @Test
    public void sleepSideEffectsWaitForTheFade() {
        assertTrue(DisplayFadePolicy.sleepCommitFallbackMs() > DisplayFadePolicy.SLEEP_FADE_MS);
    }
}
