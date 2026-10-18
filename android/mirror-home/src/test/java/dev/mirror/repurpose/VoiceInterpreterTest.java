package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class VoiceInterpreterTest {
    private final VoiceInterpreter interpreter = new VoiceInterpreter("mirror");

    @Test
    public void aMultiwordPhraseMustBeCompleteAndAtTheStart() {
        interpreter.setWakePhrase("hey mochi");
        for (String text : new String[]{"mochi go to sleep", "hey go to sleep",
                "mirror go to sleep", "the hey mochi go to sleep", "hey mochis go to sleep"}) {
            assertEquals(text, VoiceInterpreter.Kind.OTHER, heard(text, 0).kind);
        }
        assertEquals(VoiceCommands.Command.SLEEP, heard("hey mochi go to sleep", 0).command);
        assertEquals(VoiceCommands.Command.WAKE, heard("hey mochi hey mochi wake up", 2000).command);
        assertEquals(VoiceInterpreter.Kind.WAKE, heard("hey mochi", 5000).kind);
        assertEquals(VoiceCommands.Command.SLEEP, heard("go to sleep", 7000).command);
    }

    @Test
    public void changingThePhraseClearsTheOldWakeAndAnswerWindows() {
        heard("mirror", 0);
        interpreter.awaitAnswer(0);
        interpreter.setWakePhrase("hello friend");
        assertFalse(interpreter.windowOpen(1000));
        assertEquals(VoiceInterpreter.Kind.OTHER,
                interpreter.heard("at seven", 1, 1, 1000, 2000, true).kind);
        assertEquals(VoiceInterpreter.Kind.OTHER, heard("mirror brighter", 2000).kind);
        assertEquals(VoiceCommands.Command.BRIGHTER, heard("hello friend brighter", 3000).command);
    }

    @Test
    public void aCustomPhraseMayOverlapTheStartOfACommand() {
        interpreter.setWakePhrase("wake");
        assertEquals(VoiceCommands.Command.WAKE, heard("wake wake up", 0).command);
        interpreter.setWakePhrase("good");
        assertEquals(VoiceCommands.Command.GOOD_MORNING, heard("good good morning", 1000).command);
        interpreter.setWakePhrase("go to sleep");
        assertEquals(VoiceCommands.Command.SLEEP, heard("go to sleep go to sleep", 2000).command);
    }

    private VoiceInterpreter.Outcome heard(String text, long startMs) {
        return interpreter.heard(text, 1.0, startMs, startMs + 1000);
    }

    @Test
    public void aCommandAfterTheWakeWordIsCarriedOut() {
        VoiceInterpreter.Outcome outcome = heard("mirror go to sleep", 0);
        assertEquals(VoiceInterpreter.Kind.COMMAND, outcome.kind);
        assertEquals(VoiceCommands.Command.SLEEP, outcome.command);
        assertEquals(VoiceCommands.Command.DIMMER, heard("Mirror  brightness down", 5000).command);
    }

    @Test
    public void aCommandWithoutTheWakeWordIsJustTalk() {
        assertEquals(VoiceInterpreter.Kind.OTHER, heard("go to sleep", 0).kind);
        assertEquals(VoiceInterpreter.Kind.OTHER, heard("brighter", 2000).kind);
    }

    @Test
    public void partsOfCommandsAndMixturesAreJustTalk() {
        // The recogniser puts these out for other speech.
        for (String text : new String[]{
                "wake up [unk]", "[unk] go to sleep", "mirror go", "[unk]", "", "go to",
                "mirror go to sleep [unk]", "[unk] mirror go to sleep"}) {
            VoiceInterpreter.Kind kind = heard(text, 0).kind;
            assertTrue(text, kind == VoiceInterpreter.Kind.OTHER || kind == VoiceInterpreter.Kind.NOT_UNDERSTOOD);
            interpreter.reset();
        }
    }

    @Test
    public void aPauseAfterTheWakeWordStillMakesACommand() {
        // On the Mirror: "mirror", a pause, "go to sleep" arrived as two sentences.
        assertEquals(VoiceInterpreter.Kind.WAKE, interpreter.heard("mirror", 1.0, 0, 700).kind);
        assertTrue(interpreter.windowOpen(2700));
        VoiceInterpreter.Outcome outcome = interpreter.heard("go to sleep", 1.0, 2700, 3400);
        assertEquals(VoiceInterpreter.Kind.COMMAND, outcome.kind);
        assertEquals(VoiceCommands.Command.SLEEP, outcome.command);
        // One wake word is good for one command.
        assertFalse(interpreter.windowOpen(3500));
        assertEquals(VoiceInterpreter.Kind.OTHER, heard("wake up", 4000).kind);
    }

    @Test
    public void theWakeWordWearsOff() {
        interpreter.heard("mirror", 1.0, 0, 700);
        assertTrue(interpreter.windowOpen(700 + VoiceInterpreter.WINDOW_MS));
        assertFalse(interpreter.windowOpen(701 + VoiceInterpreter.WINDOW_MS));
        assertEquals(
                VoiceInterpreter.Kind.OTHER,
                interpreter.heard("go to sleep", 1.0, 701 + VoiceInterpreter.WINDOW_MS, 9000).kind);
    }

    @Test
    public void otherTalkWhileWaitingDoesNotUseUpTheWakeWord() {
        interpreter.heard("mirror", 1.0, 0, 700);
        assertEquals(VoiceInterpreter.Kind.OTHER, heard("[unk]", 1500).kind);
        assertEquals(VoiceInterpreter.Kind.COMMAND, heard("brighter", 3000).kind);
    }

    @Test
    public void theWakeWordSaidTwiceIsStillOne() {
        // On the Mirror: "mirror, mirror go to sleep" was heard exactly so.
        VoiceInterpreter.Outcome outcome = heard("mirror mirror go to sleep", 0);
        assertEquals(VoiceInterpreter.Kind.COMMAND, outcome.kind);
        assertEquals(VoiceCommands.Command.SLEEP, outcome.command);
        assertEquals(VoiceInterpreter.Kind.WAKE, heard("mirror mirror", 5000).kind);
    }

    @Test
    public void somethingElseSaidToTheMirrorCanBeSaidAgain() {
        VoiceInterpreter.Outcome outcome = heard("mirror [unk]", 0);
        assertEquals(VoiceInterpreter.Kind.NOT_UNDERSTOOD, outcome.kind);
        assertNull(outcome.command);
        assertEquals(VoiceInterpreter.Kind.COMMAND, heard("brightness up", 2500).kind);
    }

    @Test
    public void whatTheRecogniserIsNotSureOfIsNotCarriedOut() {
        VoiceInterpreter.Outcome outcome = interpreter.heard("mirror dimmer", 0.31, 0, 900);
        assertEquals(VoiceInterpreter.Kind.UNSURE, outcome.kind);
        assertEquals(VoiceCommands.Command.DIMMER, outcome.command);
        assertEquals(VoiceInterpreter.Kind.UNSURE, interpreter.heard("mirror", 0.4, 2000, 2500).kind);
        assertFalse(interpreter.windowOpen(2600));
        assertEquals(VoiceInterpreter.Kind.OTHER, interpreter.heard("mirror [unk]", 0.4, 3000, 3500).kind);
        // Exactly at the threshold counts, and a recogniser that names no confidence is taken at its word.
        assertEquals(VoiceInterpreter.Kind.COMMAND,
                interpreter.heard("mirror dimmer", VoiceInterpreter.MIN_CONFIDENCE, 5000, 5900).kind);
        assertEquals(VoiceInterpreter.Kind.COMMAND, interpreter.heard("mirror dimmer", Double.NaN, 7000, 7900).kind);
    }

    @Test
    public void startingAfreshForgetsTheWakeWord() {
        interpreter.heard("mirror", 1.0, 0, 700);
        interpreter.reset();
        assertFalse(interpreter.windowOpen(800));
        assertEquals(VoiceInterpreter.Kind.OTHER, heard("go to sleep", 1000).kind);
    }
}
