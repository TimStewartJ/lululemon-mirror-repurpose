package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class WifiDirectOnboardingTest {
    @Test
    public void escapesWifiQrCredentials() {
        assertEquals(
                "WIFI:T:WPA;S:DIRECT\\:Mirror;P:p\\;a\\\\ss;;",
                WifiDirectOnboarding.wifiQrPayload("DIRECT:Mirror", "p;a\\ss"));
    }
}
