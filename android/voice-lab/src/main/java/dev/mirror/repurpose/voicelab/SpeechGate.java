package dev.mirror.repurpose.voicelab;

/**
 * Lets sound through to the recogniser only while someone seems to be
 * speaking. Recognition is the expensive part on a 2015 processor; comparing
 * loudness is nearly free. The gate follows the room's noise level, opens
 * when a frame stands clearly above it, and stays open through short pauses.
 */
final class SpeechGate {
    /** How far above the room a frame must be to count as speech. */
    static final double OPEN_RATIO = 3.0;
    static final double MINIMUM_LEVEL = 60.0;
    /** A pause this many frames long ends what was being said. */
    static final int HANG_FRAMES = 25;
    private static final double FLOOR_RISE = 1.02;
    private static final double FLOOR_FALL = 0.80;
    /** A sound that never pauses is the room, not a sentence: about ten seconds. */
    private static final double FLOOR_RISE_WHILE_OPEN = 1.003;

    private double floor = MINIMUM_LEVEL;
    private int quietFrames = HANG_FRAMES;
    private boolean open;

    /**
     * Takes the loudness of the next frame.
     *
     * @return whether the gate is open for this frame
     */
    boolean accept(double level) {
        boolean speech = level > Math.max(MINIMUM_LEVEL, floor * OPEN_RATIO);
        if (speech) {
            quietFrames = 0;
            open = true;
            floor *= FLOOR_RISE_WHILE_OPEN;
        } else {
            quietFrames++;
            // Follow the room slowly upwards and quickly downwards, and only
            // while nobody speaks, so a long sentence does not become the room.
            floor = level > floor ? floor * FLOOR_RISE : floor * FLOOR_FALL + level * (1 - FLOOR_FALL);
            floor = Math.max(1.0, floor);
            if (quietFrames >= HANG_FRAMES) {
                open = false;
            }
        }
        return open;
    }

    boolean isOpen() {
        return open;
    }

    double noiseFloor() {
        return floor;
    }
}
