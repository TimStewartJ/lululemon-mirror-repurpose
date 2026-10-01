package dev.mirror.repurpose.voicelab;

import java.io.IOException;

/**
 * Brings a recording down to the recogniser's 16 kHz by averaging whole
 * groups of samples. That is a crude filter, but speech recognition uses
 * nothing above 8 kHz and the rates a microphone offers are multiples of 16.
 */
final class Downsampler {
    static final int TARGET_RATE = 16_000;

    private final int factor;
    private long sum;
    private int pending;

    Downsampler(int sourceRate) throws IOException {
        if (sourceRate < TARGET_RATE || sourceRate % TARGET_RATE != 0) {
            throw new IOException(sourceRate + " Hz is not a multiple of " + TARGET_RATE + " Hz");
        }
        factor = sourceRate / TARGET_RATE;
    }

    /** Converts the next samples in place and returns how many came out. */
    int process(short[] samples, int count) {
        if (factor == 1) {
            return count;
        }
        int written = 0;
        for (int index = 0; index < count; index++) {
            sum += samples[index];
            if (++pending == factor) {
                samples[written++] = (short) (sum / factor);
                sum = 0;
                pending = 0;
            }
        }
        return written;
    }

    static short[] toRate(short[] samples, int sourceRate, int targetRate) throws IOException {
        if (targetRate != TARGET_RATE) {
            throw new IOException("Only " + TARGET_RATE + " Hz output is supported");
        }
        Downsampler downsampler = new Downsampler(sourceRate);
        short[] copy = samples.clone();
        int count = downsampler.process(copy, copy.length);
        return count == copy.length ? copy : java.util.Arrays.copyOf(copy, count);
    }
}
