package dev.mirror.repurpose;

import java.util.Arrays;

/**
 * Decides where a request for the assistant ends.
 *
 * <p>The recogniser knows the Mirror's commands and little else, and it ends
 * a sentence of other words where it pleases: often in the middle of one,
 * a moment after a word it could not place. Its sentence is therefore only
 * where a request begins. The request goes on until the room is as quiet as
 * it was before, and the recogniser has no words under way; or, in a room
 * that is never quiet, until the recogniser has made nothing of what it
 * hears for a while. Positions count samples since listening began.
 */
final class RequestEnd {
    /** The length of sound whose loudness is taken as one measurement: a tenth of a second. */
    static final int WINDOW_SAMPLES = VoiceService.SAMPLE_RATE / 10;
    /** How long after the recogniser's last word nothing more may have come. */
    static final int QUIET_SAMPLES = VoiceService.SAMPLE_RATE * 8 / 10;
    /** How long the room has to have been quiet as well. */
    static final int STILL_SAMPLES = VoiceService.SAMPLE_RATE / 2;
    /** A room quiet for this long ends a request whatever the recogniser still turns over. */
    static final int LONG_STILL_SAMPLES = VoiceService.SAMPLE_RATE * 12 / 10;
    /** In a room that is never quiet: how long the recogniser may have made nothing of it. */
    static final int NOTHING_SAMPLES = VoiceService.SAMPLE_RATE * 25 / 10;
    /** Nobody asks for longer than this in one breath. */
    static final int LONGEST_SAMPLES = VoiceService.SAMPLE_RATE * 12;
    /** Sound kept after the last of a request. */
    static final int TAIL_SAMPLES = VoiceService.SAMPLE_RATE / 2;
    /** How much of the sound before a request tells how quiet the room is. */
    static final int ROOM_SAMPLES = VoiceService.SAMPLE_RATE * 8;
    /** Below this nothing counts as speech: the hiss of a microphone in a silent room. */
    private static final double LEAST_THRESHOLD = 30.0;

    private final long from;
    private final double threshold;
    private long lastWordEnd;
    private long loudAt;

    /**
     * @param from where the request begins
     * @param sentenceEnd where the recogniser's last word of its first sentence ended
     * @param threshold how loud a tenth of a second has to be to count as speech
     */
    RequestEnd(long from, long sentenceEnd, double threshold) {
        this.from = from;
        this.threshold = threshold;
        this.lastWordEnd = sentenceEnd;
        this.loudAt = sentenceEnd;
    }

    /**
     * Begins with what the microphone has heard so far.
     *
     * @param room the sound before the request, by which the room's quiet is known
     * @param sentence the sound of the recogniser's sentence
     * @param since the sound after that sentence, up to now
     */
    static RequestEnd begin(long from, long sentenceEnd, short[] room, short[] sentence, short[] since) {
        RequestEnd end = new RequestEnd(from, sentenceEnd, threshold(levels(room), levels(sentence)));
        double[] later = levels(since);
        for (int index = 0; index < later.length; index++) {
            if (later[index] >= end.threshold) {
                end.loudAt = sentenceEnd + (long) (index + 1) * WINDOW_SAMPLES;
            }
        }
        return end;
    }

    /** The recogniser ended a further sentence, whose last word ended there. */
    void sentence(long wordEnd) {
        lastWordEnd = Math.max(lastWordEnd, wordEnd);
    }

    /**
     * Takes the next tenth of a second or so.
     *
     * @param heard how many samples have been heard by now
     * @param level how loud the newest of them were, as {@link #level} measures it
     * @param wordsUnderWay whether the recogniser holds words of a sentence it has not ended
     * @return where the request ends, or -1 while it goes on
     */
    long heard(long heard, double level, boolean wordsUnderWay) {
        if (level >= threshold) {
            loudAt = heard;
        }
        long still = heard - loudAt;
        long sinceWord = heard - lastWordEnd;
        if (!wordsUnderWay && sinceWord >= QUIET_SAMPLES && still >= STILL_SAMPLES) {
            return Math.min(heard, Math.max(lastWordEnd, loudAt) + TAIL_SAMPLES);
        }
        if (still >= LONG_STILL_SAMPLES && sinceWord >= QUIET_SAMPLES) {
            return Math.min(heard, loudAt + TAIL_SAMPLES);
        }
        if (!wordsUnderWay && sinceWord >= NOTHING_SAMPLES) {
            return heard;
        }
        if (heard - from >= LONGEST_SAMPLES) {
            return heard;
        }
        return -1;
    }

    double threshold() {
        return threshold;
    }

    /** How loud some sound is: the root of its mean square. */
    static double level(short[] samples, int offset, int count) {
        if (count <= 0) {
            return 0;
        }
        double sum = 0;
        for (int index = offset; index < offset + count; index++) {
            sum += (double) samples[index] * samples[index];
        }
        return Math.sqrt(sum / count);
    }

    /** The loudness of each whole tenth of a second. */
    static double[] levels(short[] samples) {
        double[] levels = new double[samples.length / WINDOW_SAMPLES];
        for (int index = 0; index < levels.length; index++) {
            levels[index] = level(samples, index * WINDOW_SAMPLES, WINDOW_SAMPLES);
        }
        return levels;
    }

    /**
     * Halfway, as the ear counts, between the room at its quietest and the
     * speaker: above what the room does by itself, below all but the gaps
     * between words.
     */
    static double threshold(double[] room, double[] speech) {
        double floor = Math.max(1.0, percentile(room, 10));
        double spoken = percentile(speech, 75);
        return Math.max(LEAST_THRESHOLD, Math.max(floor * 2, Math.sqrt(floor * spoken)));
    }

    private static double percentile(double[] values, int percent) {
        if (values.length == 0) {
            return 0;
        }
        double[] sorted = values.clone();
        Arrays.sort(sorted);
        return sorted[Math.min(sorted.length - 1, sorted.length * percent / 100)];
    }
}
