package dev.mirror.repurpose;

import dev.mirror.repurpose.MascotRig.Mood;

/**
 * Wisp: the small ghost that lives in the mirror. It never quite stands
 * still: it floats, and its hem trails after it.
 */
final class MascotWisp extends Mascot {
    private static final float RADIUS = 0.5f;
    private static final float SHOULDER = -0.14f;
    private static final float HEM = 0.48f;
    private static final int DOME = 22;
    private static final int SIDE = 5;
    private static final int LOBES = 3;
    private static final int LOBE = 10;

    private final float[] body = new float[2 * (DOME + 1 + 2 * SIDE + LOBES * LOBE)];

    MascotWisp() {
        super("wisp", "Wisp");
    }

    @Override
    float floats() {
        return 0.05f;
    }

    @Override
    void draw(MascotPen pen, MascotRig.Pose pose, float time) {
        int count = outline(time, pose.droop);
        ink(pen, BODY);
        pen.shape(body, count, true, 0f);
        ink(pen, 1f);
        pen.shape(body, count, true, LINE);

        float lookX = pose.gazeX * 0.075f;
        float lookY = -0.13f + pose.gazeY * 0.06f;
        float tall = 0.085f * (1f + 0.12f * pose.perk);
        float sleep = pose.droop;
        ink(pen, 1f);
        eye(pen, -0.18f + lookX, lookY, 0.062f, tall, pose.open * (1f + 0.22f * pose.skew), pose.smile, sleep, FINE);
        ink(pen, 1f);
        eye(pen, 0.18f + lookX, lookY, 0.062f, tall, pose.open * (1f - 0.45f * pose.skew), pose.smile, sleep, FINE);
        float sorry = pose.is(Mood.SORRY);
        if (pose.mouth > 0.06f) {
            ink(pen, 1f);
            pen.disc(lookX * 0.6f, lookY + 0.19f, 0.04f + 0.012f * pose.mouth, 0.012f + 0.055f * pose.mouth);
        } else if (sorry > 0.02f) {
            ink(pen, sorry);
            pen.arc(lookX * 0.6f, lookY + 0.24f, 0.06f, 0.05f, 205f, 130f, FINE);
        }
        worried(pen, -0.18f + lookX, 0.18f + lookX, lookY - 0.15f, sorry);

        float glee = Math.max(pose.is(Mood.HAPPY), pose.is(Mood.GREETING));
        if (glee > 0.02f) {
            blush(pen, glee * 0.75f);
            pen.disc(-0.32f + lookX, lookY + 0.11f, 0.07f, 0.04f);
            pen.disc(0.32f + lookX, lookY + 0.11f, 0.07f, 0.04f);
        }
        if (pose.wave > 0.03f) {
            // An arm comes out of its side only to wave.
            double angle = Math.toRadians(mix(40f, -58f, Math.min(1f, pose.wave)) + 16f * pose.swing * pose.wave);
            float length = 0.27f * Math.min(1f, pose.wave * 1.6f);
            ink(pen, 1f);
            pen.line(
                    RADIUS - 0.02f, 0.06f,
                    RADIUS - 0.02f + length * (float) Math.cos(angle), 0.06f + length * (float) Math.sin(angle), LINE);
        }
        hearing(pen, -0.62f, -0.12f, false, pose.is(Mood.LISTENING), time);
        pondering(pen, 0.5f, -0.52f, pose.is(Mood.THINKING), time);
        sleeping(pen, 0.42f, -0.5f, pose.is(Mood.SLEEPY), time);
        puzzled(pen, 0.68f, -0.52f, pose.is(Mood.CONFUSED), time);
    }

    /** The body: a dome, two sides that sway, and a hem of three lobes, each breathing a little by itself. */
    private int outline(float time, float droop) {
        int at = 0;
        for (int step = 0; step <= DOME; step++) {
            double angle = Math.PI + Math.PI * step / DOME;
            body[at++] = RADIUS * (float) Math.cos(angle);
            body[at++] = SHOULDER + RADIUS * (float) Math.sin(angle);
        }
        for (int step = 1; step <= SIDE; step++) {
            float y = mix(SHOULDER, HEM, step / (float) SIDE);
            body[at++] = RADIUS + sway(y, time);
            body[at++] = y;
        }
        for (int lobe = 0; lobe < LOBES; lobe++) {
            // A ghost that droops lets its hem hang.
            float depth = 0.1f * (1f + 0.3f * (float) Math.sin(time * 3.1f + lobe * 2.1f)) + 0.04f * droop;
            for (int step = 1; step <= LOBE; step++) {
                float across = (lobe + step / (float) LOBE) / LOBES;
                float y = HEM + depth * (float) Math.sin(Math.PI * step / LOBE);
                body[at++] = RADIUS - 2f * RADIUS * across + sway(HEM, time);
                body[at++] = y;
            }
        }
        for (int step = 1; step < SIDE; step++) {
            float y = mix(HEM, SHOULDER, step / (float) SIDE);
            body[at++] = -RADIUS + sway(y, time);
            body[at++] = y;
        }
        return at / 2;
    }

    private static float sway(float y, float time) {
        float down = clamp((y - SHOULDER) / (HEM - SHOULDER), 0f, 1f);
        return 0.045f * down * down * (float) Math.sin(time * 1.8f + y * 2.4f);
    }
}
