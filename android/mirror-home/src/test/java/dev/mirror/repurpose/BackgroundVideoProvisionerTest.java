package dev.mirror.repurpose;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

import org.junit.Test;

public final class BackgroundVideoProvisionerTest {
    @Test
    public void bootstrapMatchesOnlyTheConfiguredSecret() throws Exception {
        String secret = "random-bootstrap-secret-that-is-never-committed";
        String hash = hex(MessageDigest.getInstance("SHA-256")
                .digest(secret.getBytes(StandardCharsets.UTF_8)));

        assertTrue(BackgroundVideoProvisioner.matches(hash, secret));
        assertFalse(BackgroundVideoProvisioner.matches(hash, secret + "x"));
        assertFalse(BackgroundVideoProvisioner.matches("", secret));
        assertFalse(BackgroundVideoProvisioner.matches(hash, "short"));
    }

    @Test
    public void consumedStateHonorsCurrentAndLegacyMarkers() {
        String hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                + "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

        assertFalse(BackgroundVideoProvisioner.isConsumed(hash, false, ""));
        assertTrue(BackgroundVideoProvisioner.isConsumed(hash, true, ""));
        assertTrue(BackgroundVideoProvisioner.isConsumed(hash, false, hash));
        assertFalse(BackgroundVideoProvisioner.isConsumed(
                hash,
                false,
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                        + "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"));
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder();
        for (byte value : bytes) {
            result.append(String.format(java.util.Locale.US, "%02x", value & 0xff));
        }
        return result.toString();
    }
}
