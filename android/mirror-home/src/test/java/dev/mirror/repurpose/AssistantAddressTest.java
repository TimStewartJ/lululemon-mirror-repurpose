package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class AssistantAddressTest {
    @Test
    public void anAddressIsKeptAsSchemeHostAndPort() {
        assertEquals("http://10.0.0.90:8790", AssistantAddress.normalize("http://10.0.0.90:8790"));
        assertEquals("http://10.0.0.90:8790", AssistantAddress.normalize("  http://10.0.0.90:8790/ "));
        assertEquals("https://attic.example:443", AssistantAddress.normalize("HTTPS://Attic.Example:443"));
        assertEquals("http://attic", AssistantAddress.normalize("http://attic"));
    }

    @Test
    public void anAddressTypedWithoutHttpIsTakenAsHttp() {
        assertEquals("http://10.0.0.90:8790", AssistantAddress.normalize("10.0.0.90:8790"));
        assertEquals("http://attic.local:8790", AssistantAddress.normalize("attic.local:8790"));
    }

    @Test
    public void nothingMeansNoCompanion() {
        assertEquals("", AssistantAddress.normalize(""));
        assertEquals("", AssistantAddress.normalize("   "));
        assertEquals("", AssistantAddress.normalize(null));
    }

    @Test
    public void whatIsNoUsableAddressIsRefusedWithTheReason() {
        for (String[] refused : new String[][]{
                {"ftp://10.0.0.90", "must begin with http"},
                {"http://10.0.0.90:8790/v1/ask", "ends after its port"},
                {"http://10.0.0.90:8790?x=1", "ends after its port"},
                {"http://user:secret@10.0.0.90:8790", "needs a host"},
                {"http://:8790", "needs a host"},
                {"http://10.0.0.90:99999", "port"},
                {"http://exa mple", "not a web address"},
        }) {
            IllegalArgumentException error = assertThrows(
                    refused[0], IllegalArgumentException.class, () -> AssistantAddress.normalize(refused[0]));
            assertTrue(refused[0] + ": " + error.getMessage(), error.getMessage().contains(refused[1]));
        }
        assertThrows(
                IllegalArgumentException.class,
                () -> AssistantAddress.normalize("http://" + new String(new char[220]).replace('\0', 'a')));
    }
}
