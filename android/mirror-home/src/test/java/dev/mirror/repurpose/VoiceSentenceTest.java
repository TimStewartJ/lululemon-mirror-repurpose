package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class VoiceSentenceTest {
    @Test
    public void theNamesOwnConfidenceIsKeptApartFromTheRest() throws Exception {
        VoiceSentence sentence = VoiceSentence.parse(
                "{\"result\": ["
                        + "{\"word\": \"mirror\", \"conf\": 0.97, \"start\": 1.2, \"end\": 1.6},"
                        + "{\"word\": \"what\", \"conf\": 0.41, \"start\": 1.7, \"end\": 1.9},"
                        + "{\"word\": \"[unk]\", \"conf\": 0.2, \"start\": 1.9, \"end\": 3.4}],"
                        + "\"text\": \"mirror what [unk]\"}");

        org.junit.Assert.assertEquals(0.97, sentence.nameConfidence, 1e-9);
        org.junit.Assert.assertEquals(0.2, sentence.lowestConfidence, 1e-9);
        org.junit.Assert.assertEquals(1200, sentence.startMs);
        org.junit.Assert.assertEquals(3400, sentence.endMs);
    }

    @Test
    public void aNameSaidTwiceIsAsSureAsItsWeakerHearing() throws Exception {
        VoiceSentence sentence = VoiceSentence.parse(
                "{\"result\": ["
                        + "{\"word\": \"mirror\", \"conf\": 0.9, \"start\": 0, \"end\": 0.4},"
                        + "{\"word\": \"mirror\", \"conf\": 0.7, \"start\": 0.5, \"end\": 0.9},"
                        + "{\"word\": \"brighter\", \"conf\": 1, \"start\": 1, \"end\": 1.5}],"
                        + "\"text\": \"mirror mirror brighter\"}");

        org.junit.Assert.assertEquals(0.7, sentence.nameConfidence, 1e-9);
    }

    @Test
    public void aSentenceThatDoesNotBeginWithTheNameHasNoConfidenceInIt() throws Exception {
        VoiceSentence sentence = VoiceSentence.parse(
                "{\"result\": ["
                        + "{\"word\": \"the\", \"conf\": 1, \"start\": 0, \"end\": 0.2},"
                        + "{\"word\": \"mirror\", \"conf\": 1, \"start\": 0.2, \"end\": 0.6}],"
                        + "\"text\": \"the mirror\"}");

        org.junit.Assert.assertTrue(Double.isNaN(sentence.nameConfidence));
    }

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
