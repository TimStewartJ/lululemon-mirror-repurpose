package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;

import org.junit.Test;

/** What the interpreter makes of speech once there is an assistant to pass requests on to. */
public final class VoiceInterpreterAssistantTest {
    private final VoiceInterpreter interpreter = new VoiceInterpreter();

    private VoiceInterpreter.Outcome heard(String text, double lowest, double name, long startMs) {
        return interpreter.heard(text, lowest, name, startMs, startMs + 1500, true);
    }

    @Test
    public void whatFollowsTheNameAndIsNoCommandIsARequest() {
        // The recogniser knows few of a request's words and is sure of fewer.
        VoiceInterpreter.Outcome outcome = heard("mirror [unk] me to take out the [unk] at seven", 0.31, 1.0, 0);

        assertEquals(VoiceInterpreter.Kind.ASK, outcome.kind);
        assertEquals(VoiceInterpreter.BY_NAME, outcome.addressed);
        assertNull(outcome.command);
    }

    @Test
    public void theMirrorsOwnCommandsStayItsOwn() {
        VoiceInterpreter.Outcome outcome = heard("mirror go to sleep", 1.0, 1.0, 0);

        assertEquals(VoiceInterpreter.Kind.COMMAND, outcome.kind);
        assertEquals(VoiceCommands.Command.SLEEP, outcome.command);
    }

    @Test
    public void aNameTheRecogniserDoubtsAddressesNobody() {
        assertEquals(VoiceInterpreter.Kind.OTHER, heard("mirror what is the [unk] like today", 0.4, 0.62, 0).kind);
        // Nor does it open the wait for a command.
        assertFalse(interpreter.windowOpen(1600));
    }

    @Test
    public void talkWithoutTheNameIsNotPassedOn() {
        assertEquals(VoiceInterpreter.Kind.OTHER, heard("what is the [unk] like today", 0.4, Double.NaN, 0).kind);
        assertEquals(VoiceInterpreter.Kind.OTHER, heard("the mirror in the [unk] needs [unk]", 0.4, Double.NaN, 5000).kind);
    }

    @Test
    public void afterTheNameAloneTheNextSpeechIsARequest() {
        assertEquals(VoiceInterpreter.Kind.WAKE, interpreter.heard("mirror", 1.0, 1.0, 0, 700, true).kind);

        VoiceInterpreter.Outcome outcome = heard("show me my [unk]", 0.5, Double.NaN, 2000);

        assertEquals(VoiceInterpreter.Kind.ASK, outcome.kind);
        assertEquals(VoiceInterpreter.AFTER_NAME, outcome.addressed);
        // One name is good for one request.
        assertEquals(VoiceInterpreter.Kind.OTHER, heard("and the [unk] too", 0.5, Double.NaN, 4000).kind);
    }

    @Test
    public void aCommandAfterTheNameAloneIsStillCarriedOutHere() {
        interpreter.heard("mirror", 1.0, 1.0, 0, 700, true);

        assertEquals(VoiceInterpreter.Kind.COMMAND, heard("go to sleep", 1.0, Double.NaN, 2000).kind);
    }

    @Test
    public void aCommandInDoubtIsLeftToTheAssistantWhenTheNameIsSure() {
        // Without an assistant this is dropped as unsure.
        VoiceInterpreter.Outcome outcome = heard("mirror dimmer", 0.55, 0.97, 0);

        assertEquals(VoiceInterpreter.Kind.ASK, outcome.kind);
        assertEquals(VoiceInterpreter.BY_NAME, outcome.addressed);
        assertEquals(VoiceInterpreter.Kind.UNSURE, heard("mirror dimmer", 0.55, 0.6, 9000).kind);
    }

    @Test
    public void whenTheAssistantAskedTheNextSpeechIsTheAnswer() {
        interpreter.awaitAnswer(10_000);

        VoiceInterpreter.Outcome outcome = heard("at seven in the [unk]", 0.4, Double.NaN, 14_000);

        assertEquals(VoiceInterpreter.Kind.ASK, outcome.kind);
        assertEquals(VoiceInterpreter.ANSWER, outcome.addressed);
        // The answer was given; talk after it is talk.
        assertEquals(VoiceInterpreter.Kind.OTHER, heard("thank you", 0.9, Double.NaN, 17_000).kind);
    }

    @Test
    public void anAnswerThatComesTooLateIsTalk() {
        interpreter.awaitAnswer(10_000);

        assertEquals(
                VoiceInterpreter.Kind.OTHER,
                heard("at seven", 0.9, Double.NaN, 10_001 + VoiceInterpreter.ANSWER_MS).kind);
    }

    @Test
    public void aCommandEndsTheWaitForAnAnswer() {
        interpreter.awaitAnswer(10_000);
        assertEquals(VoiceInterpreter.Kind.COMMAND, heard("mirror go to sleep", 1.0, 1.0, 12_000).kind);

        assertEquals(VoiceInterpreter.Kind.OTHER, heard("at seven", 0.9, Double.NaN, 14_000).kind);
    }

    @Test
    public void withoutAnAssistantNothingIsPassedOnAndAnAnswerIsNotAwaited() {
        interpreter.awaitAnswer(10_000);

        assertEquals(
                VoiceInterpreter.Kind.OTHER,
                interpreter.heard("at seven", 0.9, Double.NaN, 12_000, 13_000, false).kind);
        assertEquals(
                VoiceInterpreter.Kind.NOT_UNDERSTOOD,
                interpreter.heard("mirror [unk]", 1.0, 1.0, 20_000, 21_000, false).kind);
    }

    @Test
    public void startingAfreshForgetsAQuestion() {
        interpreter.awaitAnswer(10_000);
        interpreter.reset();

        assertEquals(VoiceInterpreter.Kind.OTHER, heard("at seven", 0.9, Double.NaN, 12_000).kind);
    }
}
