package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.junit.Test;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

public final class VoiceCommandsTest {
    @Test
    public void eachWordingStandsForOneCommand() {
        assertEquals(VoiceCommands.Command.SLEEP, VoiceCommands.forWording("go to sleep"));
        assertEquals(VoiceCommands.Command.GOOD_NIGHT, VoiceCommands.forWording("  Good   NIGHT "));
        assertEquals(VoiceCommands.Command.GOOD_MORNING, VoiceCommands.forWording("good morning"));
        assertEquals(VoiceCommands.Command.HOME, VoiceCommands.forWording("I'm home"));
        assertEquals(VoiceCommands.Command.HOME, VoiceCommands.forWording("i'm back"));
        assertEquals(VoiceCommands.Command.WAKE, VoiceCommands.forWording("wake up"));
        assertEquals(VoiceCommands.Command.DIMMER, VoiceCommands.forWording("brightness down"));
        assertEquals(VoiceCommands.Command.NEXT_VIDEO, VoiceCommands.forWording("change the video"));
        assertNull(VoiceCommands.forWording("mirror go to sleep"));
        assertNull(VoiceCommands.forWording("go to"));
        assertNull(VoiceCommands.forWording(""));
        assertNull(VoiceCommands.forWording(null));
    }

    @Test
    public void theRecogniserIsToldEveryWayACommandCanArrive() throws Exception {
        JSONArray grammar = new JSONArray(VoiceCommands.grammar());
        Set<String> sentences = new HashSet<>();
        for (int index = 0; index < grammar.length(); index++) {
            assertTrue("listed twice: " + grammar.getString(index), sentences.add(grammar.getString(index)));
        }
        int wordings = 0;
        for (VoiceCommands.Command command : VoiceCommands.Command.values()) {
            for (String wording : command.wordings()) {
                wordings++;
                assertTrue(sentences.contains("mirror " + wording));
                // After a pause the command arrives as a sentence of its own.
                assertTrue(sentences.contains(wording));
            }
        }
        assertTrue(sentences.contains("mirror"));
        assertTrue(sentences.contains("[unk]"));
        assertTrue(sentences.contains("because"));
        assertEquals(2 * wordings + 2 + VoiceCommands.otherWords().size(), grammar.length());
    }

    @Test
    public void theWordsForTalkAreNoPartOfAnyCommandAndSoundLikeNone() {
        List<String> others = VoiceCommands.otherWords();
        assertTrue("only " + others.size(), others.size() > 300);
        assertEquals(new HashSet<>(others).size(), others.size());
        for (String word : others) {
            // Contractions are single words to a speech model, apostrophe and all.
            assertTrue(word, word.matches("[a-z]+('[a-z]+)?"));
            assertTrue(word + " is a command's word", !VoiceCommands.words().contains(word));
        }
        // Each of these cost commands when it was on the list.
        for (String near : new String[]{"nearer", "mere", "error", "dinner", "swimmer", "writer", "lighter", "awake", "asleep"}) {
            assertTrue(near, !others.contains(near));
        }
    }

    @Test
    public void whatIsKeptOfASentenceHoldsOnlyTheCommandsWords() {
        assertEquals("mirror go to sleep", VoiceCommands.withoutOtherWords("mirror go to sleep"));
        assertEquals("mirror [unk]", VoiceCommands.withoutOtherWords("mirror what time is it"));
        assertEquals("mirror [unk] change [unk] the video [unk]",
                VoiceCommands.withoutOtherWords("Mirror  please change my the video now then"));
        assertEquals("mirror [unk]", VoiceCommands.withoutOtherWords("mirror [unk] because [unk]"));
        assertEquals("", VoiceCommands.withoutOtherWords(""));
    }

    @Test
    public void wordingsArePlainLowerCaseWords() {
        // A speech model only knows lower-case words; one it lacks is silently never heard.
        List<String> words = VoiceCommands.words();
        assertEquals("mirror", words.get(0));
        for (String word : words) {
            assertTrue(word, word.matches("[a-z]+('[a-z]+)?"));
        }
        assertEquals(new HashSet<>(words).size(), words.size());
    }

    @Test
    public void aGreetingIsItsOwnCommand() {
        // "Good night" once stood for sleep alone; with an assistant it is answered first.
        int greetings = 0;
        for (VoiceCommands.Command command : VoiceCommands.Command.values()) {
            greetings += command.greets() ? 1 : 0;
        }
        assertEquals(5, greetings);
        assertTrue(VoiceCommands.Command.GOOD_NIGHT.greets());
        assertTrue(VoiceCommands.Command.HOME.greets());
        assertFalse(VoiceCommands.Command.SLEEP.greets());
        assertFalse(VoiceCommands.Command.WAKE.greets());
        assertEquals("good-morning", VoiceCommands.Command.GOOD_MORNING.id);
    }

    @Test
    public void aWordOfACommandIsNotAlsoSomethingElse() {
        // "home" and "back" were words to take talk for until a greeting needed them.
        List<String> others = VoiceCommands.otherWords();
        for (String word : VoiceCommands.words()) {
            assertFalse(word, others.contains(word));
        }
        assertTrue(others.contains("house"));
    }

    @Test
    public void wordingsThatWereTakenForEachOtherStayOut() {
        // In noise "turn off" was heard as "turn on", which would wake a Mirror told to sleep.
        for (String wording : new String[]{"turn on", "turn off", "screen on", "screen off"}) {
            assertNull(wording, VoiceCommands.forWording(wording));
        }
    }

    @Test
    public void theControlsAreToldHowToSayEachCommand() throws Exception {
        JSONArray described = VoiceCommands.describe();
        assertEquals(VoiceCommands.Command.values().length, described.length());
        assertEquals("sleep", described.getJSONObject(0).getString("id"));
        assertEquals("mirror go to sleep", described.getJSONObject(0).getJSONArray("say").getString(0));
        assertEquals("Next video", described.getJSONObject(4).getString("caption"));
    }
}
