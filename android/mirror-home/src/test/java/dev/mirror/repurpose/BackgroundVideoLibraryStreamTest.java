package dev.mirror.repurpose;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.security.MessageDigest;

import org.junit.Test;

public final class BackgroundVideoLibraryStreamTest {
    @Test
    public void streamsExactlyTheDeclaredBodyIntoOneFile() throws Exception {
        byte[] body = "video-bytes".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        ByteArrayInputStream input = new ByteArrayInputStream(
                "video-bytesNEXT".getBytes(java.nio.charset.StandardCharsets.UTF_8));
        File output = Files.createTempFile("background-video", ".part").toFile();
        try {
            String id = BackgroundVideoLibrary.writeIncoming(input, output, body.length);

            assertEquals(hex(MessageDigest.getInstance("SHA-256").digest(body)), id);
            assertArrayEquals(body, Files.readAllBytes(output.toPath()));
            assertEquals('N', input.read());
        } finally {
            output.delete();
        }
    }

    @Test
    public void removesPartialFileWhenUploadEndsEarly() throws Exception {
        File output = Files.createTempFile("background-video", ".part").toFile();
        try {
            BackgroundVideoLibrary.writeIncoming(
                    new ByteArrayInputStream(new byte[]{1, 2}),
                    output,
                    3);
        } catch (IOException expected) {
            assertFalse(output.exists());
            return;
        } finally {
            output.delete();
        }
        throw new AssertionError("Expected an early-upload failure");
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder();
        for (byte value : bytes) {
            result.append(String.format(java.util.Locale.US, "%02x", value & 0xff));
        }
        return result.toString();
    }
}
