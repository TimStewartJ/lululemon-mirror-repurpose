package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class ConfigStoreTest {
    @Test
    public void retiredSourcesFallBackToBuiltInDashboard() {
        assertEquals("", ConfigStore.normalizeDashboardUrl(null));
        assertEquals("", ConfigStore.normalizeDashboardUrl(""));
        assertEquals("", ConfigStore.normalizeDashboardUrl(
                "http://127.0.0.1:8787/dashboard/gallery.html"));
        assertEquals("", ConfigStore.normalizeDashboardUrl(
                "http://127.0.0.1:8787/dashboard/aurora.html"));
        assertEquals(
                "http://192.168.1.10:8123/dashboard",
                ConfigStore.normalizeDashboardUrl("http://192.168.1.10:8123/dashboard"));
    }
}
