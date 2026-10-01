package dev.mirror.repurpose.voicelab;

import java.io.Closeable;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

/** 16-bit PCM WAV files: all this lab records and all it recognises. */
final class Wav {
    final int sampleRate;
    final int channels;
    final short[] samples;

    Wav(int sampleRate, int channels, short[] samples) {
        this.sampleRate = sampleRate;
        this.channels = channels;
        this.samples = samples;
    }

    double seconds() {
        return samples.length / (double) (sampleRate * channels);
    }

    /** One channel of an interleaved recording. */
    short[] channel(int index) {
        short[] result = new short[samples.length / channels];
        for (int frame = 0; frame < result.length; frame++) {
            result[frame] = samples[frame * channels + index];
        }
        return result;
    }

    /** The first channel at the 16 kHz the recogniser takes. */
    short[] mono16k() throws IOException {
        return Downsampler.toRate(channel(0), sampleRate, Downsampler.TARGET_RATE);
    }

    static Wav read(File file) throws IOException {
        try (InputStream input = new FileInputStream(file)) {
            return read(input);
        }
    }

    static Wav read(InputStream stream) throws IOException {
        DataInputStream input = new DataInputStream(stream);
        byte[] header = new byte[12];
        input.readFully(header);
        if (!tag(header, 0).equals("RIFF") || !tag(header, 8).equals("WAVE")) {
            throw new IOException("Not a WAV file");
        }
        int sampleRate = 0;
        int channels = 0;
        while (true) {
            byte[] chunk = new byte[8];
            try {
                input.readFully(chunk);
            } catch (EOFException end) {
                throw new IOException("WAV file has no sound data");
            }
            String name = tag(chunk, 0);
            int size = ByteBuffer.wrap(chunk, 4, 4).order(ByteOrder.LITTLE_ENDIAN).getInt();
            if (size < 0) {
                throw new IOException("WAV chunk is too large");
            }
            if (name.equals("fmt ")) {
                byte[] format = new byte[size];
                input.readFully(format);
                ByteBuffer fields = ByteBuffer.wrap(format).order(ByteOrder.LITTLE_ENDIAN);
                int encoding = fields.getShort(0);
                channels = fields.getShort(2);
                sampleRate = fields.getInt(4);
                int bits = fields.getShort(14);
                if (encoding != 1 || bits != 16 || channels < 1 || channels > 2) {
                    throw new IOException("Only 16-bit PCM with one or two channels is supported");
                }
            } else if (name.equals("data")) {
                if (sampleRate == 0) {
                    throw new IOException("WAV sound data comes before its format");
                }
                byte[] data = new byte[size];
                int read = 0;
                // A writer that was interrupted leaves a longer size than the file holds.
                while (read < size) {
                    int count = input.read(data, read, size - read);
                    if (count < 0) {
                        break;
                    }
                    read += count;
                }
                short[] samples = new short[read / 2];
                ByteBuffer.wrap(data, 0, samples.length * 2)
                        .order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(samples);
                return new Wav(sampleRate, channels, samples);
            } else {
                long wanted = size + (size & 1);
                long skipped = 0;
                while (skipped < wanted) {
                    long count = input.skip(wanted - skipped);
                    if (count <= 0) {
                        if (input.read() < 0) {
                            throw new IOException("WAV file has no sound data");
                        }
                        count = 1;
                    }
                    skipped += count;
                }
            }
        }
    }

    static void write(File file, int sampleRate, int channels, short[] samples, int count)
            throws IOException {
        try (Writer writer = new Writer(file, sampleRate, channels)) {
            writer.write(samples, count);
        }
    }

    /** Writes a recording piece by piece, for sound too long to hold in memory. */
    static final class Writer implements Closeable {
        private final RandomAccessFile output;
        private final int sampleRate;
        private final int channels;
        private long soundBytes;

        Writer(File file, int sampleRate, int channels) throws IOException {
            this.sampleRate = sampleRate;
            this.channels = channels;
            output = new RandomAccessFile(file, "rw");
            output.setLength(0);
            output.write(header(0));
        }

        void write(short[] samples, int count) throws IOException {
            ByteBuffer bytes = ByteBuffer.allocate(count * 2).order(ByteOrder.LITTLE_ENDIAN);
            bytes.asShortBuffer().put(samples, 0, count);
            output.write(bytes.array());
            soundBytes += count * 2L;
        }

        /** Completes the file: only now is its length known. */
        @Override
        public void close() throws IOException {
            try {
                output.seek(0);
                output.write(header(soundBytes));
            } finally {
                output.close();
            }
        }

        private byte[] header(long bytes) {
            ByteBuffer header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN);
            header.put(ascii("RIFF")).putInt((int) (36 + bytes)).put(ascii("WAVE"));
            header.put(ascii("fmt ")).putInt(16).putShort((short) 1).putShort((short) channels);
            header.putInt(sampleRate).putInt(sampleRate * channels * 2);
            header.putShort((short) (channels * 2)).putShort((short) 16);
            header.put(ascii("data")).putInt((int) bytes);
            return header.array();
        }
    }

    private static String tag(byte[] bytes, int offset) {
        return new String(bytes, offset, 4, StandardCharsets.US_ASCII);
    }

    private static byte[] ascii(String text) {
        return text.getBytes(StandardCharsets.US_ASCII);
    }
}
