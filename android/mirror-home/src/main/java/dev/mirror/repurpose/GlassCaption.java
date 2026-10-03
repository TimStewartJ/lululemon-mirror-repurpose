package dev.mirror.repurpose;

import android.os.Handler;
import android.os.Looper;

/**
 * The way to the line on the glass, for whatever in Mirror Home has
 * something to show there. It stays within the process: a broadcast would
 * wait its turn behind the system's own, which after an installation or a
 * change of network can take seconds, and a caption that comes seconds late
 * is an answer to nothing.
 */
final class GlassCaption {
    /** Shows a line; runs on the main thread. */
    interface Glass {
        void show(String kind, String text, long millis);
    }

    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static volatile Glass glass;

    private GlassCaption() {
    }

    static void attach(Glass shown) {
        glass = shown;
    }

    static void detach(Glass shown) {
        if (glass == shown) {
            glass = null;
        }
    }

    /**
     * Shows a line, or does what its kind says. Any thread. Without a
     * dashboard there is no glass to show it on, and it is dropped.
     *
     * @param millis for how long; 0 for as long as its kind is shown
     */
    static void show(String kind, String text, long millis) {
        MAIN.post(() -> {
            Glass current = glass;
            if (current != null) {
                current.show(kind, text, millis);
            }
        });
    }
}
