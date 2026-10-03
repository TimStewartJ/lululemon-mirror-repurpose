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
    public void anAnswerWithSeveralPartsBringsThemAsRows() throws Exception {
        AssistantReply reply = AssistantReply.parse("{\"heard\":\"\",\"reply\":\"Good morning\",\"seconds\":18,"
                + "\"details\":[{\"label\":\"Weather\",\"text\":\"72\u00b0 and clear\"},"
                + "{\"label\":\"Missed\",\"text\":\"Start dishwasher\"}]}");
        assertEquals("Good morning", reply.reply);
        assertEquals(2, reply.details.size());
        assertEquals("Weather", reply.details.get(0).label);
        assertEquals("Start dishwasher", reply.details.get(1).text);
        assertEquals(18_000L, reply.millis());
    }

    @Test
    public void anAnswerWithoutRowsHasNone() throws Exception {
        AssistantReply reply = AssistantReply.parse("{\"reply\":\"Done.\"}");
        assertTrue(reply.details.isEmpty());
        assertEquals(3_500L, reply.millis());
        // Rows under nothing are rows of nothing.
        assertTrue(AssistantReply.parse("{\"ignored\":true,\"details\":[{\"label\":\"a\",\"text\":\"b\"}]}")
                .details.isEmpty());
    }

    @Test
    public void aTimeToShowThatIsNoTimeIsLeftToTheLength() throws Exception {
        for (String seconds : new String[]{"0", "1", "31", "-4", "\"long\""}) {
            AssistantReply reply = AssistantReply.parse("{\"reply\":\"Done.\",\"seconds\":" + seconds + "}");
            assertEquals(seconds, 3_500L, reply.millis());
        }
    }

    @Test
    public void rowsAreGivenTimeToBeReadToo() {
        java.util.List<GlassCaption.Row> rows = new java.util.ArrayList<>();
        assertEquals(3_500L, AssistantReply.showMillis("Done.", rows));
        rows.add(new GlassCaption.Row("Weather", new String(new char[40]).replace('\0', 'a')));
        assertEquals(3_500L + 1_200L + 55L * 40, AssistantReply.showMillis("Done.", rows));
        for (int more = 0; more < 4; more++) {
            rows.add(new GlassCaption.Row("To do", new String(new char[90]).replace('\0', 'a')));
        }
        assertEquals(24_000L, AssistantReply.showMillis("Done.", rows));
    }

    @Test
    public void aLineStaysLongEnoughToBeReadAndNoLonger() {
        assertEquals(3_500L, AssistantReply.showMillis("Done."));
        assertEquals(2_000L + 70L * 60, AssistantReply.showMillis(new String(new char[60]).replace('\0', 'a')));
        assertEquals(14_000L, AssistantReply.showMillis(new String(new char[200]).replace('\0', 'a')));
    }
}
