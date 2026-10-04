package dev.mirror.repurpose;

import dev.mirror.repurpose.MascotRig.Mood;

/**
 * Blink: two eyes and nothing else. The least there can be of a face, and
 * the one that reads from farthest away; what it feels is all in how the
 * eyes stand.
 */
final class MascotBlink extends Mascot {
    MascotBlink() {
        super("blink", "Blink");
    }

    @Override
    void draw(MascotPen pen, MascotRig.Pose pose, float time) {
        float lookX = pose.gazeX * 0.17f;
        float lookY = 0.04f + pose.gazeY * 0.14f;
        float half = 0.13f + 0.025f * pose.perk;
        // With no mouth to move, the eyes keep time with what is said.
        float tall = 0.25f * (1f + 0.22f * (pose.mouth - 0.3f) * pose.is(Mood.SPEAKING));
        float sleep = pose.droop;

        // Sorry, the eyes lean together at the top, as under drawn brows.
        float slant = 20f * pose.is(Mood.SORRY);
        for (int side = -1; side <= 1; side += 2) {
            float open = pose.open * (side < 0 ? 1f + 0.25f * pose.skew : 1f - 0.5f * pose.skew);
            pen.save();
            pen.translate(side * 0.3f + lookX, lookY);
            pen.rotate(-side * slant);
            ink(pen, 1f);
            eye(pen, 0f, 0f, half, tall, open, pose.smile, sleep, 0.075f);
            pen.restore();
        }

        float glee = Math.max(pose.is(Mood.HAPPY), pose.is(Mood.GREETING));
        if (glee > 0.02f) {
            blush(pen, glee * 0.75f);
            pen.disc(-0.53f + lookX, lookY + 0.27f, 0.085f, 0.05f);
            pen.disc(0.53f + lookX, lookY + 0.27f, 0.085f, 0.05f);
        }
        float listening = pose.is(Mood.LISTENING);
        hearing(pen, 0.6f, lookY, true, listening, time);
        hearing(pen, -0.6f, lookY, false, listening, time);
        pondering(pen, 0.56f, -0.28f, pose.is(Mood.THINKING), time);
        sleeping(pen, 0.5f, -0.2f, pose.is(Mood.SLEEPY), time);
        puzzled(pen, 0.66f, -0.38f, pose.is(Mood.CONFUSED), time);
    }
}
