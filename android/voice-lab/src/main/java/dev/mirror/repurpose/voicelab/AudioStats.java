package dev.mirror.repurpose.voicelab;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Arrays;

/**
 * What a recording says about a microphone before anyone listens to it: how
 * loud it is, how much of it is clipped, and how far speech stands above the
 * room's own noise.
 */
final class AudioStats {
    private static final int FULL_SCALE = 32768;
    /** Frames this long are compared to tell speech from the room. */
    static final int FRAME_MS = 30;

    private AudioStats() {
    }

    static double decibels(double amplitude) {
        return amplitude <= 0 ? -120.0 : Math.max(-120.0, 20.0 * Math.log10(amplitude / FULL_SCALE));
    }

    static double rms(short[] samples, int offset, int length) {
        if (length <= 0) {
            return 0;
        }
        double sum = 0;
        for (int index = offset; index < offset + length; index++) {
            sum += (double) samples[index] * samples[index];
        }
        return Math.sqrt(sum / length);
    }

    /** RMS of every whole frame of one channel, in recording order. */
    static double[] frameLevels(short[] samples, int sampleRate) {
        int frame = sampleRate * FRAME_MS / 1000;
        double[] levels = new double[frame == 0 ? 0 : samples.length / frame];
        for (int index = 0; index < levels.length; index++) {
            levels[index] = rms(samples, index * frame, frame);
        }
        return levels;
    }

    /** The level below which the given share of frames fall. */
    static double percentile(double[] levels, double share) {
        if (levels.length == 0) {
            return 0;
        }
        double[] sorted = levels.clone();
        Arrays.sort(sorted);
        int index = (int) Math.round(share * (sorted.length - 1));
        return sorted[Math.max(0, Math.min(sorted.length - 1, index))];
    }

    static JSONObject describe(short[] samples, int sampleRate) throws JSONException {
        int peak = 0;
        int clipped = 0;
        long sum = 0;
        int firstSound = -1;
        for (int index = 0; index < samples.length; index++) {
            int value = Math.abs((int) samples[index]);
            peak = Math.max(peak, value);
            if (value >= FULL_SCALE - 2) {
                clipped++;
            }
            if (firstSound < 0 && value > 0) {
                firstSound = index;
            }
            sum += samples[index];
        }
        double[] levels = frameLevels(samples, sampleRate);
        // The quietest tenth of a recording is the room; the loudest tenth is the speech.
        double floor = percentile(levels, 0.10);
        double loud = percentile(levels, 0.90);
        return new JSONObject()
                .put("seconds", round(samples.length / (double) sampleRate))
                .put("peakDb", round(decibels(peak)))
                .put("rmsDb", round(decibels(rms(samples, 0, samples.length))))
                .put("noiseFloorDb", round(decibels(floor)))
                .put("loudDb", round(decibels(loud)))
                .put("speechAboveNoiseDb", round(decibels(loud) - decibels(floor)))
                .put("clippedSamples", clipped)
                .put("dcOffset", samples.length == 0 ? 0 : sum / samples.length)
                .put("silent", peak == 0)
                .put(
                        "firstSoundSeconds",
                        firstSound < 0 ? JSONObject.NULL : round(firstSound / (double) sampleRate));
    }

    static double round(double value) {
        return Math.round(value * 100.0) / 100.0;
    }
}
