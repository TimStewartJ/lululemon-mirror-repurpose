package dev.mirror.repurpose.updater;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class OtaManagerPolicyTest {
    @Test
    public void recoveryStateLatchesKnownGoodPreservation() {
        assertTrue(OtaManager.shouldPreserveBackup(
                OtaConstants.STATE_RECOVERY_REQUIRED,
                false));
        assertTrue(OtaManager.shouldPreserveBackup(
                OtaConstants.STATE_FAILED,
                true));
    }

    @Test
    public void failedForwardRecoveryRemainsRecoveryRequired() {
        assertEquals(
                OtaConstants.STATE_RECOVERY_REQUIRED,
                OtaManager.candidateFailureState(true));
        assertEquals(
                OtaConstants.STATE_FAILED,
                OtaManager.candidateFailureState(false));
    }
}
