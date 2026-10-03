package dev.mirror.repurpose;

import static org.junit.Assert.assertArrayEquals;

import org.junit.Test;

public class VoiceServicePlaceTest {
    private static final int RATE = VoiceService.SAMPLE_RATE;

    private static String sentence(double start, double end) {
        return "{\"result\": [{\"word\": \"mirror\", \"conf\": 1, \"start\": " + start + ", \"end\": 1.0},"
                + " {\"word\": \"[unk]\", \"conf\": 0.4, \"start\": 1.1, \"end\": " + end + "}],"
                + " \"text\": \"mirror [unk]\"}";
    }

    @Test
    public void aSentenceLiesWhereItsWordsDoWithALittleBefore() {
        assertArrayEquals(
                new long[]{RATE / 2, 3L * RATE},
                VoiceService.place(sentence(1.5, 3.0), 0, 10L * RATE));
    }

    @Test
    public void wordTimesCountFromWhereTheRecogniserInUseBegan() {
        long began = 120L * RATE;
        assertArrayEquals(
                new long[]{began + RATE / 2, began + 3L * RATE},
                VoiceService.place(sentence(1.5, 3.0), began, began + 10L * RATE));
    }

    @Test
    public void aSentenceLiesWithinWhatWasHeard() {
        assertArrayEquals(new long[]{0, 2L * RATE}, VoiceService.place(sentence(0.2, 3.0), 0, 2L * RATE));
    }

    @Test
    public void theSoundBeforeASentenceIsASecond() {
        // The first word of a request was placed late by its own length, and lost with less.
        assertArrayEquals(new long[]{4L * RATE, 8L * RATE}, VoiceService.place(sentence(5.0, 8.0), 0, 10L * RATE));
    }

    @Test
    public void aSentenceWithoutWordTimesLiesNowhere() {
        assertArrayEquals(new long[]{-1, -1}, VoiceService.place("{\"text\": \"mirror\"}", 0, RATE));
        assertArrayEquals(new long[]{-1, -1}, VoiceService.place("{\"text\": \"\"}", 0, RATE));
        assertArrayEquals(new long[]{-1, -1}, VoiceService.place("not json", 0, RATE));
    }
}
