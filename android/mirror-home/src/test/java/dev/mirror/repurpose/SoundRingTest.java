package dev.mirror.repurpose;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;

import org.junit.Test;

import java.io.ByteArrayInputStream;

public final class SoundRingTest {
    private static short[] count(int from, int to) {
        short[] samples = new short[to - from];
        for (int index = 0; index < samples.length; index++) {
            samples[index] = (short) (from + index);
        }
        return samples;
    }

    @Test
    public void whatWasWrittenCanBeCutOutByItsPlaceInTheSound() {
        SoundRing ring = new SoundRing(100);
        ring.write(count(0, 30), 30);
        ring.write(count(30, 60), 30);

        assertEquals(60, ring.written());
        assertArrayEquals(count(10, 45), ring.copy(10, 45));
    }

    @Test
    public void olderSoundGivesWayToNewer() {
        SoundRing ring = new SoundRing(50);
        for (int start = 0; start < 200; start += 20) {
            ring.write(count(start, start + 20), 20);
        }

        assertArrayEquals(count(160, 190), ring.copy(160, 190));
        // Only the last fifty are still held.
        assertArrayEquals(count(150, 170), ring.copy(100, 170));
        assertEquals(0, ring.copy(0, 150).length);
    }

    @Test
    public void whatHasNotBeenHeardYetIsLeftOut() {
        SoundRing ring = new SoundRing(50);
        ring.write(count(0, 20), 20);

        assertArrayEquals(count(15, 20), ring.copy(15, 400));
        assertArrayEquals(count(0, 5), ring.copy(-30, 5));
        assertEquals(0, ring.copy(30, 20).length);
    }

    @Test
    public void onlyTheCountGivenIsTakenFromAChunk() {
        SoundRing ring = new SoundRing(50);
        ring.write(count(0, 20), 8);

        assertEquals(8, ring.written());
        assertArrayEquals(count(0, 8), ring.copy(0, 50));
    }

    @Test
    public void aChunkLargerThanTheRingKeepsItsEnd() {
        SoundRing ring = new SoundRing(10);
        ring.write(count(0, 35), 35);

        assertArrayEquals(count(25, 35), ring.copy(0, 35));
    }

    @Test
    public void aCutIsAWavFileTheRecogniserCouldReadBack() throws Exception {
        short[] samples = {0, 1, -1, 32767, -32768, 1234};

        byte[] file = SoundRing.wav(samples, 16_000);

        assertEquals(44 + 12, file.length);
        assertArrayEquals(samples, VoiceClip.read(new ByteArrayInputStream(file)));
    }
}
