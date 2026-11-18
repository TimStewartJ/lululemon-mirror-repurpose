package dev.mirror.repurpose.updater;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
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

    @Test
    public void supportedFingerprintAcceptsStockAndCleanSourceRecoveredMirrorBuilds() {
        assertTrue(OtaManager.isSupportedFingerprint(BuildConfig.SUPPORTED_FINGERPRINT));
        assertTrue(OtaManager.isSupportedFingerprint(
                "Android/msm8916_64/msm8916_64:6.0.1/MMB29M/builder10280100:userdebug/test-keys"));
        assertTrue(OtaManager.isSupportedFingerprint(
                "Android/msm8916_64/msm8916_64:6.0.1/MMB29M/1:user/release-keys"));
    }

    @Test
    public void supportedFingerprintRejectsNullIncompleteAndUnrelatedBuilds() {
        assertFalse(OtaManager.isSupportedFingerprint(null));
        assertFalse(OtaManager.isSupportedFingerprint(""));
        assertFalse(OtaManager.isSupportedFingerprint(
                "Android/msm8916_64/msm8916_64:6.0.1/MMB29M"));
        assertFalse(OtaManager.isSupportedFingerprint(
                "Android/msm8916_64/msm8916_64:6.0.1/MMB29M/"));
        assertFalse(OtaManager.isSupportedFingerprint(
                "mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/412:user/release-keys"));
        assertFalse(OtaManager.isSupportedFingerprint(
                "google/bullhead/bullhead:6.0.1/MMB29M/2431559:user/release-keys"));
    }
}
