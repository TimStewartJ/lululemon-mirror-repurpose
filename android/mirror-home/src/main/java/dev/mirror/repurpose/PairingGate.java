package dev.mirror.repurpose;

/**
 * Decides whether a pairing attempt is examined at all. A code is accepted
 * only while one is on display, and wrong guesses lock pairing for a period
 * that doubles with each lockout, so a code cannot be found by guessing over
 * the network. Times are monotonic milliseconds supplied by the caller.
 */
final class PairingGate {
    enum State { OPEN, CLOSED, LOCKED }

    static final int MAX_FAILURES = 5;
    static final long FIRST_LOCKOUT_MS = 30_000L;
    static final long MAX_LOCKOUT_MS = 60 * 60_000L;
    static final long QUIET_RESET_MS = 24 * 60 * 60_000L;
    /* Display surfaces refresh every few seconds; this bridges the gaps. */
    static final long DISPLAY_GRACE_MS = 30_000L;

    private long displayedUntil;
    private int failures;
    private int lockouts;
    private long lockedUntil;
    private long lastFailureAt;
    private boolean everFailed;
    private long wrongCodes;

    /** A surface is showing the code right now. */
    void displayed(long now) {
        displayedThrough(now + DISPLAY_GRACE_MS);
    }

    /** The code was handed out for use until {@code until}. */
    void displayedThrough(long until) {
        displayedUntil = Math.max(displayedUntil, until);
    }

    /** Nothing shows a code any more, for example because it was just used. */
    void close() {
        displayedUntil = 0L;
    }

    boolean onDisplay(long now) {
        return now < displayedUntil;
    }

    State state(long now) {
        forgetOldFailures(now);
        if (now < lockedUntil) {
            return State.LOCKED;
        }
        return onDisplay(now) ? State.OPEN : State.CLOSED;
    }

    /** Milliseconds until a lock ends; zero when pairing is not locked. */
    long lockedForMillis(long now) {
        return Math.max(0L, lockedUntil - now);
    }

    void recordWrongCode(long now) {
        forgetOldFailures(now);
        everFailed = true;
        lastFailureAt = now;
        wrongCodes++;
        failures++;
        if (failures >= MAX_FAILURES) {
            failures = 0;
            lockedUntil = now + lockoutMillis(lockouts);
            lockouts++;
        }
    }

    /** A correct code, or an owner opening a window, clears every penalty. */
    void reset() {
        failures = 0;
        lockouts = 0;
        lockedUntil = 0L;
    }

    long wrongCodes() {
        return wrongCodes;
    }

    /** When the last wrong code arrived, or -1 if there has been none. */
    long lastWrongCodeAt() {
        return everFailed ? lastFailureAt : -1L;
    }

    static long lockoutMillis(int previousLockouts) {
        if (previousLockouts >= 16) {
            return MAX_LOCKOUT_MS;
        }
        return Math.min(MAX_LOCKOUT_MS, FIRST_LOCKOUT_MS << previousLockouts);
    }

    private void forgetOldFailures(long now) {
        if (everFailed && now >= lockedUntil && now - lastFailureAt >= QUIET_RESET_MS) {
            failures = 0;
            lockouts = 0;
        }
    }
}
