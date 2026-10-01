package dev.mirror.repurpose.voicelab;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

import java.io.IOException;

public final class DownsamplerTest {
    @Test
    public void soundAlreadyAtTheRecognisersRateIsLeftAlone() throws Exception {
        short[] samples = {5, -5, 7};
        assertEquals(3, new Downsampler(16_000).process(samples, 3));
        assertArrayEquals(new short[]{5, -5, 7}, samples);
    }

    @Test
    public void wholeGroupsOfSamplesAreAveraged() throws Exception {
        short[] samples = {3, 6, 9, -30, -60, -90};
        assertEquals(2, new Downsampler(48_000).process(samples, 6));
        assertEquals(6, samples[0]);
        assertEquals(-60, samples[1]);
    }

    @Test
    public void aGroupSplitBetweenTwoReadsIsStillOneSample() throws Exception {
        Downsampler downsampler = new Downsampler(48_000);
        short[] first = {3, 6};
        short[] second = {9, 30, 60, 90, 300};
        assertEquals(0, downsampler.process(first, 2));
        assertEquals(2, downsampler.process(second, 5));
        assertEquals(6, second[0]);
        assertEquals(60, second[1]);
    }

    @Test
    public void ratesThatAreNotMultiplesOfSixteenKilohertzAreRefused() {
        for (int rate : new int[]{44_100, 8_000, 22_050}) {
            assertThrows(IOException.class, () -> new Downsampler(rate));
        }
        assertThrows(IOException.class, () -> Downsampler.toRate(new short[3], 48_000, 8_000));
    }
}
