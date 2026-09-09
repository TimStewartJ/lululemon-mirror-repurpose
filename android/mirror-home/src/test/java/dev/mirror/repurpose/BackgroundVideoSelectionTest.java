package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class BackgroundVideoSelectionTest {
    private static final String FIRST = repeat('a');
    private static final String SECOND = repeat('b');

    @Test
    public void activationRetainsPreviousSelection() {
        BackgroundVideoSelection selection =
                new BackgroundVideoSelection(FIRST, "").activate(SECOND);

        assertEquals(SECOND, selection.activeId);
        assertEquals(FIRST, selection.previousId);
        assertTrue(selection.canRollback());
    }

    @Test
    public void activatingCurrentVideoIsIdempotent() {
        BackgroundVideoSelection selection =
                new BackgroundVideoSelection(FIRST, SECOND).activate(FIRST);

        assertEquals(FIRST, selection.activeId);
        assertEquals(SECOND, selection.previousId);
    }

    @Test
    public void rollbackSwapsActiveAndPrevious() {
        BackgroundVideoSelection selection =
                new BackgroundVideoSelection(FIRST, SECOND).rollback();

        assertEquals(SECOND, selection.activeId);
        assertEquals(FIRST, selection.previousId);
    }

    @Test
    public void deletingPreviousClearsRollbackWithoutChangingActive() {
        BackgroundVideoSelection selection =
                new BackgroundVideoSelection(FIRST, SECOND).remove(SECOND);

        assertEquals(FIRST, selection.activeId);
        assertEquals("", selection.previousId);
        assertFalse(selection.canRollback());
    }

    @Test
    public void rejectsMalformedIdentifiers() {
        assertFalse(BackgroundVideoSelection.validId("../video"));
        assertFalse(BackgroundVideoSelection.validId(repeat('A')));
        assertFalse(BackgroundVideoSelection.validId(repeat('a') + "0"));
        assertTrue(BackgroundVideoSelection.validId(FIRST));
    }

    private static String repeat(char value) {
        StringBuilder result = new StringBuilder();
        for (int index = 0; index < 64; index++) {
            result.append(value);
        }
        return result.toString();
    }
}
