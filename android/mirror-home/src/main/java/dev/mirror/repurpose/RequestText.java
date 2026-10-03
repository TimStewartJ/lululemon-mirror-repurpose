package dev.mirror.repurpose;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * The text of a request body, read as UTF-8 whatever its Content-Type says.
 *
 * <p>NanoHTTPD decodes a POST body as ASCII unless Content-Type names a
 * charset, and browsers send JSON as plain {@code application/json}: a note
 * posted from the controls with an accent or a degree sign in it was stored
 * as replacement characters. JSON is UTF-8 by its own definition, so the
 * bytes are taken from the connection and decoded here.
 */
final class RequestText {
    private RequestText() {
    }

    /**
     * Reads exactly {@code length} bytes.
     *
     * @throws IOException if the connection ends before that
     */
    static String read(InputStream input, int length) throws IOException {
        byte[] bytes = new byte[length];
        int read = 0;
        while (read < length) {
            int count = input.read(bytes, read, length - read);
            if (count < 0) {
                throw new IOException("Request body ended early");
            }
            read += count;
        }
        return new String(bytes, StandardCharsets.UTF_8);
    }
}
