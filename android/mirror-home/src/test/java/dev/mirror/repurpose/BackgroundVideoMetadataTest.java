package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONObject;
import org.junit.Test;

public final class BackgroundVideoMetadataTest {
    @Test
    public void metadataRoundTripsWithoutFilesystemPaths() throws Exception {
        BackgroundVideoMetadata original = new BackgroundVideoMetadata(
                repeat('a'),
                "Four Seasons.mp4",
                27_000_000L,
                123_456L,
                "video/avc",
                1080,
                1920,
                0,
                48_000L,
                24f,
                4_800_000,
                "OMX.qcom.video.decoder.avc",
                100,
                41,
                false);

        JSONObject visible = original.toJson(true, false, true);
        BackgroundVideoMetadata parsed = BackgroundVideoMetadata.parse(original.serialize());

        assertEquals(original.id, parsed.id);
        assertEquals(original.name, parsed.name);
        assertEquals(original.durationMs, parsed.durationMs);
        assertTrue(visible.getBoolean("active"));
        assertTrue(visible.getBoolean("posterAvailable"));
        assertFalsePath(visible.toString());
    }

    @Test(expected = IllegalArgumentException.class)
    public void metadataRejectsMalformedContentIds() {
        new BackgroundVideoMetadata(
                "../video",
                "bad.mp4",
                1,
                1,
                "video/avc",
                1,
                1,
                0,
                1,
                1,
                1,
                "decoder",
                66,
                31,
                false);
    }

    private static void assertFalsePath(String value) {
        assertTrue(!value.contains("/data/"));
        assertTrue(!value.contains("\\\\"));
    }

    private static String repeat(char value) {
        StringBuilder result = new StringBuilder();
        for (int index = 0; index < 64; index++) {
            result.append(value);
        }
        return result.toString();
    }
}
