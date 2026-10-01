package dev.mirror.repurpose.voicelab;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class SpeechGateTest {
    private static final double ROOM = 100;
    private static final double VOICE = 2000;

    private static SpeechGate settled() {
        SpeechGate gate = new SpeechGate();
        for (int frame = 0; frame < 200; frame++) {
            assertFalse(gate.accept(ROOM));
        }
        return gate;
    }

    @Test
    public void aSteadyRoomNeverOpensTheGate() {
        SpeechGate gate = settled();
        assertFalse(gate.isOpen());
        assertTrue(gate.noiseFloor() > ROOM * 0.9 && gate.noiseFloor() < ROOM * 1.1);
    }

    @Test
    public void aVoiceOpensItAtOnce() {
        SpeechGate gate = settled();
        assertTrue(gate.accept(VOICE));
    }

    @Test
    public void aShortPauseDoesNotEndTheSentence() {
        SpeechGate gate = settled();
        gate.accept(VOICE);
        for (int frame = 0; frame < SpeechGate.HANG_FRAMES - 1; frame++) {
            assertTrue(gate.accept(ROOM));
        }
        assertTrue(gate.accept(VOICE));
    }

    @Test
    public void aLongPauseEndsIt() {
        SpeechGate gate = settled();
        gate.accept(VOICE);
        for (int frame = 0; frame < SpeechGate.HANG_FRAMES - 1; frame++) {
            gate.accept(ROOM);
        }
        assertFalse(gate.accept(ROOM));
    }

    @Test
    public void soundOnlySlightlyAboveTheRoomIsNotSpeech() {
        SpeechGate gate = settled();
        assertFalse(gate.accept(ROOM * 2));
    }

    @Test
    public void aSilentMicrophoneDoesNotMakeEveryWhisperSpeech() {
        SpeechGate gate = new SpeechGate();
        for (int frame = 0; frame < 500; frame++) {
            gate.accept(0);
        }
        assertFalse(gate.accept(SpeechGate.MINIMUM_LEVEL - 1));
        assertTrue(gate.accept(SpeechGate.MINIMUM_LEVEL * 4));
    }

    @Test
    public void aRoomThatBecomesLouderIsLearnedAndTheGateClosesAgain() {
        SpeechGate gate = settled();
        boolean open = true;
        // A fan switched on: far louder than before, and it never pauses.
        for (int frame = 0; frame < 2000 && open; frame++) {
            open = gate.accept(ROOM * 8);
        }
        assertFalse(open);
        assertTrue(gate.accept(ROOM * 8 * 4));
    }

    @Test
    public void aSentenceOfAFewSecondsStaysOpenThroughout() {
        SpeechGate gate = settled();
        for (int frame = 0; frame < 150; frame++) {
            assertTrue(gate.accept(VOICE));
        }
    }
}
