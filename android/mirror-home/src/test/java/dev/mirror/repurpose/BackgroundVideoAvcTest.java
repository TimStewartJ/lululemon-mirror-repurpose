package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import android.media.MediaCodecInfo;

import org.junit.Test;

public final class BackgroundVideoAvcTest {
    @Test
    public void parsesAnnexBHighProfileLevel41Sps() {
        BackgroundVideoLibrary.AvcSpec spec = BackgroundVideoLibrary.parseAvcSpec(
                new byte[]{0, 0, 0, 1, 0x67, 100, 0, 41, 1});

        assertEquals(100, spec.profileIdc);
        assertEquals(41, spec.levelIdc);
    }

    @Test
    public void parsesAvcConfigurationRecord() {
        BackgroundVideoLibrary.AvcSpec spec = BackgroundVideoLibrary.parseAvcSpec(
                new byte[]{1, 77, 0, 31});

        assertEquals(77, spec.profileIdc);
        assertEquals(31, spec.levelIdc);
    }

    @Test
    public void rejectsUnsupportedAvcProfile() {
        assertNull(BackgroundVideoLibrary.parseAvcSpec(
                new byte[]{0, 0, 0, 1, 0x67, 55, 0, 31}));
    }

    @Test
    public void highLevel41CapabilityAcceptsHighLevel41Video() {
        BackgroundVideoLibrary.AvcSpec requested =
                BackgroundVideoLibrary.parseAvcSpec(new byte[]{1, 100, 0, 41});

        assertTrue(BackgroundVideoLibrary.supportsAvcProfileLevel(
                MediaCodecInfo.CodecProfileLevel.AVCProfileHigh,
                MediaCodecInfo.CodecProfileLevel.AVCLevel41,
                requested));
    }

    @Test
    public void highCapabilityDoesNotAcceptHigh10Video() {
        BackgroundVideoLibrary.AvcSpec requested =
                BackgroundVideoLibrary.parseAvcSpec(new byte[]{1, 110, 0, 41});

        assertFalse(BackgroundVideoLibrary.supportsAvcProfileLevel(
                MediaCodecInfo.CodecProfileLevel.AVCProfileHigh,
                MediaCodecInfo.CodecProfileLevel.AVCLevel41,
                requested));
    }

    @Test
    public void level4CompatibilityAcceptsLevel41WithinAppEnvelope() {
        BackgroundVideoLibrary.AvcSpec requested =
                BackgroundVideoLibrary.parseAvcSpec(new byte[]{1, 100, 0, 41});

        assertTrue(BackgroundVideoLibrary.supportsAvcProfileLevel(
                MediaCodecInfo.CodecProfileLevel.AVCProfileHigh,
                MediaCodecInfo.CodecProfileLevel.AVCLevel4,
                requested,
                true));
        assertTrue(BackgroundVideoLibrary.fitsAvcLevel4Envelope(
                1080,
                1920,
                24f,
                5_000_000));
    }

    @Test
    public void level4CompatibilityRejectsExcessMacroblockRate() {
        assertFalse(BackgroundVideoLibrary.fitsAvcLevel4Envelope(
                1080,
                1920,
                30.5f,
                5_000_000));
    }

    @Test
    public void level4CompatibilityRejectsExcessBitRate() {
        assertFalse(BackgroundVideoLibrary.fitsAvcLevel4Envelope(
                1080,
                1920,
                24f,
                20_000_001));
    }

    @Test
    public void highCapabilityDoesNotClaimFullBaselineSupport() {
        BackgroundVideoLibrary.AvcSpec requested =
                BackgroundVideoLibrary.parseAvcSpec(new byte[]{1, 66, 0, 31});

        assertFalse(BackgroundVideoLibrary.supportsAvcProfileLevel(
                MediaCodecInfo.CodecProfileLevel.AVCProfileHigh,
                MediaCodecInfo.CodecProfileLevel.AVCLevel41,
                requested));
    }
}
