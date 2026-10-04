package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

import dev.mirror.repurpose.MascotRig.Mood;

import org.junit.Test;

import java.util.HashSet;
import java.util.Set;

public class MascotTest {
    private static final float FRAME = 1f / 30f;

    @Test
    public void everyMascotIsKnownByAnIdAndHasAName() {
        Set<String> ids = new HashSet<>();
        for (Mascot mascot : Mascot.all()) {
            assertTrue(mascot.id, mascot.id.matches("[a-z]{3,12}"));
            assertTrue(mascot.name, mascot.name.matches("[A-Z][a-z]{2,11}"));
            assertTrue("two are called " + mascot.id, ids.add(mascot.id));
            assertSame(mascot, Mascot.byId(mascot.id));
            assertTrue(Mascot.known(mascot.id));
        }
        assertEquals(4, ids.size());
        assertFalse(ids.contains(Mascot.NONE));
        assertTrue(Mascot.known(Mascot.NONE));
        assertNull(Mascot.byId(Mascot.NONE));
        assertNull(Mascot.byId("dragon"));
        assertNull(Mascot.byId(null));
        assertFalse(Mascot.known("dragon"));
        assertFalse(Mascot.known(null));
        assertFalse(Mascot.known(""));
    }

    @Test
    public void aMascotThatIsAwayDrawsNothing() {
        MascotRecorder recorder = new MascotRecorder();
        for (Mascot mascot : Mascot.all()) {
            MascotRig rig = new MascotRig();
            rig.step(FRAME);
            mascot.render(recorder, rig.pose(), rig.clock());
            assertTrue(mascot.id + " draws while it is away", recorder.shapes.isEmpty());
        }
    }

    /** A view cuts off what is drawn outside it, and a mascot with its ear cut off is no mascot. */
    @Test
    public void everyMascotStaysInItsBoxWhateverItDoes() {
        MascotRecorder recorder = new MascotRecorder();
        for (Mascot mascot : Mascot.all()) {
            MascotRig rig = new MascotRig();
            // Each mood from away, and then each straight after the other, with a nod in between.
            for (int round = 0; round < 2; round++) {
                for (Mood mood : Mood.values()) {
                    if (mood == Mood.HIDDEN) {
                        continue;
                    }
                    if (round == 0) {
                        rig.show(Mood.HIDDEN);
                        play(mascot, rig, recorder, 0.7f, mood);
                    }
                    rig.show(mood);
                    rig.nod();
                    play(mascot, rig, recorder, round == 0 ? 3.2f : 0.45f, mood);
                    assertFalse(mascot.id + " draws nothing while it is " + mood, recorder.shapes.isEmpty());
                }
            }
            rig.speak(2f, Mood.CURIOUS);
            play(mascot, rig, recorder, 3f, Mood.SPEAKING);
        }
    }

    @Test
    public void aMascotComesInFaintAndIsWhollyThereOnceItIsUp() {
        MascotRecorder recorder = new MascotRecorder();
        for (Mascot mascot : Mascot.all()) {
            MascotRig rig = new MascotRig();
            rig.show(Mood.IDLE);
            rig.step(1f / 120f);
            recorder.clear();
            mascot.render(recorder, rig.pose(), rig.clock());
            assertTrue(mascot.id + " is at full strength in its first frame", strongest(recorder) < 0.5f);
            for (int frame = 0; frame < 60; frame++) {
                rig.step(FRAME);
            }
            recorder.clear();
            mascot.render(recorder, rig.pose(), rig.clock());
            assertEquals(mascot.id, 1f, strongest(recorder), 0.001f);
        }
    }

    @Test
    public void eachMoodLooksUnlikeBeingThere() {
        // If a mood drew the same as doing nothing, nobody could tell that the Mirror listens.
        MascotRecorder recorder = new MascotRecorder();
        for (Mascot mascot : Mascot.all()) {
            String calm = settled(mascot, Mood.IDLE, recorder);
            for (Mood mood : new Mood[]{Mood.LISTENING, Mood.THINKING, Mood.CURIOUS, Mood.CONFUSED, Mood.SORRY, Mood.SLEEPY}) {
                assertFalse(mascot.id + " looks the same " + mood + " as idle", calm.equals(settled(mascot, mood, recorder)));
            }
        }
    }

    private static void play(Mascot mascot, MascotRig rig, MascotRecorder recorder, float seconds, Mood mood) {
        for (int frame = Math.round(seconds / FRAME); frame > 0; frame--) {
            rig.step(FRAME);
            recorder.clear();
            mascot.render(recorder, rig.pose(), rig.clock());
            assertTrue(mascot.id + " left a transformation unrestored", recorder.balanced());
            if (recorder.shapes.isEmpty()) {
                continue;
            }
            String where = mascot.id + ", " + mood + ", at " + rig.clock() + " s: "
                    + recorder.left + ".." + recorder.right + " by " + recorder.top + ".." + recorder.bottom;
            assertFalse(where, Float.isNaN(recorder.left + recorder.right + recorder.top + recorder.bottom));
            assertTrue("out of its box: " + where,
                    recorder.left >= -1f && recorder.right <= 1f && recorder.top >= -1f && recorder.bottom <= 1f);
            for (MascotRecorder.Shape shape : recorder.shapes) {
                assertTrue(where, shape.alpha > 0f && shape.alpha <= 1f && shape.width >= 0f);
                assertNotNull(shape.points);
            }
        }
    }

    private static float strongest(MascotRecorder recorder) {
        float strongest = 0f;
        for (MascotRecorder.Shape shape : recorder.shapes) {
            strongest = Math.max(strongest, shape.alpha);
        }
        return strongest;
    }

    /** What a mascot draws two seconds into a mood, as text that differs when the drawing does. */
    private static String settled(Mascot mascot, Mood mood, MascotRecorder recorder) {
        MascotRig rig = new MascotRig();
        rig.show(mood);
        // The same moment for every mood, and none in which it blinks.
        for (int frame = 0; frame < 36; frame++) {
            rig.step(FRAME);
        }
        recorder.clear();
        mascot.render(recorder, rig.pose(), 0f);
        StringBuilder drawn = new StringBuilder();
        for (MascotRecorder.Shape shape : recorder.shapes) {
            drawn.append(shape.points.length).append(':');
            for (float value : shape.points) {
                drawn.append(Math.round(value * 50f)).append(',');
            }
        }
        return drawn.toString();
    }
}
