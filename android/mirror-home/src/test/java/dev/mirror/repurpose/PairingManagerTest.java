package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public final class PairingManagerTest {
    @Test
    public void normalizesClientNames() {
        assertEquals("Device", PairingManager.normalizeClientName(null));
        assertEquals("Device", PairingManager.normalizeClientName(" \n\t "));
        assertEquals("Phone", PairingManager.normalizeClientName(" Phone "));
        assertEquals(
                64,
                PairingManager.normalizeClientName(
                        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-extra")
                        .length());
    }
}
