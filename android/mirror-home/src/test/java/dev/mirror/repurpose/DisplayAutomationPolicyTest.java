package dev.mirror.repurpose;

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
    public void inactivitySleepsOnlyAfterTimeout() {
        assertFalse(DisplayAutomationPolicy.shouldSleep(
                true, true, true, false, 299_999, 300_000));
        assertTrue(DisplayAutomationPolicy.shouldSleep(
                true, true, true, false, 300_000, 300_000));
    }
}
