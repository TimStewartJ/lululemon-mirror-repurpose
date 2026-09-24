package dev.mirror.repurpose;

/**
 * Sleep and wake cross-fade the panel between the dashboard and a dark mirror.
 *
 * <p>A single visibility value drives both a black overlay and the backlight.
 * The overlay scales gamma-encoded pixels, so content luminance falls roughly
 * with visibility^2.2; the backlight scales it linearly. Their product keeps
 * perceived lightness close to linear in visibility, and the caller eases
 * visibility over time.
 */
final class DisplayFadePolicy {
    static final long SLEEP_FADE_MS = 3_000L;
    // Android 6 ramps backlight changes at 200 levels/s. With an ease-in-out
    // curve peaking at pi/2 times the mean slope, a full-scale wake needs about
    // two seconds to stay within that ramp.
    static final long WAKE_FADE_MS = 2_000L;
    static final long SLEEP_COMMIT_FALLBACK_MARGIN_MS = 2_000L;
    static final long BACKLIGHT_UPDATE_INTERVAL_MS = 32L;
    static final int MAX_BACKLIGHT = 255;

    private DisplayFadePolicy() {
    }

    static float clampVisibility(float visibility) {
        if (Float.isNaN(visibility)) {
            return 0f;
        }
        return Math.max(0f, Math.min(1f, visibility));
    }

    static float overlayAlpha(float visibility) {
        return 1f - clampVisibility(visibility);
    }

    static int backlightLevel(int awakeLevel, float visibility) {
        int level = Math.max(0, Math.min(MAX_BACKLIGHT, awakeLevel));
        return Math.round(level * clampVisibility(visibility));
    }

    /**
     * Window brightness override for a backlight level. Android truncates
     * {@code value * 255}, so non-zero levels are centered to survive that.
     */
    static float windowBrightness(int level) {
        if (level <= 0) {
            return 0f;
        }
        return Math.min(1f, (Math.min(MAX_BACKLIGHT, level) + 0.5f) / MAX_BACKLIGHT);
    }

    /** Duration for the remaining distance, so reversals keep a steady pace. */
    static long duration(float from, float to) {
        float distance = Math.abs(clampVisibility(to) - clampVisibility(from));
        long full = to > from ? WAKE_FADE_MS : SLEEP_FADE_MS;
        return Math.round(full * distance);
    }

    /**
     * Fallback delay before sleep side effects when no activity reports that
     * the fade finished; the normal commit happens at the end of the fade.
     */
    static long sleepCommitFallbackMs() {
        return SLEEP_FADE_MS + SLEEP_COMMIT_FALLBACK_MARGIN_MS;
    }
}
