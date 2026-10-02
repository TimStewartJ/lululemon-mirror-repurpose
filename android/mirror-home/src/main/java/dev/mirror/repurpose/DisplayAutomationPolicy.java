package dev.mirror.repurpose;

final class DisplayAutomationPolicy {
    /** As dim as a request at the Mirror makes the display: still readable in a lit room. */
    static final int MIN_STEPPED_BRIGHTNESS = 15;
    static final int MAX_BRIGHTNESS = 255;

    private DisplayAutomationPolicy() {
    }

    /** The awake brightness one step up or down, within what is still of use. */
    static int steppedBrightness(int current, int step) {
        return Math.max(MIN_STEPPED_BRIGHTNESS, Math.min(MAX_BRIGHTNESS, current + step));
    }

    static boolean shouldSleep(
            boolean scheduleAllowsWake,
            boolean motionEnabled,
            boolean motionMonitoring,
            boolean mediaActive,
            long inactiveForMs,
            long motionTimeoutMs) {
        if (!scheduleAllowsWake) {
            return true;
        }
        if (!motionEnabled || !motionMonitoring || mediaActive) {
            return false;
        }
        return inactiveForMs >= motionTimeoutMs;
    }
}
