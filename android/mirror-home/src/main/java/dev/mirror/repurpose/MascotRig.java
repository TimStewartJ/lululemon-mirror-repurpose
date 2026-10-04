package dev.mirror.repurpose;

/**
 * How a mascot moves: what it is doing, and from that, where its eyes look,
 * how far they are open, how it leans and so on, as a pose that any mascot
 * can draw.
 *
 * <p>Each part of the pose is pulled toward where its mood wants it by a
 * spring. That is what makes a mascot feel quick: a new mood takes hold in
 * the same frame, whatever the mascot was in the middle of, and it arrives
 * with the small overshoot of something that has weight. On top of the
 * springs come the things a living face does by itself: it blinks, breathes,
 * looks about while it thinks and moves its mouth while it talks.
 *
 * <p>Time is given to it in steps and it draws on no clock of its own, so
 * that a test, and a film made without a Mirror, see the same motion.
 */
final class MascotRig {
    /** What a mascot is doing. */
    enum Mood {
        /** Away, below the edge of its box. */
        HIDDEN,
        /** There, beside what it said. */
        IDLE,
        /** It heard its name and waits for what follows. */
        LISTENING,
        /** It is working on what it heard. */
        THINKING,
        /** Its answer has just arrived. */
        SPEAKING,
        /** It did what it was told. */
        HAPPY,
        /** It returns a greeting, with a wave. */
        GREETING,
        /** It asked something and waits for the answer. */
        CURIOUS,
        /** It did not follow. */
        CONFUSED,
        /** It could not help. */
        SORRY,
        /** Good night. */
        SLEEPY
    }

    /** A moment of a mascot, for it to draw. */
    static final class Pose {
        /** How far it has come up into its box: 0 away, 1 there, a little more on the way in. */
        float lift;
        /** How high it is off the ground in a hop, in box units. */
        float hop;
        /** How far its head is down in a nod, 0 to about 1. */
        float nod;
        /** How far its eyes are open: 0 shut, 1 as usual, more when all ears. */
        float open;
        /** How much its eyes smile: at 1 they are arcs. */
        float smile;
        /** Where it looks, each from -1 to 1. */
        float gazeX;
        float gazeY;
        /** How it leans, in degrees. */
        float tilt;
        /** How it is stretched upward (above 0) or squashed (below). */
        float stretch;
        /** How far its mouth is open, 0 to 1. */
        float mouth;
        /** How alert it holds itself, 0 to 1: ears up, eyes large. */
        float perk;
        /** How much it droops, 0 to 1: ears down, eyes low. */
        float droop;
        /** How unlike its two eyes are, 0 to 1: one wide, one narrowed. */
        float skew;
        /** How far its arm is up to wave, 0 to 1, and where in its swing the arm is, -1 to 1. */
        float wave;
        float swing;
        private final float[] moods = new float[Mood.values().length];

        /** How much of a mood is in this moment, 0 to 1; moods fade into each other. */
        float is(Mood mood) {
            return moods[mood.ordinal()];
        }
    }

    private static final int LIFT = 0;
    private static final int OPEN = 1;
    private static final int SMILE = 2;
    private static final int GAZE_X = 3;
    private static final int GAZE_Y = 4;
    private static final int TILT = 5;
    private static final int STRETCH = 6;
    private static final int MOUTH = 7;
    private static final int PERK = 8;
    private static final int DROOP = 9;
    private static final int SKEW = 10;
    private static final int WAVE = 11;
    private static final int HOP = 12;
    private static final int NOD = 13;
    private static final int CHANNELS = 14;

    /** How hard each spring pulls, and how much it is held back. Less holding back is more bounce. */
    private static final float[] STIFFNESS = {210, 380, 300, 160, 160, 170, 260, 620, 320, 90, 200, 200, 240, 260};
    private static final float[] DAMPING = {19, 34, 30, 22, 22, 15, 14, 40, 17, 17, 24, 26, 13, 16};

    /**
     * Where each mood wants the pose, in the order OPEN, SMILE, GAZE_X,
     * GAZE_Y, TILT, STRETCH, MOUTH, PERK, DROOP, SKEW, WAVE.
     */
    private static final float[][] ACTING = {
            /* HIDDEN    */ {1.00f, 0f, 0.00f, 0.00f, 0f, 0.00f, 0.00f, 0.35f, 0.00f, 0.00f, 0f},
            /* IDLE      */ {1.00f, 0f, 0.00f, 0.00f, 0f, 0.00f, 0.00f, 0.35f, 0.00f, 0.00f, 0f},
            /* LISTENING */ {1.25f, 0f, 0.00f, -0.10f, -5f, 0.05f, 0.00f, 1.00f, 0.00f, 0.00f, 0f},
            /* THINKING  */ {0.92f, 0f, 0.00f, -0.62f, 4f, 0.00f, 0.00f, 0.55f, 0.00f, 0.40f, 0f},
            /* SPEAKING  */ {1.00f, 0f, 0.00f, 0.00f, 0f, 0.02f, 0.50f, 0.60f, 0.00f, 0.00f, 0f},
            /* HAPPY     */ {1.00f, 1f, 0.00f, 0.00f, 0f, 0.06f, 0.35f, 0.85f, 0.00f, 0.00f, 0f},
            /* GREETING  */ {1.00f, 1f, 0.00f, 0.00f, -7f, 0.06f, 0.35f, 0.90f, 0.00f, 0.00f, 1f},
            /* CURIOUS   */ {1.20f, 0f, 0.10f, -0.10f, 11f, 0.03f, 0.00f, 0.95f, 0.00f, 0.30f, 0f},
            /* CONFUSED  */ {1.00f, 0f, -0.25f, 0.05f, -12f, -0.03f, 0.00f, 0.25f, 0.25f, 0.85f, 0f},
            /* SORRY     */ {0.80f, 0f, 0.00f, 0.45f, 0f, -0.07f, 0.00f, 0.00f, 0.85f, 0.00f, 0f},
            /* SLEEPY    */ {0.00f, 0f, 0.00f, 0.20f, 5f, -0.08f, 0.00f, 0.00f, 1.00f, 0.00f, 0f},
    };

    private static final float STEP = 1f / 120f;
    /** A frame that took longer than this was a stall, and a stall is not acted out. */
    private static final float LONGEST_STEP = 0.1f;
    private static final float BLINK_SECONDS = 0.15f;

    private final float[] value = new float[CHANNELS];
    private final float[] speed = new float[CHANNELS];
    private final float[] target = new float[CHANNELS];
    private final Pose pose = new Pose();
    private Mood mood = Mood.HIDDEN;
    private Mood next = Mood.IDLE;
    /** How long the mood lasts before the next takes over; 0 for as long as nobody says otherwise. */
    private float lasts;
    private float inMood;
    private float clock;
    private float blinkAt = -1f;
    private float nextBlink = 1.6f;
    private int seed = 20261003;

    MascotRig() {
        aim(Mood.IDLE);
        System.arraycopy(target, 0, value, 0, CHANNELS);
        value[LIFT] = 0f;
        target[LIFT] = 0f;
    }

    Mood mood() {
        return mood;
    }

    Pose pose() {
        return pose;
    }

    /** Seconds of motion so far. */
    float clock() {
        return clock;
    }

    /** Whether the mascot is away and still, so that nothing needs drawing. */
    boolean resting() {
        return mood == Mood.HIDDEN && value[LIFT] < 0.004f && Math.abs(speed[LIFT]) < 0.02f;
    }

    /**
     * Puts the mascot in a mood. Being happy and greeting pass by
     * themselves into being there; the rest last until the next mood.
     */
    void show(Mood wanted) {
        switch (wanted) {
            case HAPPY:
                enter(wanted, 1.5f, Mood.IDLE);
                break;
            case GREETING:
                enter(wanted, 2.4f, Mood.IDLE);
                break;
            case SPEAKING:
                enter(wanted, 1.4f, Mood.IDLE);
                break;
            default:
                enter(wanted, 0f, Mood.IDLE);
                break;
        }
    }

    /** Has the mascot say its answer for a while, and then be in another mood. */
    void speak(float seconds, Mood then) {
        enter(Mood.SPEAKING, seconds, then);
    }

    /** A nod: it understood. The mood stays what it was. */
    void nod() {
        speed[NOD] += 12f;
    }

    private void enter(Mood wanted, float seconds, Mood then) {
        boolean wasAway = mood == Mood.HIDDEN && value[LIFT] < 0.05f;
        boolean changes = wanted != mood;
        mood = wanted;
        lasts = seconds;
        next = then;
        inMood = 0f;
        aim(wanted);
        if (wasAway && wanted != Mood.HIDDEN) {
            // It comes up already in its part, squashed a little, and springs to its height.
            for (int channel = OPEN; channel <= WAVE; channel++) {
                value[channel] = target[channel];
                speed[channel] = 0f;
            }
            value[STRETCH] = target[STRETCH] - 0.14f;
            value[HOP] = 0f;
            value[NOD] = 0f;
            speed[HOP] = 0f;
            speed[NOD] = 0f;
            nextBlink = clock + 1.4f;
            return;
        }
        if (!changes && seconds == 0f) {
            return;
        }
        switch (wanted) {
            case HAPPY:
                speed[HOP] += 2.6f;
                break;
            case GREETING:
                speed[HOP] += 2.0f;
                break;
            case SPEAKING:
                speed[HOP] += 0.9f;
                break;
            case LISTENING:
                speed[STRETCH] += 1.5f;
                break;
            case CONFUSED:
                speed[TILT] -= 150f;
                break;
            case CURIOUS:
                speed[TILT] += 90f;
                break;
            default:
                break;
        }
    }

    private void aim(Mood wanted) {
        float[] part = ACTING[wanted.ordinal()];
        if (wanted != Mood.HIDDEN) {
            // Leaving, it keeps the face it had.
            System.arraycopy(part, 0, target, OPEN, part.length);
        }
        target[LIFT] = wanted == Mood.HIDDEN ? 0f : 1f;
        target[HOP] = 0f;
        target[NOD] = 0f;
    }

    /** Moves time on. Call once for each frame, with the seconds since the one before. */
    void step(float seconds) {
        float left = Math.max(0f, Math.min(seconds, LONGEST_STEP));
        while (left > 1e-5f) {
            float dt = Math.min(left, STEP);
            advance(dt);
            left -= dt;
        }
        compose();
    }

    private void advance(float dt) {
        clock += dt;
        inMood += dt;
        if (lasts > 0f && inMood >= lasts) {
            show(next);
        }
        if (mood == Mood.SPEAKING) {
            // Syllables, in phrases: quick openings under a slower swell.
            target[MOUTH] = 0.18f + 0.82f * Math.abs(sin(clock * 9.5f)) * (0.6f + 0.4f * sin(clock * 3.3f + 0.7f));
        }
        if (clock >= nextBlink) {
            blinkAt = clock;
            // Now and then twice in a row, as eyes do.
            nextBlink = clock + (random() < 0.18f ? 0.3f : 2.2f + 3.4f * random());
        }
        for (int channel = 0; channel < CHANNELS; channel++) {
            // Coming up may bounce; going away may not, or it would come back up for a moment.
            float damping = channel == LIFT && target[LIFT] == 0f ? 30f : DAMPING[channel];
            float pull = -STIFFNESS[channel] * (value[channel] - target[channel]) - damping * speed[channel];
            speed[channel] += pull * dt;
            value[channel] += speed[channel] * dt;
        }
        float ease = 1f - (float) Math.exp(-dt * 9f);
        for (Mood each : Mood.values()) {
            float wanted = each == mood ? 1f : 0f;
            pose.moods[each.ordinal()] += (wanted - pose.moods[each.ordinal()]) * ease;
        }
    }

    private void compose() {
        float thinking = pose.is(Mood.THINKING);
        float calm = pose.is(Mood.IDLE);
        float lid = 1f;
        float sinceBlink = clock - blinkAt;
        if (blinkAt >= 0f && sinceBlink < BLINK_SECONDS) {
            lid = 1f - sin((float) Math.PI * sinceBlink / BLINK_SECONDS);
        }
        pose.lift = Math.max(0f, value[LIFT]);
        pose.hop = Math.max(0f, value[HOP]);
        pose.nod = value[NOD];
        pose.open = Math.max(0f, value[OPEN]) * lid;
        pose.smile = value[SMILE];
        // Thinking, the eyes wander along the ceiling; at rest they drift a little, as if reading along.
        pose.gazeX = value[GAZE_X] + thinking * 0.6f * sin(clock * 1.9f) + calm * 0.1f * sin(clock * 0.7f);
        pose.gazeY = value[GAZE_Y] + thinking * 0.08f * sin(clock * 3.1f) + value[NOD] * 0.9f;
        pose.tilt = value[TILT] + thinking * 2.5f * sin(clock * 1.9f);
        pose.stretch = value[STRETCH] + 0.012f * sin(clock * 2.1f);
        pose.mouth = Math.max(0f, Math.min(1f, value[MOUTH]));
        pose.perk = value[PERK];
        pose.droop = Math.max(0f, value[DROOP]);
        pose.skew = value[SKEW];
        pose.wave = Math.max(0f, Math.min(1.1f, value[WAVE]));
        pose.swing = sin(clock * 11f);
    }

    private float random() {
        seed = seed * 1103515245 + 12345;
        return ((seed >>> 16) & 0x7fff) / 32768f;
    }

    private static float sin(float angle) {
        return (float) Math.sin(angle);
    }
}
