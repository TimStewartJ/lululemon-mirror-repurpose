package dev.mirror.repurpose;

final class DisplayAutomationPolicy {
    private DisplayAutomationPolicy() {
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
