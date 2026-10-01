package dev.mirror.repurpose.voicelab;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

public final class ListenerTest {
    private static final int FRAME = Listener.FRAME_SAMPLES;

    /** A recogniser that counts what it is given and ends a sentence on request. */
    private static final class FakeEngine implements Listener.Engine {
        int frames;
        int finals;
        int endAfterFrames = -1;
        String pending = "";

        @Override
        public boolean accept(short[] samples, int count) {
            assertEquals(FRAME, count);
            frames++;
            if (samples[0] != 0) {
                pending = "mirror wake up";
            }
            return frames == endAfterFrames;
        }

        @Override
        public String result() {
            String text = pending;
            pending = "";
            return "{\"text\": \"" + text + "\"}";
        }

        @Override
        public String finalResult() {
            finals++;
            return result();
        }
    }

    private static short[] frames(int count, int level) {
        short[] samples = new short[count * FRAME];
        java.util.Arrays.fill(samples, (short) level);
        return samples;
    }

    private final List<String> heard = new ArrayList<>();

    private Listener listener(FakeEngine engine, boolean gated) {
        return new Listener(engine, gated, result -> heard.add(result.getString("text")));
    }

    @Test
    public void withoutTheGateEverythingReachesTheRecogniser() throws Exception {
        FakeEngine engine = new FakeEngine();
        Listener listener = listener(engine, false);
        listener.feed(frames(10, 0), 10 * FRAME);
        listener.feed(frames(5, 3000), 5 * FRAME);
        listener.finish();

        assertEquals(15, engine.frames);
        assertEquals(1.0, listener.fedShare(), 0.0);
        assertEquals(java.util.Collections.singletonList("mirror wake up"), heard);
        assertEquals(0.45, listener.secondsSeen(), 1e-9);
    }

    @Test
    public void soundArrivesInAnyPiecesAndIsHandedOnInWholeFrames() throws Exception {
        FakeEngine engine = new FakeEngine();
        Listener listener = listener(engine, false);
        short[] sound = frames(3, 0);
        listener.feed(sound, 100);
        listener.feed(sound, FRAME);
        listener.feed(sound, 2 * FRAME - 100);
        assertEquals(3, engine.frames);
    }

    @Test
    public void aSentenceTheRecogniserEndsIsReportedAtOnce() throws Exception {
        FakeEngine engine = new FakeEngine();
        engine.endAfterFrames = 4;
        Listener listener = listener(engine, false);
        listener.feed(frames(4, 3000), 4 * FRAME);
        assertEquals(java.util.Collections.singletonList("mirror wake up"), heard);
        listener.finish();
        // Nothing more was said, so nothing more is reported.
        assertEquals(1, heard.size());
    }

    @Test
    public void behindTheGateAQuietRoomCostsTheRecogniserNothing() throws Exception {
        FakeEngine engine = new FakeEngine();
        Listener listener = listener(engine, true);
        listener.feed(frames(300, 20), 300 * FRAME);
        listener.finish();

        assertEquals(0, engine.frames);
        assertEquals(0, engine.finals);
        assertEquals(0.0, listener.fedShare(), 0.0);
        assertTrue(heard.isEmpty());
    }

    @Test
    public void speechPassesTheGateWithTheMomentBeforeIt() throws Exception {
        FakeEngine engine = new FakeEngine();
        Listener listener = listener(engine, true);
        listener.feed(frames(100, 20), 100 * FRAME);
        listener.feed(frames(40, 3000), 40 * FRAME);
        // Ten frames from before the voice, so a first syllable is not lost.
        assertEquals(50, engine.frames);

        listener.feed(frames(SpeechGate.HANG_FRAMES, 20), SpeechGate.HANG_FRAMES * FRAME);
        // The pause is heard too, up to the frame that closes the gate.
        assertEquals(50 + SpeechGate.HANG_FRAMES - 1, engine.frames);
        assertEquals(1, engine.finals);
        assertEquals(java.util.Collections.singletonList("mirror wake up"), heard);

        listener.finish();
        assertEquals(1, engine.finals);
        assertTrue(listener.fedShare() > 0.4 && listener.fedShare() < 0.5);
    }

    @Test
    public void speechStillGoingWhenTheStreamEndsIsReported() throws Exception {
        FakeEngine engine = new FakeEngine();
        Listener listener = listener(engine, true);
        listener.feed(frames(30, 20), 30 * FRAME);
        listener.feed(frames(20, 3000), 20 * FRAME);
        listener.finish();
        assertEquals(java.util.Collections.singletonList("mirror wake up"), heard);
    }

    @Test
    public void twoSentencesApartAreTwoResults() throws Exception {
        FakeEngine engine = new FakeEngine();
        Listener listener = listener(engine, true);
        for (int sentence = 0; sentence < 2; sentence++) {
            listener.feed(frames(60, 20), 60 * FRAME);
            listener.feed(frames(30, 3000), 30 * FRAME);
        }
        listener.feed(frames(60, 20), 60 * FRAME);
        assertEquals(java.util.Arrays.asList("mirror wake up", "mirror wake up"), heard);
    }
}
