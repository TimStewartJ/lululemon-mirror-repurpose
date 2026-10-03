package dev.mirror.repurpose;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class RequestEndTest {
    private static final int RATE = VoiceService.SAMPLE_RATE;
    private static final int STEP = RequestEnd.WINDOW_SAMPLES;
    private static final double QUIET = 10;
    private static final double LOUD = 3_000;

    /** Feeds tenths of a second from {@code from} on; returns where the request ended and when that was known. */
    private static long[] run(RequestEnd end, long from, double[] levels, boolean[] wordsUnderWay) {
        for (int index = 0; index < levels.length; index++) {
            long heard = from + (long) (index + 1) * STEP;
            long ended = end.heard(heard, levels[index], wordsUnderWay[index]);
            if (ended >= 0) {
                return new long[]{ended, heard};
            }
        }
        return new long[]{-1, -1};
    }

    private static double[] levels(Object... parts) {
        int length = 0;
        for (int index = 0; index < parts.length; index += 2) {
            length += (Integer) parts[index];
        }
        double[] levels = new double[length];
        int at = 0;
        for (int index = 0; index < parts.length; index += 2) {
            for (int count = 0; count < (Integer) parts[index]; count++) {
                levels[at++] = (Double) parts[index + 1];
            }
        }
        return levels;
    }

    private static boolean[] flags(Object... parts) {
        int length = 0;
        for (int index = 0; index < parts.length; index += 2) {
            length += (Integer) parts[index];
        }
        boolean[] flags = new boolean[length];
        int at = 0;
        for (int index = 0; index < parts.length; index += 2) {
            for (int count = 0; count < (Integer) parts[index]; count++) {
                flags[at++] = (Boolean) parts[index + 1];
            }
        }
        return flags;
    }

    @Test
    public void aSentenceThatEndedInQuietIsTheWholeRequest() {
        // The recogniser waited a second of silence before it ended the sentence.
        long sentenceEnd = 5L * RATE;
        RequestEnd end = new RequestEnd(3L * RATE, sentenceEnd, 100);
        long[] ended = run(end, sentenceEnd + RATE, levels(5, QUIET), flags(5, false));

        assertEquals("Known with the first sound after it", sentenceEnd + RATE + STEP, ended[1]);
        assertEquals(sentenceEnd + RequestEnd.TAIL_SAMPLES, ended[0]);
    }

    @Test
    public void aSentenceEndedInTheMiddleOfSpeechGoesOnUntilTheSpeakerStops() {
        long sentenceEnd = 5L * RATE;
        RequestEnd end = new RequestEnd(3L * RATE, sentenceEnd, 100);
        // Two more seconds of speech, of which the recogniser holds words; then its
        // sentence ends, and the room is quiet.
        double[] levels = levels(20, LOUD, 15, QUIET);
        boolean[] words = flags(2, false, 26, true, 7, false);
        long spokenUntil = sentenceEnd + 2L * RATE;
        long[] ended = new long[]{-1, -1};
        for (int index = 0; index < levels.length && ended[0] < 0; index++) {
            long heard = sentenceEnd + (long) (index + 1) * STEP;
            if (index == 28) {
                end.sentence(spokenUntil);
            }
            long at = end.heard(heard, levels[index], words[index]);
            if (at >= 0) {
                ended = new long[]{at, heard};
            }
        }

        assertEquals(spokenUntil + RequestEnd.TAIL_SAMPLES, ended[0]);
        assertEquals("Known as soon as the recogniser let go", sentenceEnd + 29L * STEP, ended[1]);
    }

    @Test
    public void speechThatTheRecogniserMakesNothingOfStillBelongsToTheRequest() {
        // From across a room the recogniser may hear no words for a second and more.
        long sentenceEnd = 5L * RATE;
        RequestEnd end = new RequestEnd(3L * RATE, sentenceEnd, 100);
        long[] ended = run(end, sentenceEnd, levels(20, LOUD, 10, QUIET), flags(30, false));

        assertEquals(sentenceEnd + 2L * RATE + RequestEnd.TAIL_SAMPLES, ended[0]);
        assertEquals(sentenceEnd + 2L * RATE + RequestEnd.STILL_SAMPLES, ended[1]);
    }

    @Test
    public void wordsTheRecogniserNeverLetsGoOfDoNotHoldAQuietRoomUp() {
        long sentenceEnd = 5L * RATE;
        RequestEnd end = new RequestEnd(3L * RATE, sentenceEnd, 100);
        long[] ended = run(end, sentenceEnd, levels(5, LOUD, 30, QUIET), flags(35, true));

        long spokenUntil = sentenceEnd + 5L * STEP;
        assertEquals(spokenUntil + RequestEnd.TAIL_SAMPLES, ended[0]);
        assertEquals(spokenUntil + RequestEnd.LONG_STILL_SAMPLES, ended[1]);
    }

    @Test
    public void inARoomThatIsNeverQuietTheRecogniserDecides() {
        long sentenceEnd = 5L * RATE;
        RequestEnd end = new RequestEnd(3L * RATE, sentenceEnd, 100);
        long[] ended = run(end, sentenceEnd, levels(60, LOUD), flags(60, false));

        assertEquals(sentenceEnd + RequestEnd.NOTHING_SAMPLES, ended[1]);
        assertEquals(ended[1], ended[0]);
    }

    @Test
    public void nobodyIsListenedToForever() {
        long from = 3L * RATE;
        RequestEnd end = new RequestEnd(from, 5L * RATE, 100);
        long[] ended = run(end, 5L * RATE, levels(200, LOUD), flags(200, true));

        assertEquals(from + RequestEnd.LONGEST_SAMPLES, ended[1]);
        assertEquals(ended[1], ended[0]);
    }

    @Test
    public void whatWasHeardBeforeTheRequestWasAskedForCounts() {
        short[] room = sound(80, 20);
        short[] sentence = sound(20, 4_000);
        // Half a second more of speech arrived before anyone asked.
        short[] since = sound(5, 4_000);
        long sentenceEnd = 10L * RATE;
        RequestEnd end = RequestEnd.begin(8L * RATE, sentenceEnd, room, sentence, since);

        assertTrue("Between the room and the speaker: " + end.threshold(),
                end.threshold() > 40 && end.threshold() < 2_000);
        long[] ended = run(end, sentenceEnd + 5L * STEP, levels(10, QUIET), flags(10, false));
        assertEquals(sentenceEnd + 5L * STEP + RequestEnd.TAIL_SAMPLES, ended[0]);
    }

    @Test
    public void theThresholdLiesAboveTheRoomAndBelowTheSpeaker() {
        assertEquals(Math.sqrt(50 * 3_000.0), RequestEnd.threshold(levels(10, 50.0), levels(10, 3_000.0)), 0.01);
        // A room nearly as loud as the speaker: twice the room, so that the room alone is quiet.
        assertEquals(2_000, RequestEnd.threshold(levels(10, 1_000.0), levels(10, 1_500.0)), 0.01);
        // A microphone that delivers nothing between words.
        assertEquals(Math.sqrt(3_000.0), RequestEnd.threshold(levels(10, 0.0), levels(10, 3_000.0)), 0.01);
        assertEquals(30, RequestEnd.threshold(new double[0], new double[0]), 0.01);
    }

    @Test
    public void loudnessIsMeasuredByTheTenthOfASecond() {
        short[] samples = sound(3, 1_000);
        assertArrayEquals(new double[]{1_000, 1_000, 1_000}, RequestEnd.levels(samples), 0.01);
        assertEquals(0, RequestEnd.levels(new short[STEP - 1]).length);
        assertEquals(0, RequestEnd.level(samples, 0, 0), 0);
    }

    private static short[] sound(int tenths, int amplitude) {
        short[] samples = new short[tenths * STEP];
        for (int index = 0; index < samples.length; index++) {
            samples[index] = (short) (index % 2 == 0 ? amplitude : -amplitude);
        }
        return samples;
    }
}
