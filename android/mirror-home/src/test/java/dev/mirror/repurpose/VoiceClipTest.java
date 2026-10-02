package dev.mirror.repurpose;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import org.junit.Test;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

public final class VoiceClipTest {
    @Test
    public void aClipAtTheRecognisersRateIsReadAsItIs() throws Exception {
        short[] samples = {0, 1200, -1200, 32767, -32768};

        assertArrayEquals(samples, VoiceClip.read(wav(16_000, 1, 16, samples)));
    }

    @Test
    public void aStereoClipAtThreeTimesTheRateBecomesItsFirstChannelAveraged() throws Exception {
        // Left and right alternate; three left samples make one of the recogniser's.
        short[] samples = {300, -9, 600, -9, 900, -9, -30, 7, -60, 7, -90, 7};

        assertArrayEquals(new short[]{600, -60}, VoiceClip.read(wav(48_000, 2, 16, samples)));
    }

    @Test
    public void chunksBesideTheSoundArePassedOver() throws Exception {
        ByteArrayOutputStream file = new ByteArrayOutputStream();
        file.write(riff());
        file.write(chunk("fmt ", format(16_000, 1, 16)));
        // An odd-sized chunk is followed by one byte of padding.
        file.write(chunk("LIST", new byte[]{1, 2, 3}));
        file.write(0);
        file.write(chunk("data", bytes(new short[]{5, 6})));

        assertArrayEquals(new short[]{5, 6}, VoiceClip.read(new ByteArrayInputStream(file.toByteArray())));
    }

    @Test
    public void whatTheRecogniserCannotTakeIsRefusedWithTheReason() throws Exception {
        assertRefused("Not a WAV file", new ByteArrayInputStream("this is no sound file".getBytes(StandardCharsets.US_ASCII)));
        assertRefused("Only 16-bit sound at 16 or 48 kHz", wav(44_100, 1, 16, new short[]{1}));
        assertRefused("Only 16-bit sound at 16 or 48 kHz", wav(16_000, 1, 8, new short[]{1}));
        ByteArrayOutputStream silent = new ByteArrayOutputStream();
        silent.write(riff());
        silent.write(chunk("fmt ", format(16_000, 1, 16)));
        assertRefused("has no sound", new ByteArrayInputStream(silent.toByteArray()));
        ByteArrayOutputStream backwards = new ByteArrayOutputStream();
        backwards.write(riff());
        backwards.write(chunk("data", bytes(new short[]{1})));
        assertRefused("comes before its format", new ByteArrayInputStream(backwards.toByteArray()));
    }

    private static void assertRefused(String reason, ByteArrayInputStream input) {
        try {
            VoiceClip.read(input);
            fail("Read a clip that should be refused: " + reason);
        } catch (IOException refused) {
            assertTrue(refused.getMessage(), refused.getMessage().contains(reason));
        }
    }

    private static ByteArrayInputStream wav(int rate, int channels, int bits, short[] samples)
            throws IOException {
        ByteArrayOutputStream file = new ByteArrayOutputStream();
        file.write(riff());
        file.write(chunk("fmt ", format(rate, channels, bits)));
        file.write(chunk("data", bytes(samples)));
        assertEquals(0, file.size() % 2);
        return new ByteArrayInputStream(file.toByteArray());
    }

    private static byte[] riff() {
        return ByteBuffer.allocate(12).order(ByteOrder.LITTLE_ENDIAN)
                .put("RIFF".getBytes(StandardCharsets.US_ASCII))
                .putInt(0)
                .put("WAVE".getBytes(StandardCharsets.US_ASCII))
                .array();
    }

    private static byte[] format(int rate, int channels, int bits) {
        return ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
                .putShort((short) 1)
                .putShort((short) channels)
                .putInt(rate)
                .putInt(rate * channels * bits / 8)
                .putShort((short) (channels * bits / 8))
                .putShort((short) bits)
                .array();
    }

    private static byte[] chunk(String tag, byte[] body) {
        return ByteBuffer.allocate(8 + body.length).order(ByteOrder.LITTLE_ENDIAN)
                .put(tag.getBytes(StandardCharsets.US_ASCII))
                .putInt(body.length)
                .put(body)
                .array();
    }

    private static byte[] bytes(short[] samples) {
        ByteBuffer buffer = ByteBuffer.allocate(samples.length * 2).order(ByteOrder.LITTLE_ENDIAN);
        for (short sample : samples) {
            buffer.putShort(sample);
        }
        return buffer.array();
    }
}
