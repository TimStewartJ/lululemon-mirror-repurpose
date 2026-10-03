package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

public final class RequestTextTest {
    private static final String TEXT = "{\"text\":\"Caf\u00e9 72\u00b0 \u2600 \u65e5\u672c\"}";

    @Test
    public void textIsReadAsUtf8() throws IOException {
        byte[] sent = TEXT.getBytes(StandardCharsets.UTF_8);

        assertEquals(TEXT, RequestText.read(new ByteArrayInputStream(sent), sent.length));
    }

    @Test
    public void aCharacterThatArrivesInTwoPiecesIsStillOneCharacter() throws IOException {
        byte[] sent = TEXT.getBytes(StandardCharsets.UTF_8);
        InputStream oneByteAtATime = new InputStream() {
            private int next;

            @Override
            public int read() {
                return next < sent.length ? sent[next++] & 0xff : -1;
            }

            @Override
            public int read(byte[] target, int offset, int count) {
                if (next >= sent.length) {
                    return -1;
                }
                target[offset] = sent[next++];
                return 1;
            }
        };

        assertEquals(TEXT, RequestText.read(oneByteAtATime, sent.length));
    }

    @Test
    public void onlyTheBodyIsRead() throws IOException {
        byte[] sent = "{}next request".getBytes(StandardCharsets.UTF_8);

        assertEquals("{}", RequestText.read(new ByteArrayInputStream(sent), 2));
    }

    @Test
    public void aBodyThatEndsEarlyIsAnError() {
        byte[] sent = "{\"te".getBytes(StandardCharsets.UTF_8);

        assertThrows(IOException.class, () -> RequestText.read(new ByteArrayInputStream(sent), 40));
    }

    @Test
    public void anEmptyBodyIsEmptyText() throws IOException {
        assertEquals("", RequestText.read(new ByteArrayInputStream(new byte[0]), 0));
    }
}
