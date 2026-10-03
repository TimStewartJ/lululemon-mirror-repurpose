package dev.mirror.repurpose;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;

/**
 * The last half minute of what the microphone heard, kept in memory only so
 * that a request for the assistant can be cut out of it once the recogniser
 * has found where it began and ended. Older sound is overwritten as new
 * sound arrives; nothing is written anywhere unless a request is cut out.
 */
final class SoundRing {
    private final short[] ring;
    private long written;

    SoundRing(int capacity) {
        ring = new short[capacity];
    }

    synchronized void write(short[] samples, int count) {
        int start = Math.max(0, count - ring.length);
        for (int index = start; index < count; index++) {
            ring[(int) ((written + index) % ring.length)] = samples[index];
        }
        written += count;
    }

    /** How many samples have been written since the ring was made. */
    synchronized long written() {
        return written;
    }

    /**
     * The samples from {@code from} up to {@code to}, counted from the first
     * sample ever written, as far as they are still held.
     *
     * @return the samples, or an empty array if none of them are held
     */
    synchronized short[] copy(long from, long to) {
        long first = Math.max(Math.max(0, from), written - ring.length);
        long last = Math.min(to, written);
        if (last <= first) {
            return new short[0];
        }
        short[] samples = new short[(int) (last - first)];
        for (int index = 0; index < samples.length; index++) {
            samples[index] = ring[(int) ((first + index) % ring.length)];
        }
        return samples;
    }

    /** A WAV file of one channel of 16-bit samples. */
    static byte[] wav(short[] samples, int rate) {
        ByteBuffer header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN);
        header.put("RIFF".getBytes(StandardCharsets.US_ASCII));
        header.putInt(36 + samples.length * 2);
        header.put("WAVEfmt ".getBytes(StandardCharsets.US_ASCII));
        header.putInt(16);
        header.putShort((short) 1);
        header.putShort((short) 1);
        header.putInt(rate);
        header.putInt(rate * 2);
        header.putShort((short) 2);
        header.putShort((short) 16);
        header.put("data".getBytes(StandardCharsets.US_ASCII));
        header.putInt(samples.length * 2);
        ByteBuffer sound = ByteBuffer.allocate(samples.length * 2).order(ByteOrder.LITTLE_ENDIAN);
        sound.asShortBuffer().put(samples);
        ByteArrayOutputStream file = new ByteArrayOutputStream(44 + samples.length * 2);
        file.write(header.array(), 0, 44);
        file.write(sound.array(), 0, samples.length * 2);
        return file.toByteArray();
    }
}
