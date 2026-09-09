package dev.mirror.repurpose;

final class AmbientVideoPolicy {
    static final class State {
        final boolean enabled;
        final boolean playing;

        State(boolean enabled, boolean playing) {
            this.enabled = enabled;
            this.playing = playing;
        }
    }

    private AmbientVideoPolicy() {
    }

    static State desiredState(
            boolean builtInDashboard,
            boolean presentationActive,
            boolean activityResumed,
            boolean sleeping) {
        boolean enabled = builtInDashboard && !presentationActive && activityResumed;
        return new State(enabled, enabled && !sleeping);
    }
}
