package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class PairingGateTest {
    private static final long SECOND = 1000L;
    private static final long MINUTE = 60 * SECOND;
    private static final long HOUR = 60 * MINUTE;

    @Test
    public void pairingIsClosedUntilACodeIsShown() {
        PairingGate gate = new PairingGate();

        assertEquals(PairingGate.State.CLOSED, gate.state(0L));
        assertEquals(PairingGate.State.CLOSED, gate.state(5 * HOUR));
        assertFalse(gate.onDisplay(5 * HOUR));

        gate.displayed(5 * HOUR);
        assertEquals(PairingGate.State.OPEN, gate.state(5 * HOUR));
        assertTrue(gate.onDisplay(5 * HOUR + PairingGate.DISPLAY_GRACE_MS - 1));
        assertEquals(
                PairingGate.State.CLOSED,
                gate.state(5 * HOUR + PairingGate.DISPLAY_GRACE_MS));
    }

    @Test
    public void aSurfaceThatKeepsShowingTheCodeKeepsPairingOpen() {
        PairingGate gate = new PairingGate();

        for (long now = 0; now <= 20 * MINUTE; now += 5 * SECOND) {
            gate.displayed(now);
            assertEquals(PairingGate.State.OPEN, gate.state(now + 4 * SECOND));
        }
    }

    @Test
    public void anOpenedWindowLastsUntilItsEndAndIsNotShortenedByARefresh() {
        PairingGate gate = new PairingGate();

        gate.displayedThrough(10 * MINUTE);
        gate.displayed(MINUTE);
        assertEquals(PairingGate.State.OPEN, gate.state(10 * MINUTE - 1));
        assertEquals(PairingGate.State.CLOSED, gate.state(10 * MINUTE));

        gate.displayedThrough(30 * MINUTE);
        gate.close();
        assertEquals(PairingGate.State.CLOSED, gate.state(11 * MINUTE));
    }

    @Test
    public void fiveWrongCodesLockPairingEvenWhileACodeIsShown() {
        PairingGate gate = new PairingGate();
        gate.displayedThrough(HOUR);

        for (int attempt = 1; attempt < PairingGate.MAX_FAILURES; attempt++) {
            gate.recordWrongCode(attempt);
            assertEquals(PairingGate.State.OPEN, gate.state(attempt));
        }
        gate.recordWrongCode(10L);

        assertEquals(PairingGate.State.LOCKED, gate.state(10L));
        assertEquals(PairingGate.FIRST_LOCKOUT_MS, gate.lockedForMillis(10L));
        assertEquals(
                PairingGate.State.LOCKED,
                gate.state(10L + PairingGate.FIRST_LOCKOUT_MS - 1));
        assertEquals(PairingGate.State.OPEN, gate.state(10L + PairingGate.FIRST_LOCKOUT_MS));
        assertEquals(0L, gate.lockedForMillis(10L + PairingGate.FIRST_LOCKOUT_MS));
    }

    @Test
    public void eachLockoutDoublesUpToAnHour() {
        assertEquals(30 * SECOND, PairingGate.lockoutMillis(0));
        assertEquals(MINUTE, PairingGate.lockoutMillis(1));
        assertEquals(2 * MINUTE, PairingGate.lockoutMillis(2));
        assertEquals(32 * MINUTE, PairingGate.lockoutMillis(6));
        assertEquals(HOUR, PairingGate.lockoutMillis(7));
        assertEquals(HOUR, PairingGate.lockoutMillis(40));

        PairingGate gate = new PairingGate();
        long now = 0L;
        long guesses = 0L;
        // A day of guessing as fast as the gate allows, with a code always shown.
        while (now < 24 * HOUR) {
            gate.displayed(now);
            if (gate.state(now) == PairingGate.State.OPEN) {
                gate.recordWrongCode(now);
                guesses++;
            } else {
                now += gate.lockedForMillis(now);
            }
        }

        assertEquals(guesses, gate.wrongCodes());
        assertTrue("guesses in a day: " + guesses, guesses <= 160);
    }

    @Test
    public void rotatingTheCodeDoesNotForgiveWrongGuesses() {
        PairingGate gate = new PairingGate();
        gate.displayedThrough(HOUR);
        for (int attempt = 0; attempt < PairingGate.MAX_FAILURES - 1; attempt++) {
            gate.recordWrongCode(attempt);
        }

        // Ten minutes later a new code is on display; one more miss still locks.
        gate.displayed(10 * MINUTE);
        gate.recordWrongCode(10 * MINUTE);

        assertEquals(PairingGate.State.LOCKED, gate.state(10 * MINUTE));
    }

    @Test
    public void aCorrectCodeOrAnOwnerWindowClearsThePenalty() {
        PairingGate gate = new PairingGate();
        gate.displayedThrough(HOUR);
        for (int attempt = 0; attempt < 2 * PairingGate.MAX_FAILURES; attempt++) {
            gate.recordWrongCode(attempt * MINUTE);
        }
        assertEquals(PairingGate.State.LOCKED, gate.state(9 * MINUTE + SECOND));

        gate.reset();

        assertEquals(PairingGate.State.OPEN, gate.state(9 * MINUTE + SECOND));
        for (int attempt = 0; attempt < PairingGate.MAX_FAILURES; attempt++) {
            gate.recordWrongCode(11 * MINUTE);
        }
        assertEquals(PairingGate.FIRST_LOCKOUT_MS, gate.lockedForMillis(11 * MINUTE));
    }

    @Test
    public void aQuietDayForgetsEarlierLockouts() {
        PairingGate gate = new PairingGate();
        for (int attempt = 0; attempt < PairingGate.MAX_FAILURES; attempt++) {
            gate.recordWrongCode(0L);
        }
        long later = PairingGate.QUIET_RESET_MS;
        gate.displayed(later);
        for (int attempt = 0; attempt < PairingGate.MAX_FAILURES; attempt++) {
            gate.recordWrongCode(later);
        }

        assertEquals(PairingGate.FIRST_LOCKOUT_MS, gate.lockedForMillis(later));

        // Without the quiet day the second lockout would have been longer.
        PairingGate busy = new PairingGate();
        for (int attempt = 0; attempt < PairingGate.MAX_FAILURES; attempt++) {
            busy.recordWrongCode(0L);
        }
        for (int attempt = 0; attempt < PairingGate.MAX_FAILURES; attempt++) {
            busy.recordWrongCode(HOUR);
        }
        assertEquals(2 * PairingGate.FIRST_LOCKOUT_MS, busy.lockedForMillis(HOUR));
    }

    @Test
    public void reportsWrongCodesForTheHealthReport() {
        PairingGate gate = new PairingGate();

        assertEquals(0L, gate.wrongCodes());
        assertEquals(-1L, gate.lastWrongCodeAt());

        gate.recordWrongCode(0L);
        gate.recordWrongCode(7 * SECOND);

        assertEquals(2L, gate.wrongCodes());
        assertEquals(7 * SECOND, gate.lastWrongCodeAt());
    }
}
