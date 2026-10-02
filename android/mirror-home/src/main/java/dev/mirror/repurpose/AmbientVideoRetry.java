package dev.mirror.repurpose;

/**
 * How long the background video waits before it tries again after its player
 * failed.
 *
 * <p>The factory launcher's setup screen plays a video too, and it is in
 * front whenever Mirror Home's process has just ended. On a Mirror, Mirror
 * Home was restarted while that screen held the video decoder; its own player
 * was refused one ("insufficient resources"), and the film stayed off for
 * fourteen minutes, until the display was next woken. Nothing but a change
 * of the display's state used to start the player again.
 */
final class AmbientVideoRetry {
    private static final long[] DELAYS_MS = {5_000L, 15_000L, 60_000L, 5 * 60_000L};

    private int failures;

    /** The wait before the next try: longer after each failure in a row, five minutes at most. */
    long nextDelayMs() {
        long delay = DELAYS_MS[Math.min(failures, DELAYS_MS.length - 1)];
        failures++;
        return delay;
    }

    /** The video is on the glass again. */
    void succeeded() {
        failures = 0;
    }
}
