package dev.mirror.repurpose;

import dev.mirror.repurpose.MascotRig.Mood;

/**
 * Lune: a moon. It is full when it attends, wanes to a sliver to sleep,
 * and while it thinks its phases pass over it.
 */
final class MascotLune extends Mascot {
    private static final float RADIUS = 0.54f;
    private static final int EDGE = 24;

    private final float[] lit = new float[4 * (EDGE + 1)];

    MascotLune() {
        super("lune", "Lune");
    }

    @Override
    void draw(MascotPen pen, MascotRig.Pose pose, float time) {
        float glee = Math.max(pose.is(Mood.HAPPY), pose.is(Mood.GREETING));
        float attending = Math.max(
                Math.max(pose.is(Mood.LISTENING), pose.is(Mood.SPEAKING)), Math.max(glee, pose.is(Mood.CURIOUS)));
        float sleepy = pose.is(Mood.SLEEPY);
        float sorry = pose.is(Mood.SORRY);
        float phase = 0.9f + 0.1f * attending - 0.62f * sleepy;
        phase = mix(phase, 0.56f + 0.44f * (float) Math.sin(time * 3.4f), pose.is(Mood.THINKING));
        phase = clamp(phase, 0.07f, 1f);
        // Sorry, it goes dim, as behind thin cloud.
        float glow = 1f - 0.45f * sorry;

        // The whole disc, faintly, and over it the part that is lit.
        ink(pen, 0.3f);
        pen.ring(0f, 0f, RADIUS, RADIUS, FINE);
        int count = lit(phase);
        ink(pen, 2.2f * BODY * glow);
        pen.shape(lit, count, true, 0f);
        ink(pen, glow);
        pen.shape(lit, count, true, LINE);

        // As a sliver, its face is in the dark part, beside the light.
        float lookX = pose.gazeX * 0.06f - 0.13f * sleepy;
        float lookY = -0.06f + pose.gazeY * 0.05f;
        float tall = 0.082f * (1f + 0.12f * pose.perk);
        float sleep = pose.droop;
        ink(pen, 1f);
        eye(pen, -0.19f + lookX, lookY, 0.058f, tall, pose.open * (1f + 0.22f * pose.skew), pose.smile, sleep, FINE);
        ink(pen, 1f);
        eye(pen, 0.19f + lookX, lookY, 0.058f, tall, pose.open * (1f - 0.45f * pose.skew), pose.smile, sleep, FINE);
        if (pose.mouth > 0.1f) {
            ink(pen, 1f);
            pen.disc(lookX * 0.5f, lookY + 0.2f, 0.038f + 0.012f * pose.mouth, 0.01f + 0.05f * pose.mouth);
        } else {
            // It smiles a little whenever nothing is the matter.
            float content = 1f - sorry - pose.is(Mood.CONFUSED) - 0.6f * pose.is(Mood.THINKING);
            ink(pen, 0.9f * content * (1f - pose.mouth * 10f));
            pen.arc(lookX * 0.5f, lookY + 0.12f, 0.085f, 0.07f, 30f, 120f, FINE);
        }
        if (glee > 0.02f) {
            blush(pen, glee * 0.75f);
            pen.disc(-0.32f + lookX, lookY + 0.12f, 0.065f, 0.038f);
            pen.disc(0.32f + lookX, lookY + 0.12f, 0.065f, 0.038f);
            // Two stars come out.
            star(pen, 0.7f, -0.46f, 0.075f, glee, time);
            star(pen, -0.7f, -0.26f, 0.05f, glee, time + 1.3f);
        }
        float listening = pose.is(Mood.LISTENING);
        if (listening > 0.02f) {
            // A halo that widens, as sound reaches it.
            for (int wave = 0; wave < 2; wave++) {
                float along = fraction(time / 1.5f + wave * 0.5f);
                // It opens out as the moon comes up, and not before.
                float radius = RADIUS + (0.07f + 0.2f * along) * listening;
                ink(pen, listening * (1f - along) * 0.6f);
                pen.ring(0f, 0f, radius, radius, 0.03f);
            }
        }
        worried(pen, -0.19f + lookX, 0.19f + lookX, lookY - 0.15f, sorry);
        if (sorry > 0.02f && pose.mouth <= 0.1f) {
            ink(pen, sorry);
            pen.arc(lookX * 0.5f, lookY + 0.25f, 0.06f, 0.05f, 205f, 130f, FINE);
        }
        sleeping(pen, 0.46f, -0.5f, sleepy, time);
        puzzled(pen, 0.72f, -0.5f, pose.is(Mood.CONFUSED), time);
    }

    /** The lit part: the right limb from top to bottom, and the terminator back up. */
    private int lit(float phase) {
        int at = 0;
        for (int step = 0; step <= EDGE; step++) {
            double angle = -Math.PI / 2 + Math.PI * step / EDGE;
            lit[at++] = RADIUS * (float) Math.cos(angle);
            lit[at++] = RADIUS * (float) Math.sin(angle);
        }
        for (int step = 1; step < EDGE; step++) {
            double angle = Math.PI / 2 - Math.PI * step / EDGE;
            lit[at++] = -(2f * phase - 1f) * RADIUS * (float) Math.cos(angle);
            lit[at++] = RADIUS * (float) Math.sin(angle);
        }
        return at / 2;
    }

    private void star(MascotPen pen, float x, float y, float size, float amount, float time) {
        float arm = size * (0.7f + 0.3f * (float) Math.sin(time * 5f));
        ink(pen, amount * (0.6f + 0.4f * (float) Math.sin(time * 5f)));
        pen.line(x - arm, y, x + arm, y, 0.03f);
        pen.line(x, y - arm, x, y + arm, 0.03f);
    }
}
