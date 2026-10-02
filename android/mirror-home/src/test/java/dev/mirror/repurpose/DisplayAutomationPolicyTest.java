package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class DisplayAutomationPolicyTest {
    @Test
    public void quietHoursAlwaysSleep() {
        assertTrue(DisplayAutomationPolicy.shouldSleep(
                false, true, false, true, 0, 300_000));
    }

    @Test
    public void missingCameraFailsOpenInsideWakeWindow() {
        assertFalse(DisplayAutomationPolicy.shouldSleep(
                true, true, false, false, Long.MAX_VALUE, 300_000));
    }

    @Test
    public void activeMediaSuppressesInactivitySleep() {
        assertFalse(DisplayAutomationPolicy.shouldSleep(
                true, true, true, true, 900_000, 300_000));
    }

    @Test
    public void brightnessStepsStayWithinTheUsefulRange() {
        assertEquals(230, DisplayAutomationPolicy.steppedBrightness(190, 40));
        assertEquals(255, DisplayAutomationPolicy.steppedBrightness(230, 40));
        assertEquals(255, DisplayAutomationPolicy.steppedBrightness(255, 40));
        assertEquals(150, DisplayAutomationPolicy.steppedBrightness(190, -40));
        assertEquals(15, DisplayAutomationPolicy.steppedBrightness(30, -40));
        assertEquals(15, DisplayAutomationPolicy.steppedBrightness(15, -40));
        assertEquals(15, DisplayAutomationPolicy.steppedBrightness(1, -40));
    }

    @Test
    public void inactivitySleepsOnlyAfterTimeout() {
        assertFalse(DisplayAutomationPolicy.shouldSleep(
                true, true, true, false, 299_999, 300_000));
        assertTrue(DisplayAutomationPolicy.shouldSleep(
                true, true, true, false, 300_000, 300_000));
    }
}
