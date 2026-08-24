package dev.mirror.repurpose.updater;

final class OtaConstants {
    static final int PORT = 8791;
    static final String HOME_PACKAGE = "dev.mirror.repurpose";
    static final String HOME_ACTIVITY = "dev.mirror.repurpose.MainActivity";
    static final String ACTION_INSTALL_RESULT =
            "dev.mirror.repurpose.updater.INSTALL_RESULT";

    static final String STATE_IDLE = "idle";
    static final String STATE_VALIDATING = "validating";
    static final String STATE_INSTALLING = "installing";
    static final String STATE_HEALTH_CHECK = "health_check";
    static final String STATE_ROLLING_BACK = "rolling_back";
    static final String STATE_SUCCEEDED = "succeeded";
    static final String STATE_ROLLED_BACK = "rolled_back";
    static final String STATE_FAILED = "failed";
    static final String STATE_RECOVERY_REQUIRED = "recovery_required";

    private OtaConstants() {
    }
}
