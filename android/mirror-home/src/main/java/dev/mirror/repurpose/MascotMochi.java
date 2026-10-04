package dev.mirror.repurpose;

import dev.mirror.repurpose.MascotRig.Mood;

/**
 * Mochi: a cat's head. Its ears say most of it: up when it listens,
 * flicking while it thinks, flat when it is sorry or asleep.
 */
final class MascotMochi extends Mascot {
    private static final float CENTER = 0.12f;
    private static final float WIDE = 0.56f;
    private static final float HIGH = 0.44f;
    /** Where on the head's outline an ear stands, in degrees: from, the middle, to. */
    private static final float[] RIGHT_EAR = {291f, 314f, 337f};
    private static final float[] LEFT_EAR = {203f, 226f, 249f};
    private static final int STEP = 6;

    private final float[] head = new float[2 * (360 / STEP + 8)];
    private final float[] tip = new float[2];

    MascotMochi() {
        super("mochi", "Mochi");
    }

    @Override
    void draw(MascotPen pen, MascotRig.Pose pose, float time) {
        float alert = Math.max(pose.is(Mood.LISTENING), pose.is(Mood.THINKING));
        // An ear flicks now and then while it attends: first one, then the other.
        float beat = fraction(time / 2.2f);
        float flickRight = alert * (beat < 0.1f ? (float) Math.sin(Math.PI * beat / 0.1f) : 0f);
        float flickLeft = alert * (beat > 0.5f && beat < 0.6f ? (float) Math.sin(Math.PI * (beat - 0.5f) / 0.1f) : 0f);

        int count = outline(pose, flickLeft, flickRight);
        ink(pen, BODY);
        pen.shape(head, count, true, 0f);
        ink(pen, 1f);
        pen.shape(head, count, true, LINE);

        float lookX = pose.gazeX * 0.06f;
        float lookY = 0.07f + pose.gazeY * 0.05f;
        float tall = 0.082f * (1f + 0.12f * pose.perk);
        float sleep = pose.droop;
        ink(pen, 1f);
        eye(pen, -0.2f + lookX, lookY, 0.058f, tall, pose.open * (1f + 0.22f * pose.skew), pose.smile, sleep, FINE);
        ink(pen, 1f);
        eye(pen, 0.2f + lookX, lookY, 0.058f, tall, pose.open * (1f - 0.45f * pose.skew), pose.smile, sleep, FINE);

        // The mouth of a cat: two small bowls that meet under the nose.
        float mouthX = lookX * 0.5f;
        float mouthY = lookY + 0.15f;
        float bowl = 0.05f;
        ink(pen, 1f);
        pen.arc(mouthX - bowl, mouthY, bowl, bowl * 0.9f, 0f, 180f, FINE);
        pen.arc(mouthX + bowl, mouthY, bowl, bowl * 0.9f, 0f, 180f, FINE);
        if (pose.mouth > 0.1f) {
            pen.disc(mouthX, mouthY + 0.07f, 0.032f, 0.008f + 0.045f * pose.mouth);
        }

        float glee = Math.max(pose.is(Mood.HAPPY), pose.is(Mood.GREETING));
        float raise = 0.06f * glee - 0.07f * pose.droop;
        for (int side = -1; side <= 1; side += 2) {
            ink(pen, 0.6f);
            pen.line(side * 0.37f, 0.2f, side * 0.7f, 0.13f - raise, 0.032f);
            pen.line(side * 0.37f, 0.27f, side * 0.7f, 0.29f - raise * 0.6f, 0.032f);
        }
        if (glee > 0.02f) {
            blush(pen, glee * 0.75f);
            pen.disc(-0.33f + lookX, lookY + 0.11f, 0.065f, 0.038f);
            pen.disc(0.33f + lookX, lookY + 0.11f, 0.065f, 0.038f);
        }
        worried(pen, -0.2f + lookX, 0.2f + lookX, lookY - 0.15f, pose.is(Mood.SORRY));
        pondering(pen, 0.34f, -0.54f, pose.is(Mood.THINKING), time);
        sleeping(pen, 0.48f, -0.42f, pose.is(Mood.SLEEPY), time);
        puzzled(pen, 0.7f, -0.5f, pose.is(Mood.CONFUSED), time);
    }

    /** The head, once around from three o'clock, with an ear where the outline reaches each. */
    private int outline(MascotRig.Pose pose, float flickLeft, float flickRight) {
        float height = 0.2f + 0.17f * pose.perk - 0.04f * pose.droop;
        // A greeting waves with an ear, there being no hand.
        float wave = pose.wave * (0.5f + 0.5f * pose.swing) * 26f;
        int at = 0;
        for (int degrees = 0; degrees < 360; degrees += STEP) {
            float[] ear = degrees > LEFT_EAR[0] && degrees < LEFT_EAR[2] ? LEFT_EAR
                    : degrees > RIGHT_EAR[0] && degrees < RIGHT_EAR[2] ? RIGHT_EAR : null;
            if (ear == null) {
                at = rim(degrees, at);
                continue;
            }
            if (degrees - STEP > ear[0]) {
                continue;
            }
            boolean right = ear == RIGHT_EAR;
            float outward = right ? 1f : -1f;
            float lean = outward * (32f * pose.droop + (right ? wave : 0f))
                    - outward * 20f * (right ? flickRight : flickLeft);
            at = rim(ear[0], at);
            ear(ear[1], height, lean);
            head[at++] = tip[0];
            head[at++] = tip[1];
            at = rim(ear[2], at);
        }
        return at / 2;
    }

    private int rim(float degrees, int at) {
        double angle = Math.toRadians(degrees);
        head[at] = WIDE * (float) Math.cos(angle);
        head[at + 1] = CENTER + HIGH * (float) Math.sin(angle);
        return at + 2;
    }

    /** The tip of an ear: out from the head where the ear stands, turned by how the ear leans. */
    private void ear(float degrees, float height, float lean) {
        double angle = Math.toRadians(degrees);
        double outX = Math.cos(angle) / WIDE;
        double outY = Math.sin(angle) / HIGH;
        double length = Math.hypot(outX, outY);
        double turn = Math.toRadians(lean);
        double x = outX / length;
        double y = outY / length;
        tip[0] = WIDE * (float) Math.cos(angle) + height * (float) (x * Math.cos(turn) - y * Math.sin(turn));
        tip[1] = CENTER + HIGH * (float) Math.sin(angle) + height * (float) (x * Math.sin(turn) + y * Math.cos(turn));
    }
}
