package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class AmbientVideoRetryTest {
    @Test
    public void theFirstTryComesWithinSeconds() {
        assertEquals(5_000L, new AmbientVideoRetry().nextDelayMs());
    }

    @Test
    public void triesGrowFartherApartAndThenStayFiveMinutesApart() {
        AmbientVideoRetry retry = new AmbientVideoRetry();

        assertEquals(5_000L, retry.nextDelayMs());
        assertEquals(15_000L, retry.nextDelayMs());
        assertEquals(60_000L, retry.nextDelayMs());
        for (int attempt = 0; attempt < 50; attempt++) {
            assertEquals(300_000L, retry.nextDelayMs());
        }
    }

    @Test
    public void aVideoThatPlayedAgainIsTriedQuicklyTheNextTimeItFails() {
        AmbientVideoRetry retry = new AmbientVideoRetry();
        retry.nextDelayMs();
        retry.nextDelayMs();
        retry.nextDelayMs();

        retry.succeeded();

        assertEquals(5_000L, retry.nextDelayMs());
    }
}
