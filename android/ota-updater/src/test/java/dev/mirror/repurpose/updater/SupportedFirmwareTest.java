package dev.mirror.repurpose.updater;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class SupportedFirmwareTest {
    @Test
    public void acceptsTheFactoryImageAndSilveringBuildsForTheBoard() {
        assertTrue(SupportedFirmware.supports(
                "mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/329:user/release-keys"));
        // What the Silvering build gave before its build number was fixed.
        assertTrue(SupportedFirmware.supports(
                "mirror/silvering/msm8916_64:6.0.1/MMB29M/builder11180644:userdebug/test-keys"));
        assertTrue(SupportedFirmware.supports(
                "mirror/silvering/msm8916_64:6.0.1/MMB29M/silvering.54b14ca1d2e3:userdebug/test-keys"));
        assertTrue(SupportedFirmware.supports(
                "mirror/silvering/msm8916_64:6.0.1/MMB29M/silvering.54b14ca1d2e3:user/release-keys"));
    }

    @Test
    public void refusesOtherFirmware() {
        assertFalse(SupportedFirmware.supports(null));
        assertFalse(SupportedFirmware.supports(""));
        // Another factory build, another board, another Android release or CAF release.
        assertFalse(SupportedFirmware.supports(
                "mirror/mirror/msm8916_64:6.0.1/IFC-6309-2.0-MIR/412:user/release-keys"));
        assertFalse(SupportedFirmware.supports(
                "mirror/silvering/msm8909:6.0.1/MMB29M/silvering.54b14ca1d2e3:userdebug/test-keys"));
        assertFalse(SupportedFirmware.supports(
                "mirror/silvering/msm8916_64:7.1.2/MMB29M/silvering.54b14ca1d2e3:userdebug/test-keys"));
        assertFalse(SupportedFirmware.supports(
                "mirror/silvering/msm8916_64:6.0.1/LMY47V/silvering.54b14ca1d2e3:userdebug/test-keys"));
        // A plain CAF build is not Silvering; neither is an eng build.
        assertFalse(SupportedFirmware.supports(
                "Android/msm8916_64/msm8916_64:6.0.1/MMB29M/builder10280100:userdebug/test-keys"));
        assertFalse(SupportedFirmware.supports(
                "mirror/silvering/msm8916_64:6.0.1/MMB29M/silvering.54b14ca1d2e3:eng/test-keys"));
        // Nothing missing, nothing extra.
        assertFalse(SupportedFirmware.supports(
                "mirror/silvering/msm8916_64:6.0.1/MMB29M/:userdebug/test-keys"));
        assertFalse(SupportedFirmware.supports(
                "mirror/silvering/msm8916_64:6.0.1/MMB29M/a/b:userdebug/test-keys"));
        assertFalse(SupportedFirmware.supports(
                "mirror/silvering/msm8916_64:6.0.1/MMB29M/silvering.1:userdebug/test-keys\n"));
        assertFalse(SupportedFirmware.supports(
                "xmirror/silvering/msm8916_64:6.0.1/MMB29M/silvering.1:userdebug/test-keys"));
    }
}
