package dev.mirror.repurpose.updater;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.HashSet;
import java.util.Set;
import java.security.MessageDigest;

import org.junit.Test;

public final class OtaAuthenticatorTest {
    @Test
    public void canonicalRequestIsStableAcrossImplementations() {
        assertEquals(
                "PUT\n/api/v1/update\n1787600000\n"
                        + "00112233445566778899aabbccddeeff\n"
                        + "abcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd",
                OtaAuthenticator.canonicalRequest(
                        "put",
                        "/api/v1/update",
                        "1787600000",
                        "00112233445566778899AABBCCDDEEFF",
                        "ABCDEFABCDEFABCDEFABCDEFABCDEFABCDEFABCDEFABCDEFABCDEFABCDEFABCD"));
    }

    @Test
    public void hmacMatchesHostClientVector() throws Exception {
        String canonical = OtaAuthenticator.canonicalRequest(
                "GET",
                "/api/v1/status",
                "1787600000",
                "00112233445566778899aabbccddeeff",
                OtaAuthenticator.EMPTY_SHA256);
        assertEquals(
                "b9a4973b26871e37e55088196c48d4c80c618f7b1e235ba4812aba0696e121e9",
                OtaAuthenticator.hmacHex(
                        "test-token-0123456789",
                        canonical));
    }

    @Test
    public void counterWindowAcceptsOutOfOrderRequestsAndRejectsReplay() throws Exception {
        long latest = 1_000_000_000_000L;
        Set<String> counters = new HashSet<>();
        counters.add(Long.toString(latest));

        OtaAuthenticator.CounterState state = OtaAuthenticator.advanceCounter(
                latest - 1_000_000L,
                latest,
                counters,
                true);

        assertEquals(latest, state.maximum);
        assertTrue(state.counters.contains(Long.toString(latest - 1_000_000L)));
        try {
            OtaAuthenticator.advanceCounter(
                    latest - 1_000_000L,
                    state.maximum,
                    state.counters,
                    true);
        } catch (OtaAuthenticator.AuthException expected) {
            assertTrue(expected.getMessage().contains("already used"));
            return;
        }
        throw new AssertionError("Replay counter was accepted");
    }

    @Test(expected = OtaAuthenticator.AuthException.class)
    public void counterWindowRejectsRequestsOutsideReorderingWindow() throws Exception {
        long latest = 1_000_000_000_000L;
        Set<String> counters = new HashSet<>();
        counters.add(Long.toString(latest));
        OtaAuthenticator.advanceCounter(
                latest - 301_000_000_000L,
                latest,
                counters,
                true);
    }

    @Test(expected = OtaAuthenticator.AuthException.class)
    public void counterMigrationRejectsPreviouslyAcceptedMaximum() throws Exception {
        OtaAuthenticator.advanceCounter(
                100,
                100,
                new HashSet<String>(),
                false);
    }

    @Test
    public void bootstrapTokenRequiresMatchingIndependentHash() throws Exception {
        String token = "bootstrap-secret";
        byte[] digest = MessageDigest.getInstance("SHA-256")
                .digest(token.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        StringBuilder hash = new StringBuilder();
        for (byte item : digest) {
            hash.append(String.format(java.util.Locale.US, "%02x", item & 0xff));
        }

        assertTrue(OtaAuthenticator.bootstrapTokenMatches(token, hash.toString()));
        assertTrue(!OtaAuthenticator.bootstrapTokenMatches("wrong", hash.toString()));
    }

    @Test
    public void actualPermissionBodyMustMatchTheSignedHash() throws Exception {
        byte[] body = "{\"granted\":true}".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(body);
        StringBuilder hash = new StringBuilder();
        for (byte item : digest) {
            hash.append(String.format(java.util.Locale.US, "%02x", item & 0xff));
        }
        OtaAuthenticator.verifyBodySha256(body, hash.toString());
        try {
            OtaAuthenticator.verifyBodySha256(
                    "{\"granted\":false}".getBytes(java.nio.charset.StandardCharsets.UTF_8),
                    hash.toString());
        } catch (OtaAuthenticator.AuthException expected) {
            assertTrue(expected.getMessage().contains("does not match"));
            return;
        }
        throw new AssertionError("Altered permission body was accepted");
    }
}
