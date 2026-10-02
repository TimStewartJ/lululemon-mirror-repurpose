package dev.mirror.repurpose;

import java.io.DataInputStream;
import java.io.EOFException;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

/**
 * A recorded sentence, read from a WAV file as the recogniser takes sound:
 * one channel at 16 kHz. The validation suite speaks to a debug build with
 * these, because an emulator has no microphone to speak into.
 */
final class VoiceClip {
    private static final int MAX_BYTES = 8 * 1024 * 1024;

    private VoiceClip() {
    }

    static short[] read(File file) throws IOException {
        try (InputStream input = new FileInputStream(file)) {
            return read(input);
        }
    }

    static short[] read(InputStream stream) throws IOException {
        DataInputStream input = new DataInputStream(stream);
        byte[] header = new byte[12];
        input.readFully(header);
        if (!tag(header, 0).equals("RIFF") || !tag(header, 8).equals("WAVE")) {
            throw new IOException("Not a WAV file");
        }
        int rate = 0;
        int channels = 0;
        while (true) {
            byte[] chunk = new byte[8];
            try {
                input.readFully(chunk);
            } catch (EOFException end) {
                throw new IOException("The WAV file has no sound");
            }
            int size = ByteBuffer.wrap(chunk, 4, 4).order(ByteOrder.LITTLE_ENDIAN).getInt();
            if (size < 0 || size > MAX_BYTES) {
                throw new IOException("The WAV file is too large");
            }
            byte[] body = new byte[size];
            int read = 0;
            while (read < size) {
                int count = input.read(body, read, size - read);
                if (count < 0) {
                    break;
                }
                read += count;
            }
            if (tag(chunk, 0).equals("fmt ")) {
                if (read < 16) {
                    throw new IOException("The WAV file has no format");
                }
                ByteBuffer format = ByteBuffer.wrap(body).order(ByteOrder.LITTLE_ENDIAN);
                int encoding = format.getShort(0);
                channels = format.getShort(2);
                rate = format.getInt(4);
                int bits = format.getShort(14);
                if (encoding != 1 || bits != 16 || channels < 1 || channels > 2
                        || (rate != VoiceService.SAMPLE_RATE && rate != 3 * VoiceService.SAMPLE_RATE)) {
                    throw new IOException("Only 16-bit sound at 16 or 48 kHz is supported");
                }
            } else if (tag(chunk, 0).equals("data")) {
                if (rate == 0) {
                    throw new IOException("The WAV file's sound comes before its format");
                }
                short[] samples = new short[read / 2];
                ByteBuffer.wrap(body, 0, samples.length * 2)
                        .order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(samples);
                return reduce(samples, channels, rate / VoiceService.SAMPLE_RATE);
            } else if ((size & 1) == 1 && input.read() < 0) {
                // Chunks are padded to an even length.
                throw new IOException("The WAV file has no sound");
            }
        }
    }

    /** The first channel, with each group of {@code factor} samples averaged into one. */
    private static short[] reduce(short[] samples, int channels, int factor) {
        short[] result = new short[samples.length / (channels * factor)];
        for (int index = 0; index < result.length; index++) {
            int sum = 0;
            for (int step = 0; step < factor; step++) {
                sum += samples[(index * factor + step) * channels];
            }
            result[index] = (short) (sum / factor);
        }
        return result;
    }

    private static String tag(byte[] bytes, int offset) {
        return new String(bytes, offset, 4, StandardCharsets.US_ASCII);
    }
}
