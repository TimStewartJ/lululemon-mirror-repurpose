package dev.mirror.repurpose.voicelab;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

public final class WavTest {
    @Rule
    public final TemporaryFolder folder = new TemporaryFolder();

    @Test
    public void whatIsWrittenIsReadBack() throws Exception {
        File file = folder.newFile("mono.wav");
        short[] samples = {0, 1, -1, 32767, -32768, 1234};
        Wav.write(file, 16_000, 1, samples, 5);

        Wav wav = Wav.read(file);
        assertEquals(16_000, wav.sampleRate);
        assertEquals(1, wav.channels);
        assertArrayEquals(new short[]{0, 1, -1, 32767, -32768}, wav.samples);
        assertEquals(5 / 16_000.0, wav.seconds(), 1e-9);
        assertEquals(44 + 10, file.length());
    }

    @Test
    public void aLongRecordingWrittenInPiecesReadsBackWhole() throws Exception {
        File file = folder.newFile("long.wav");
        try (Wav.Writer writer = new Wav.Writer(file, 16_000, 1)) {
            writer.write(new short[]{1, 2, 3}, 3);
            writer.write(new short[]{-4, -5, 99}, 2);
            writer.write(new short[0], 0);
        }
        Wav wav = Wav.read(file);
        assertEquals(16_000, wav.sampleRate);
        assertArrayEquals(new short[]{1, 2, 3, -4, -5}, wav.samples);
        assertEquals(44 + 10, file.length());
    }

    @Test
    public void aRecordingThatReplacesAnotherIsNotLongerThanItsSound() throws Exception {
        File file = folder.newFile("again.wav");
        Wav.write(file, 16_000, 1, new short[50], 50);
        Wav.write(file, 16_000, 1, new short[]{7}, 1);
        assertEquals(44 + 2, file.length());
        assertArrayEquals(new short[]{7}, Wav.read(file).samples);
    }

    @Test
    public void channelsOfAStereoRecordingAreSeparated() throws Exception {
        File file = folder.newFile("stereo.wav");
        Wav.write(file, 48_000, 2, new short[]{1, -1, 2, -2, 3, -3}, 6);

        Wav wav = Wav.read(file);
        assertEquals(2, wav.channels);
        assertArrayEquals(new short[]{1, 2, 3}, wav.channel(0));
        assertArrayEquals(new short[]{-1, -2, -3}, wav.channel(1));
        assertEquals(3 / 48_000.0, wav.seconds(), 1e-9);
    }

    @Test
    public void chunksOtherProgramsAddAreSkipped() throws Exception {
        // Windows speech writes a LIST chunk, of odd length here, before the sound.
        byte[] wav = build(16_000, 1, 16, 1, "LIST", new byte[]{1, 2, 3}, new short[]{7, 8});
        assertArrayEquals(new short[]{7, 8}, Wav.read(new ByteArrayInputStream(wav)).samples);
    }

    @Test
    public void aRecordingThatWasCutOffKeepsWhatItHas() throws Exception {
        byte[] whole = build(16_000, 1, 16, 1, null, null, new short[]{1, 2, 3, 4});
        byte[] cut = java.util.Arrays.copyOf(whole, whole.length - 3);
        assertArrayEquals(new short[]{1, 2}, Wav.read(new ByteArrayInputStream(cut)).samples);
    }

    @Test
    public void soundThatIsNotPlain16BitIsRefused() throws Exception {
        for (byte[] wav : new byte[][]{
                build(16_000, 1, 8, 1, null, null, new short[]{1}),
                build(16_000, 1, 16, 3, null, null, new short[]{1}),
                build(16_000, 6, 16, 1, null, null, new short[]{1}),
                "RIFFxxxxWAVX".getBytes(StandardCharsets.US_ASCII),
        }) {
            assertThrows(IOException.class, () -> Wav.read(new ByteArrayInputStream(wav)));
        }
    }

    @Test
    public void aFileWithoutSoundIsRefused() throws Exception {
        byte[] whole = build(16_000, 1, 16, 1, null, null, new short[]{1});
        byte[] headerOnly = java.util.Arrays.copyOf(whole, 36);
        assertThrows(IOException.class, () -> Wav.read(new ByteArrayInputStream(headerOnly)));
    }

    @Test
    public void aMicrophonesHigherRateIsBroughtDownForTheRecogniser() throws Exception {
        File file = folder.newFile("fast.wav");
        Wav.write(file, 48_000, 2, new short[]{3, 0, 6, 0, 9, 0, 30, 0, 60, 0, 90, 0}, 12);
        assertArrayEquals(new short[]{6, 60}, Wav.read(file).mono16k());
        assertEquals(44 + 24, Files.size(file.toPath()));
    }

    private static byte[] build(
            int rate, int channels, int bits, int encoding, String extraName, byte[] extra, short[] samples)
            throws IOException {
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        body.write("WAVE".getBytes(StandardCharsets.US_ASCII));
        ByteBuffer format = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN);
        format.put("fmt ".getBytes(StandardCharsets.US_ASCII)).putInt(16);
        format.putShort((short) encoding).putShort((short) channels).putInt(rate);
        format.putInt(rate * channels * bits / 8).putShort((short) (channels * bits / 8)).putShort((short) bits);
        body.write(format.array());
        if (extraName != null) {
            body.write(extraName.getBytes(StandardCharsets.US_ASCII));
            body.write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(extra.length).array());
            body.write(extra);
            if (extra.length % 2 == 1) {
                body.write(0);
            }
        }
        ByteBuffer data = ByteBuffer.allocate(8 + samples.length * 2).order(ByteOrder.LITTLE_ENDIAN);
        data.put("data".getBytes(StandardCharsets.US_ASCII)).putInt(samples.length * 2);
        for (short sample : samples) {
            data.putShort(sample);
        }
        body.write(data.array());
        ByteArrayOutputStream file = new ByteArrayOutputStream();
        file.write("RIFF".getBytes(StandardCharsets.US_ASCII));
        file.write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(body.size()).array());
        file.write(body.toByteArray());
        return file.toByteArray();
    }
}
