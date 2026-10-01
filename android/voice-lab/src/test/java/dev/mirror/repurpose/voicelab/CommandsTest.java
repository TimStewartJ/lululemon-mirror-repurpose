package dev.mirror.repurpose.voicelab;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

import java.util.Arrays;

public final class CommandsTest {
    private static final String FILE = String.join("\n",
            "# what to say | what to send to Mirror Home",
            "",
            "Mirror  go to SLEEP | POST /api/v1/automation/sleep",
            "mirror brighter | POST /api/v1/control/brightness {\"value\": 220}",
            "mirror hello",
            "");

    private static JSONObject heard(String text, double... confidences) throws Exception {
        JSONArray words = new JSONArray();
        String[] parts = text.split(" ");
        for (int index = 0; index < confidences.length; index++) {
            words.put(new JSONObject().put("word", parts[index]).put("conf", confidences[index]));
        }
        JSONObject result = new JSONObject().put("text", text);
        return confidences.length == 0 ? result : result.put("result", words);
    }

    @Test
    public void phrasesAreReadInOrderWithoutCommentsAndInOneSpelling() {
        Commands commands = Commands.parse(FILE);
        assertEquals(
                Arrays.asList("mirror go to sleep", "mirror brighter", "mirror hello"),
                commands.phrases());
        assertTrue(commands.knows("  MIRROR   go to sleep "));
        assertFalse(commands.knows("mirror go"));
    }

    @Test
    public void eachPhraseCarriesItsRequest() {
        Commands commands = Commands.parse(FILE);
        Commands.Request sleep = commands.requestFor("mirror go to sleep");
        assertEquals("POST", sleep.method);
        assertEquals("/api/v1/automation/sleep", sleep.path);
        assertEquals("", sleep.body);
        assertEquals("{\"value\": 220}", commands.requestFor("Mirror brighter").body);
        // A phrase may be recognised without doing anything.
        assertNull(commands.requestFor("mirror hello"));
        assertNull(commands.requestFor("mirror dance"));
    }

    @Test
    public void theRecogniserIsToldToExpectThePhrasesAndAnythingElse() throws Exception {
        JSONArray grammar = new JSONArray(Commands.parse(FILE).grammar());
        assertEquals(4, grammar.length());
        assertEquals("mirror go to sleep", grammar.getString(0));
        assertEquals("[unk]", grammar.getString(3));
    }

    @Test
    public void aRequestWithoutAPathIsRefused() {
        assertThrows(IllegalArgumentException.class, () -> Commands.parse("mirror stop | POST"));
    }

    @Test
    public void aConfidentKnownPhraseIsACommand() throws Exception {
        Commands commands = Commands.parse(FILE);
        assertEquals("mirror brighter", commands.accepted(heard("mirror brighter", 1.0, 0.9), 0.5));
        // A recogniser that reports no words is taken at its word.
        assertEquals("mirror brighter", commands.accepted(heard("Mirror Brighter"), 0.5));
    }

    @Test
    public void otherSpeechFittedToAPhraseWithLittleConfidenceIsTurnedAway() throws Exception {
        Commands commands = Commands.parse(FILE);
        assertNull(commands.accepted(heard("mirror brighter", 1.0, 0.31), 0.5));
        assertEquals(0.31, Commands.lowestConfidence(heard("mirror brighter", 1.0, 0.31)), 0.0);
        assertTrue(Double.isNaN(Commands.lowestConfidence(heard("mirror brighter"))));
    }

    @Test
    public void unknownSpeechAndSilenceAreNotCommands() throws Exception {
        Commands commands = Commands.parse(FILE);
        assertNull(commands.accepted(heard("[unk]"), 0.5));
        assertNull(commands.accepted(heard("mirror [unk]"), 0.5));
        assertNull(commands.accepted(heard("mirror go"), 0.5));
        assertNull(commands.accepted(heard(""), 0.5));
        assertNull(commands.accepted(new JSONObject(), 0.5));
    }
}
