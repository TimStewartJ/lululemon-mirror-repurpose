package dev.mirror.repurpose;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class InputValidatorTest {
    @Test
    public void validatesWifiInputs() {
        assertTrue(InputValidator.validSsid("Home WiFi"));
        assertFalse(InputValidator.validSsid(""));
        assertFalse(InputValidator.validSsid(
                "this-network-name-is-longer-than-thirty-two-bytes"));

        assertTrue(InputValidator.validWpaPassphrase("correct horse battery staple"));
        assertTrue(InputValidator.validWpaPassphrase(
                "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"));
        assertFalse(InputValidator.validWpaPassphrase("short"));
    }

    @Test
    public void validatesDashboardUrls() {
        assertTrue(InputValidator.validDashboardUrl(""));
        assertTrue(InputValidator.validDashboardUrl("http://192.168.1.10:8123/dashboard"));
        assertTrue(InputValidator.validDashboardUrl("https://example.test/mirror"));
        assertFalse(InputValidator.validDashboardUrl("javascript:alert(1)"));
        assertFalse(InputValidator.validDashboardUrl("not a url"));
    }
}
