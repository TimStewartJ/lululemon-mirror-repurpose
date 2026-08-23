package dev.mirror.repurpose;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertEquals;
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

    @Test
    public void validatesMediaUrls() {
        assertTrue(InputValidator.validMediaUrl("http://192.168.1.20:4317/media-files/demo.mp4"));
        assertTrue(InputValidator.validMediaUrl("https://example.test/live/stream.m3u8"));
        assertTrue(InputValidator.validMediaUrl("rtsp://192.168.1.20/live"));
        assertFalse(InputValidator.validMediaUrl("file:///sdcard/private.mp4"));
        assertFalse(InputValidator.validMediaUrl("javascript:alert(1)"));
    }

    @Test
    public void validatesTimeZones() {
        assertTrue(InputValidator.validTimeZone("America/Los_Angeles"));
        assertTrue(InputValidator.validTimeZone("UTC"));
        assertFalse(InputValidator.validTimeZone("not a time zone"));
        assertFalse(InputValidator.validTimeZone(""));
    }

    @Test
    public void parsesScheduleTimes() {
        assertEquals(0, InputValidator.parseTimeMinutes("00:00"));
        assertEquals(7 * 60 + 30, InputValidator.parseTimeMinutes("07:30"));
        assertEquals(23 * 60 + 59, InputValidator.parseTimeMinutes("23:59"));
        assertEquals(-1, InputValidator.parseTimeMinutes("24:00"));
        assertEquals(-1, InputValidator.parseTimeMinutes("7:30"));
    }
}
