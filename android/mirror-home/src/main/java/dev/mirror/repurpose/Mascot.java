package dev.mirror.repurpose;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * A character that the Mirror answers as: a small drawing above its words
 * that listens, thinks and answers along with it.
 *
 * <p>All of them are drawn the way the Mirror's icons are, in thin warm
 * white lines on the black that is plain mirror, and none has more to it
 * than can be read from across a room: eyes, an outline, one thing of its
 * own. What they do is the same for all and comes from {@link MascotRig};
 * what differs is the body that acts it out. A mascot holds no state, so one
 * can be swapped for another in the middle of a sentence.
 */
abstract class Mascot {
    /** The setting for answering without a character, as before there were any. */
    static final String NONE = "none";

    /** The Mirror's warm white, and the one colour beside it: a blush for when a mascot is pleased. */
    static final int INK = 0xF5F2EC;
    static final int BLUSH = 0xFF9E8F;
    /** The width of an outline, and of the finer lines of a face. */
    static final float LINE = 0.054f;
    static final float FINE = 0.038f;
    /** How strong the faint light inside an outline is, which makes a body of it. */
    static final float BODY = 0.05f;
    /** Where a mascot stands, below its middle: it squashes and stretches from this point. */
    private static final float FOOT = 0.62f;
    /** The middle of its head, about which it tilts, as a dog does that is trying to follow. */
    private static final float NECK = 0.05f;

    private static final List<Mascot> ALL = Collections.unmodifiableList(Arrays.asList(
            new MascotBlink(), new MascotWisp(), new MascotMochi(), new MascotLune()));

    final String id;
    final String name;
    private final float[] curve = new float[18];
    /** How much of the mascot is there, 0 to 1; everything is drawn this strong at most. */
    private float presence;

    Mascot(String id, String name) {
        this.id = id;
        this.name = name;
    }

    static List<Mascot> all() {
        return ALL;
    }

    /** The mascot of this name, or null for none, an unknown one, or nothing at all. */
    static Mascot byId(String id) {
        for (Mascot mascot : ALL) {
            if (mascot.id.equals(id)) {
                return mascot;
            }
        }
        return null;
    }

    /** Whether a setting names a mascot or says that there is to be none. */
    static boolean known(String id) {
        return NONE.equals(id) || byId(id) != null;
    }

    /** Draws the mascot in a pose. Nothing is drawn of one that is away. */
    final void render(MascotPen pen, MascotRig.Pose pose, float time) {
        if (pose.lift <= 0.003f) {
            return;
        }
        presence = Math.min(1f, pose.lift * 1.6f);
        float size = 0.55f + 0.45f * pose.lift;
        pen.save();
        pen.translate(
                0f,
                (1f - Math.min(1f, pose.lift)) * 0.3f - pose.hop
                        + floats() * (float) Math.sin(time * 1.7f) + pose.nod * 0.16f);
        pen.translate(0f, FOOT);
        pen.scale(size * (1f - pose.stretch * 0.7f), size * (1f + pose.stretch));
        pen.translate(0f, NECK - FOOT);
        pen.rotate(pose.tilt);
        pen.translate(0f, -NECK);
        draw(pen, pose, time);
        pen.restore();
    }

    /** Draws the mascot where it stands, in its box. */
    abstract void draw(MascotPen pen, MascotRig.Pose pose, float time);

    /** How far it drifts up and down while it is there, in box units. */
    float floats() {
        return 0.02f;
    }

    final void ink(MascotPen pen, float alpha) {
        pen.ink(INK, clamp(alpha, 0f, 1f) * presence);
    }

    final void blush(MascotPen pen, float alpha) {
        pen.ink(BLUSH, clamp(alpha, 0f, 1f) * presence);
    }

    /**
     * One eye. Open it is a filled oval; shut it is a line, which a smile
     * bends into an arch and sleep into a bowl. An eye goes from one to the
     * other without a jump, so that a blink and a smile can be sprung.
     *
     * @param half its half width
     * @param tall its half height when open as usual
     */
    final void eye(
            MascotPen pen, float x, float y, float half, float tall,
            float open, float smile, float sleep, float width) {
        float lid = clamp(open, 0f, 1.6f) * (1f - clamp(smile * 1.7f, 0f, 1f));
        float height = tall * lid;
        if (height > width * 0.5f) {
            pen.disc(x, y, half, height);
            return;
        }
        float bend = smile > 0.25f
                ? clamp((smile - 0.25f) / 0.75f, 0f, 1.2f)
                : -clamp(sleep, 0f, 1f) * 0.8f;
        float reach = half * 1.25f;
        for (int point = 0; point < 9; point++) {
            float along = -1f + point / 4f;
            curve[2 * point] = x + along * reach;
            curve[2 * point + 1] = y - bend * half * (0.95f * (1f - along * along) - 0.35f);
        }
        pen.shape(curve, 9, false, width);
    }

    /** Small letters z that rise and fade, for a mascot that sleeps. */
    final void sleeping(MascotPen pen, float x, float y, float amount, float time) {
        if (amount < 0.02f) {
            return;
        }
        for (int letter = 0; letter < 3; letter++) {
            float along = fraction(time / 2.7f + letter / 3f);
            float size = 0.035f + 0.045f * along;
            float cx = x + 0.2f * along + 0.03f * (float) Math.sin(along * 6f + letter);
            float cy = y - 0.36f * along;
            ink(pen, amount * (float) Math.sin(Math.PI * along) * 0.9f);
            curve[0] = cx - size;
            curve[1] = cy - size;
            curve[2] = cx + size;
            curve[3] = cy - size;
            curve[4] = cx - size;
            curve[5] = cy + size;
            curve[6] = cx + size;
            curve[7] = cy + size;
            pen.shape(curve, 4, false, 0.032f);
        }
    }

    /** A question mark beside a mascot that did not follow. */
    final void puzzled(MascotPen pen, float x, float y, float amount, float time) {
        if (amount < 0.02f) {
            return;
        }
        float radius = 0.085f;
        pen.save();
        pen.translate(x, y + 0.02f * (float) Math.sin(time * 2.6f));
        pen.rotate(10f + 5f * (float) Math.sin(time * 2.1f));
        pen.scale(0.6f + 0.4f * amount, 0.6f + 0.4f * amount);
        ink(pen, amount);
        pen.arc(0f, 0f, radius, radius, 190f, 240f, 0.045f);
        pen.line(
                radius * (float) Math.cos(Math.toRadians(70)), radius * (float) Math.sin(Math.toRadians(70)),
                0f, 0.16f, 0.045f);
        pen.disc(0f, 0.27f, 0.03f, 0.03f);
        pen.restore();
    }

    /**
     * Three dots that rise from a mascot one after the other while it
     * thinks: the dots that the Mirror shows when it has no mascot.
     */
    final void pondering(MascotPen pen, float x, float y, float amount, float time) {
        if (amount < 0.02f) {
            return;
        }
        float beat = fraction(time / 1.25f);
        for (int dot = 0; dot < 3; dot++) {
            float swell = (float) Math.pow(0.5 - 0.5 * Math.cos(2 * Math.PI * (beat - dot * 0.16f)), 1.6);
            float radius = (0.02f + 0.011f * dot) * (0.85f + 0.3f * swell);
            ink(pen, amount * (0.3f + 0.7f * swell));
            pen.disc(x + dot * 0.1f, y - dot * 0.085f - 0.02f * swell, radius, radius);
        }
    }

    /** Brows drawn together over the eyes of a mascot that is sorry. */
    final void worried(MascotPen pen, float leftX, float rightX, float y, float amount) {
        if (amount < 0.02f) {
            return;
        }
        ink(pen, amount);
        pen.line(leftX - 0.065f, y + 0.028f, leftX + 0.06f, y - 0.028f, FINE);
        pen.line(rightX + 0.065f, y + 0.028f, rightX - 0.06f, y - 0.028f, FINE);
    }

    /** Arcs that widen and fade beside a mascot, as sound reaches it. */
    final void hearing(MascotPen pen, float x, float y, boolean toTheRight, float amount, float time) {
        if (amount < 0.02f) {
            return;
        }
        for (int wave = 0; wave < 2; wave++) {
            float along = fraction(time / 1.3f + wave * 0.5f);
            float radius = 0.08f + 0.2f * along;
            ink(pen, amount * (1f - along) * 0.85f);
            pen.arc(x, y, radius, radius * 1.15f, toTheRight ? -38f : 142f, 76f, FINE);
        }
    }

    static float clamp(float value, float low, float high) {
        return Math.max(low, Math.min(high, value));
    }

    static float mix(float from, float to, float amount) {
        return from + (to - from) * amount;
    }

    static float fraction(float value) {
        return value - (float) Math.floor(value);
    }
}
