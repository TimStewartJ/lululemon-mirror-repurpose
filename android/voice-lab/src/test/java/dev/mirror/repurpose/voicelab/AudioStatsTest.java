package dev.mirror.repurpose.voicelab;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public final class AudioStatsTest {
    private static final int RATE = 16_000;

    private static short[] tone(double amplitude, double seconds) {
        short[] samples = new short[(int) (RATE * seconds)];
        for (int index = 0; index < samples.length; index++) {
            samples[index] = (short) Math.round(amplitude * Math.sin(2 * Math.PI * 440 * index / RATE));
        }
        return samples;
    }

    @Test
    public void aMicrophoneThatDeliversNothingIsCalledSilent() throws Exception {
        JSONObject stats = AudioStats.describe(new short[RATE], RATE);
        assertTrue(stats.getBoolean("silent"));
        assertEquals(-120.0, stats.getDouble("peakDb"), 0.0);
        assertEquals(-120.0, stats.getDouble("rmsDb"), 0.0);
        assertTrue(stats.isNull("firstSoundSeconds"));
        assertEquals(1.0, stats.getDouble("seconds"), 0.0);
    }

    @Test
    public void aFullScaleToneMeasuresAsOne() throws Exception {
        JSONObject stats = AudioStats.describe(tone(32767, 1), RATE);
        assertFalse(stats.getBoolean("silent"));
        assertEquals(0.0, stats.getDouble("peakDb"), 0.01);
        // A sine is three decibels below its own peak.
        assertEquals(-3.01, stats.getDouble("rmsDb"), 0.02);
        assertEquals(0, stats.getInt("dcOffset"));
    }

    @Test
    public void halvingTheAmplitudeCostsSixDecibels() {
        assertEquals(-6.02, AudioStats.decibels(16384), 0.01);
        assertEquals(-120.0, AudioStats.decibels(0), 0.0);
    }

    @Test
    public void samplesAtTheLimitAreCountedAsClipped() throws Exception {
        short[] samples = {32767, -32768, 32766, 100, -32767};
        assertEquals(4, AudioStats.describe(samples, RATE).getInt("clippedSamples"));
    }

    @Test
    public void speechIsMeasuredAgainstTheRoom() throws Exception {
        // Four seconds of a quiet room with one second of something forty decibels louder.
        short[] samples = tone(30, 5);
        short[] loud = tone(3000, 1);
        System.arraycopy(loud, 0, samples, 2 * RATE, loud.length);

        JSONObject stats = AudioStats.describe(samples, RATE);
        assertEquals(40.0, stats.getDouble("speechAboveNoiseDb"), 0.5);
        assertEquals(AudioStats.decibels(30 / Math.sqrt(2)), stats.getDouble("noiseFloorDb"), 0.5);
    }

    @Test
    public void aSilentStartIsTimed() throws Exception {
        short[] samples = new short[RATE];
        samples[RATE / 4] = 9;
        assertEquals(0.25, AudioStats.describe(samples, RATE).getDouble("firstSoundSeconds"), 0.0);
    }

    @Test
    public void framesAreThirtyMillisecondsAndPartialOnesAreDropped() {
        assertEquals(33, AudioStats.frameLevels(new short[RATE], RATE).length);
        assertEquals(0, AudioStats.frameLevels(new short[100], RATE).length);
        assertEquals(0.0, AudioStats.percentile(new double[0], 0.5), 0.0);
        assertEquals(3.0, AudioStats.percentile(new double[]{5, 1, 3}, 0.5), 0.0);
    }
}
