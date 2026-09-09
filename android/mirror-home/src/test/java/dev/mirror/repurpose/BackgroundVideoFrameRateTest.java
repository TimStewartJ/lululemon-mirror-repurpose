package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import org.junit.Test;

public final class BackgroundVideoFrameRateTest {
    @Test
    public void returnsZeroForInsufficientSamples() {
        BackgroundVideoLibrary.FrameRateStats stats =
                BackgroundVideoLibrary.analyzeFrameRate(
                        new ArrayList<>(Arrays.asList(123L, 123L)));

        assertEquals(0f, stats.nominal, 0f);
        assertEquals(0f, stats.maximumSustained, 0f);
    }

    @Test
    public void infersPresentationRateFromOutOfOrderBFrames() {
        List<Long> decodeOrder = new ArrayList<>(Arrays.asList(
                0L,
                125_000L,
                41_667L,
                83_333L,
                250_000L,
                166_667L,
                208_333L));

        assertEquals(
                24f,
                BackgroundVideoLibrary.frameRateFromSampleTimes(decodeOrder),
                0.02f);
    }

    @Test
    public void ignoresDuplicatePresentationTimestamps() {
        List<Long> timestamps = new ArrayList<>(Arrays.asList(
                0L,
                0L,
                33_333L,
                66_667L,
                100_000L));

        assertEquals(
                30f,
                BackgroundVideoLibrary.frameRateFromSampleTimes(timestamps),
                0.02f);
    }

    @Test
    public void detectsHighRateSectionAfterSlowOpening() {
        List<Long> timestamps = new ArrayList<>();
        long timeUs = 0;
        for (int index = 0; index < 120; index++) {
            timestamps.add(timeUs);
            timeUs += 41_667L;
        }
        for (int index = 0; index < 120; index++) {
            timestamps.add(timeUs);
            timeUs += 16_667L;
        }

        BackgroundVideoLibrary.FrameRateStats stats =
                BackgroundVideoLibrary.analyzeFrameRate(timestamps);

        assertEquals(60f, stats.maximumSustained, 0.1f);
    }
}
