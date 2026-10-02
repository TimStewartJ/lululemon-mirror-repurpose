package dev.mirror.repurpose;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class VoiceStandDownTest {
    private static final long START = 1_000_000L;

    @Test
    public void nothingBeingInstalledLeavesVoiceAlone() {
        VoiceStandDown standDown = new VoiceStandDown();

        assertFalse(standDown.active(START));
    }

    @Test
    public void voiceStepsAsideFromTheMomentAnInstallationBegins() {
        VoiceStandDown standDown = new VoiceStandDown();

        standDown.began(7, START);

        assertTrue(standDown.active(START));
        assertTrue(standDown.active(START + VoiceStandDown.LIMIT_MS - 1));
    }

    @Test
    public void voiceReturnsAsSoonAsTheInstallationEnds() {
        VoiceStandDown standDown = new VoiceStandDown();
        standDown.began(7, START);

        standDown.ended(7);

        assertFalse(standDown.active(START + 1));
    }

    @Test
    public void anInstallationThatNeverReportsItsEndIsNotWaitedForForEver() {
        VoiceStandDown standDown = new VoiceStandDown();
        standDown.began(7, START);

        assertFalse(standDown.active(START + VoiceStandDown.LIMIT_MS));
    }

    @Test
    public void voiceWaitsForEveryInstallationUnderWay() {
        VoiceStandDown standDown = new VoiceStandDown();
        standDown.began(7, START);
        standDown.began(8, START + 1_000);

        standDown.ended(7);
        assertTrue(standDown.active(START + 2_000));

        standDown.ended(8);
        assertFalse(standDown.active(START + 3_000));
    }

    @Test
    public void anInstallationSeenAtWorkAgainIsWaitedForAfresh() {
        VoiceStandDown standDown = new VoiceStandDown();
        standDown.began(7, START);

        standDown.began(7, START + VoiceStandDown.LIMIT_MS - 1);

        assertTrue(standDown.active(START + VoiceStandDown.LIMIT_MS + 1_000));
    }

    @Test
    public void oneGivenUpOnDoesNotHoldUpTheNext() {
        VoiceStandDown standDown = new VoiceStandDown();
        standDown.began(7, START);
        long later = START + VoiceStandDown.LIMIT_MS + 60_000;

        standDown.began(8, later);
        standDown.ended(8);

        assertFalse(standDown.active(later + 1_000));
    }

    @Test
    public void theEndOfAnInstallationNobodySawBeginChangesNothing() {
        VoiceStandDown standDown = new VoiceStandDown();
        standDown.began(7, START);

        standDown.ended(99);

        assertTrue(standDown.active(START + 1_000));
    }
}
