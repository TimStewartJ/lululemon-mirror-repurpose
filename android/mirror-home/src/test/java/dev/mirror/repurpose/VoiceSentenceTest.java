package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class VoiceSentenceTest {
    @Test
    public void aSentenceCarriesItsLeastCertainWordAndItsTimes() throws Exception {
        VoiceSentence sentence = VoiceSentence.parse(
                "{\"result\": ["
                        + "{\"conf\": 1.0, \"end\": 6.42, \"start\": 5.97, \"word\": \"mirror\"},"
                        + "{\"conf\": 0.62, \"end\": 6.69, \"start\": 6.42, \"word\": \"wake\"},"
                        + "{\"conf\": 0.91, \"end\": 6.96, \"start\": 6.69, \"word\": \"up\"}],"
                        + " \"text\": \"mirror wake up\"}");
        assertEquals("mirror wake up", sentence.text);
        assertEquals(0.62, sentence.lowestConfidence, 0.0);
        assertEquals(5970, sentence.startMs);
        assertEquals(6960, sentence.endMs);
    }

    @Test
    public void silenceIsNoSentence() throws Exception {
        assertNull(VoiceSentence.parse("{\"text\": \"\"}"));
        assertNull(VoiceSentence.parse("{}"));
        assertNull(VoiceSentence.parse("{\"text\": \"   \"}"));
    }

    @Test
    public void aSentenceWithoutWordTimesIsStillASentence() throws Exception {
        VoiceSentence sentence = VoiceSentence.parse("{\"text\": \"Mirror  Brighter\"}");
        assertEquals("mirror brighter", sentence.text);
        assertTrue(Double.isNaN(sentence.lowestConfidence));
        assertEquals(0, sentence.startMs);
        assertEquals(0, sentence.endMs);
    }
}
