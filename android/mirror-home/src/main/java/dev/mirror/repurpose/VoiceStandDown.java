package dev.mirror.repurpose;

import java.util.HashSet;
import java.util.Set;

/**
 * When voice steps aside for an installation.
 *
 * <p>Android 6 compiles an app while it installs it, which takes memory that a
 * Mirror with the recogniser running does not have to spare. An update of
 * Mirror Home with voice listening left the Mirror so short that Android
 * stopped the factory launcher and the updater, and restarted Mirror Home
 * four times, before the update went in. So the recogniser stops while an
 * installation is under way and starts again when it has ended.
 */
final class VoiceStandDown {
    /** An installation whose end is never reported is waited for this long. */
    static final long LIMIT_MS = 3 * 60_000L;

    private final Set<Integer> installations = new HashSet<>();
    private long untilElapsed;

    /** An installation has begun, or is seen to be at work. */
    synchronized void began(int installation, long nowElapsed) {
        forgetIfOverdue(nowElapsed);
        installations.add(installation);
        untilElapsed = nowElapsed + LIMIT_MS;
    }

    /** An installation has ended, whichever way. */
    synchronized void ended(int installation) {
        installations.remove(installation);
    }

    /** Whether voice should stay stopped. */
    synchronized boolean active(long nowElapsed) {
        forgetIfOverdue(nowElapsed);
        return !installations.isEmpty();
    }

    private void forgetIfOverdue(long nowElapsed) {
        if (nowElapsed >= untilElapsed) {
            installations.clear();
        }
    }
}
