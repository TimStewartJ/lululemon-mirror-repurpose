package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import dev.mirror.repurpose.MascotRig.Mood;

import org.junit.Test;

public class MascotRigTest {
    private static final float FRAME = 1f / 60f;

    private static void run(MascotRig rig, float seconds) {
        for (int frame = Math.round(seconds / FRAME); frame > 0; frame--) {
            rig.step(FRAME);
        }
    }

    @Test
    public void itComesUpAtOnceWithALittleBounceAndSettles() {
        MascotRig rig = new MascotRig();
        rig.show(Mood.LISTENING);
        rig.step(FRAME);
        assertTrue("nothing of it after one frame", rig.pose().lift > 0f);
        float highest = 0f;
        for (int frame = 0; frame < 15; frame++) {
            rig.step(FRAME);
        }
        assertTrue("a quarter of a second in, it is only " + rig.pose().lift + " up", rig.pose().lift > 0.9f);
        for (int frame = 0; frame < 90; frame++) {
            rig.step(FRAME);
            highest = Math.max(highest, rig.pose().lift);
        }
        assertTrue("it came up without any bounce: " + highest, highest > 1.01f);
        assertTrue("it shot out of its box: " + highest, highest < 1.12f);
        assertEquals(1f, rig.pose().lift, 0.01f);
        // It arrives in its part: all ears from the first frame.
        assertTrue(rig.pose().open > 1.1f);
    }

    @Test
    public void aNewMoodTakesHoldInTheSameFrame() {
        MascotRig rig = new MascotRig();
        rig.show(Mood.LISTENING);
        run(rig, 1.5f);
        float gaze = rig.pose().gazeY;
        float perk = rig.pose().perk;
        rig.show(Mood.THINKING);
        rig.step(FRAME);
        assertTrue("its eyes had not moved a frame later", rig.pose().gazeY < gaze);
        assertTrue(rig.pose().perk < perk);
        run(rig, 0.5f);
        assertTrue(rig.pose().is(Mood.THINKING) > 0.95f);
        assertTrue(rig.pose().is(Mood.LISTENING) < 0.05f);
        assertTrue("thinking, it does not look up: " + rig.pose().gazeY, rig.pose().gazeY < -0.4f);
    }

    @Test
    public void itSaysItsAnswerAndThenIsWhatFollows() {
        MascotRig rig = new MascotRig();
        rig.show(Mood.THINKING);
        run(rig, 1f);
        rig.speak(1f, Mood.CURIOUS);
        float widest = 0f;
        float narrowest = 1f;
        for (int frame = 0; frame < 54; frame++) {
            rig.step(FRAME);
            if (frame > 10) {
                widest = Math.max(widest, rig.pose().mouth);
                narrowest = Math.min(narrowest, rig.pose().mouth);
            }
        }
        assertEquals(Mood.SPEAKING, rig.mood());
        assertTrue("its mouth does not move: " + narrowest + " to " + widest, widest - narrowest > 0.3f);
        run(rig, 0.2f);
        assertEquals(Mood.CURIOUS, rig.mood());
        run(rig, 1f);
        assertEquals(0f, rig.pose().mouth, 0.02f);
        assertTrue(rig.pose().tilt > 8f);
    }

    @Test
    public void beingPleasedPassesByItself() {
        for (Mood passing : new Mood[]{Mood.HAPPY, Mood.GREETING}) {
            MascotRig rig = new MascotRig();
            rig.show(Mood.IDLE);
            run(rig, 1f);
            rig.show(passing);
            float highest = 0f;
            for (int frame = 0; frame < 40; frame++) {
                rig.step(FRAME);
                highest = Math.max(highest, rig.pose().hop);
            }
            assertEquals(passing, rig.mood());
            assertTrue(passing + " without a hop: " + highest, highest > 0.05f && highest < 0.25f);
            assertTrue(rig.pose().smile > 0.9f);
            run(rig, 2.2f);
            assertEquals(Mood.IDLE, rig.mood());
            run(rig, 0.6f);
            assertEquals(0f, rig.pose().smile, 0.03f);
            assertEquals(0f, rig.pose().hop, 0.01f);
        }
    }

    @Test
    public void onlyAGreetingWaves() {
        MascotRig rig = new MascotRig();
        rig.show(Mood.GREETING);
        run(rig, 0.8f);
        assertTrue(rig.pose().wave > 0.9f);
        rig.show(Mood.HAPPY);
        run(rig, 0.8f);
        assertEquals(0f, rig.pose().wave, 0.03f);
    }

    @Test
    public void aNodGoesDownAndComesBackAndLeavesTheMoodAlone() {
        MascotRig rig = new MascotRig();
        rig.show(Mood.THINKING);
        run(rig, 1f);
        rig.nod();
        float deepest = 0f;
        for (int frame = 0; frame < 30; frame++) {
            rig.step(FRAME);
            deepest = Math.max(deepest, rig.pose().nod);
        }
        assertTrue("a nod of " + deepest, deepest > 0.25f && deepest < 0.8f);
        assertEquals(Mood.THINKING, rig.mood());
        run(rig, 1f);
        assertEquals(0f, rig.pose().nod, 0.01f);
    }

    @Test
    public void itLeavesWithoutComingBackUpAndThenRests() {
        MascotRig rig = new MascotRig();
        assertTrue("a mascot that was never shown is not at rest", rig.resting());
        rig.show(Mood.HAPPY);
        run(rig, 0.6f);
        assertFalse(rig.resting());
        float smile = rig.pose().smile;
        rig.show(Mood.HIDDEN);
        float before = rig.pose().lift;
        for (int frame = 0; frame < 60; frame++) {
            rig.step(FRAME);
            assertTrue("it came back up while leaving", rig.pose().lift <= before + 1e-4f);
            assertTrue(rig.pose().lift >= 0f);
            before = rig.pose().lift;
        }
        assertTrue("a second later it is still " + before + " there", rig.resting());
        // It leaves with the face it had, and does not go blank on the way out.
        assertEquals(smile, rig.pose().smile, 0.1f);
        // Nor does being pleased run on behind the scenes and bring it back.
        run(rig, 3f);
        assertEquals(Mood.HIDDEN, rig.mood());
        assertTrue(rig.resting());
    }

    @Test
    public void itBlinksNowAndThenAndNotWhileItSleeps() {
        MascotRig rig = new MascotRig();
        rig.show(Mood.IDLE);
        run(rig, 1f);
        int blinks = 0;
        int open = 0;
        boolean shut = false;
        int frames = 20 * 60;
        for (int frame = 0; frame < frames; frame++) {
            rig.step(FRAME);
            boolean now = rig.pose().open < 0.25f;
            blinks += now && !shut ? 1 : 0;
            shut = now;
            open += rig.pose().open > 0.9f ? 1 : 0;
        }
        assertTrue("it blinked " + blinks + " times in twenty seconds", blinks >= 3 && blinks <= 10);
        assertTrue("its eyes are open only " + open + " frames of " + frames, open > frames * 0.9);
        rig.show(Mood.SLEEPY);
        run(rig, 1f);
        for (int frame = 0; frame < 300; frame++) {
            rig.step(FRAME);
            assertTrue(rig.pose().open < 0.1f);
        }
    }

    @Test
    public void aStallIsNotActedOut() {
        MascotRig rig = new MascotRig();
        rig.show(Mood.IDLE);
        rig.step(30f);
        assertTrue("half a minute without a frame moved it " + rig.clock() + " s", rig.clock() <= 0.11f);
        rig.step(-1f);
        rig.step(Float.NaN);
        assertTrue(rig.clock() <= 0.11f);
        assertFalse(Float.isNaN(rig.pose().lift));
    }

    @Test
    public void theSameStepsGiveTheSameMotion() {
        MascotRig one = new MascotRig();
        MascotRig other = new MascotRig();
        for (MascotRig rig : new MascotRig[]{one, other}) {
            rig.show(Mood.LISTENING);
            run(rig, 0.7f);
            rig.speak(0.8f, Mood.IDLE);
            run(rig, 6.3f);
        }
        assertEquals(one.clock(), other.clock(), 0f);
        assertEquals(one.pose().open, other.pose().open, 0f);
        assertEquals(one.pose().gazeX, other.pose().gazeX, 0f);
        assertEquals(one.pose().stretch, other.pose().stretch, 0f);
    }
}
