package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import org.json.JSONException;
import org.junit.Test;

public final class AssistantReplyTest {
    @Test
    public void anAnswerIsReadWithWhatWasHeardAndDone() throws Exception {
        AssistantReply reply = AssistantReply.parse(
                "{\"id\":\"u1\",\"heard\":\"remind me to take out the trash at seven\","
                        + "\"reply\":\"I'll remind you tomorrow at 7.\",\"ignored\":false,\"reason\":\"\","
                        + "\"listen\":false,\"acted\":[\"board_add\",\"arrange_widgets\"],"
                        + "\"ms\":{\"stt\":1480,\"agent\":3320,\"total\":4830}}");

        assertEquals("remind me to take out the trash at seven", reply.heard);
        assertEquals("I'll remind you tomorrow at 7.", reply.reply);
        assertEquals("board_add, arrange_widgets", reply.acted);
        assertFalse(reply.ignored);
        assertFalse(reply.listen);
    }

    @Test
    public void talkThatWasNotMeantForTheMirrorShowsNothing() throws Exception {
        AssistantReply reply = AssistantReply.parse(
                "{\"heard\":\"the mirror in the hall needs cleaning\",\"reply\":\"Thanks!\","
                        + "\"ignored\":true,\"reason\":\"not-addressed\",\"listen\":true}");

        assertTrue(reply.ignored);
        assertEquals("", reply.reply);
        assertEquals("not-addressed", reply.reason);
        assertFalse(reply.listen);
    }

    @Test
    public void aQuestionAsksForAnAnswer() throws Exception {
        AssistantReply reply = AssistantReply.parse("{\"reply\":\"At what time?\",\"listen\":true}");

        assertTrue(reply.listen);
        assertEquals("", reply.heard);
        assertEquals("", reply.acted);
    }

    @Test
    public void nothingToShowAsksForNoAnswerEither() throws Exception {
        assertFalse(AssistantReply.parse("{\"reply\":\"  \",\"listen\":true}").listen);
    }

    @Test
    public void aReplyIsOneLineOfBoundedLength() throws Exception {
        StringBuilder words = new StringBuilder();
        for (int index = 0; index < 80; index++) {
            words.append("word").append(index).append(index % 7 == 0 ? "\\n" : " ");
        }

        String shown = AssistantReply.parse("{\"reply\":\"" + words + "\"}").reply;

        assertFalse(shown.contains("\n"));
        assertTrue(shown.length() <= AssistantReply.MAX_TEXT + 1);
        assertTrue(shown, shown.endsWith("\u2026"));
        // Cut between words, not inside one.
        assertTrue(shown, shown.matches(".*word\\d+\u2026$"));
    }

    @Test
    public void whatIsNoAnswerAtAllIsRefused() {
        assertThrows(JSONException.class, () -> AssistantReply.parse("<html>502 Bad Gateway</html>"));
    }

    @Test
    public void aLineStaysLongEnoughToBeReadAndNoLonger() {
        assertEquals(3_500L, AssistantReply.showMillis("Done."));
        assertEquals(2_000L + 70L * 60, AssistantReply.showMillis(new String(new char[60]).replace('\0', 'a')));
        assertEquals(14_000L, AssistantReply.showMillis(new String(new char[200]).replace('\0', 'a')));
    }
}
