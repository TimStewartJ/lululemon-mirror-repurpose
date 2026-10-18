package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

public final class WakePhraseTest {
    @Test
    public void defaultsFollowTheMascotOrUseMirrorWithoutOne() {
        assertEquals("hey mirror", WakePhrase.defaultFor(null));
        assertEquals("hey mirror", WakePhrase.defaultFor(""));
        for (String name : new String[]{"Blink", "Wisp", "Mochi", "Lune"}) {
            assertEquals("hey " + name.toLowerCase(java.util.Locale.ROOT), WakePhrase.defaultFor(name));
        }
    }

    @Test
    public void phrasesAreNormalizedAndInvalidInputsAreRefused() {
        assertEquals("hey mochi", WakePhrase.normalize("  Hey \t Mochi  "));
        assertEquals("hello my friend", WakePhrase.normalize("Hello my friend"));
        assertEquals("let's talk", WakePhrase.normalize("Let's talk"));
        for (String bad : new String[]{"", "[unk]", "hey, mochi", "hey 123",
                "one two three four five", "a".repeat(65), "hey\n[unk]", "h\u00e9y"}) {
            assertThrows(bad, IllegalArgumentException.class, () -> WakePhrase.normalize(bad));
        }
    }
}
