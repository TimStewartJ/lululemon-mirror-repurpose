package dev.mirror.repurpose;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.Charset;
import java.util.Arrays;

/**
 * Writes a tar stream: POSIX ustar headers, with GNU long-name entries for
 * paths that do not fit, which every common tar reader understands.
 */
final class TarWriter {
    static final int BLOCK = 512;
    static final char REGULAR = '0';
    static final char SYMLINK = '2';
    static final char DIRECTORY = '5';
    /** The largest size an 11-digit octal size field holds, plus one: 8 GiB. */
    static final long MAX_SIZE = 1L << 33;
    private static final char LONG_NAME = 'L';
    private static final char LONG_LINK = 'K';
    private static final Charset UTF8 = Charset.forName("UTF-8");

    private final OutputStream out;
    private long written;

    TarWriter(OutputStream out) {
        this.out = out;
    }

    long bytesWritten() {
        return written;
    }

    /** Writes the header of one entry; for a regular file, {@code size} bytes of content must follow. */
    void header(String name, char type, int mode, int uid, int gid, long size, long mtimeSeconds,
            String linkName) throws IOException {
        byte[] nameBytes = name.getBytes(UTF8);
        if (nameBytes.length > 100) {
            longEntry(LONG_NAME, nameBytes);
        }
        byte[] linkBytes = linkName == null ? new byte[0] : linkName.getBytes(UTF8);
        if (linkBytes.length > 100) {
            longEntry(LONG_LINK, linkBytes);
        }
        write(block(Arrays.copyOf(nameBytes, Math.min(100, nameBytes.length)), type, mode, uid, gid,
                size, mtimeSeconds, Arrays.copyOf(linkBytes, Math.min(100, linkBytes.length)), "ustar\0", "00"));
    }

    void content(byte[] buffer, int length) throws IOException {
        out.write(buffer, 0, length);
        written += length;
    }

    /** Pads a regular file's content to a whole block. */
    void endContent(long size) throws IOException {
        int pad = (int) ((BLOCK - size % BLOCK) % BLOCK);
        if (pad > 0) {
            write(new byte[pad]);
        }
    }

    void finish() throws IOException {
        write(new byte[BLOCK * 2]);
        out.flush();
    }

    private void longEntry(char type, byte[] value) throws IOException {
        byte[] withNul = Arrays.copyOf(value, value.length + 1);
        write(block("././@LongLink".getBytes(UTF8), type, 0644, 0, 0, withNul.length, 0,
                new byte[0], "ustar  ", ""));
        write(withNul);
        endContent(withNul.length);
    }

    static byte[] block(byte[] name, char type, int mode, int uid, int gid, long size, long mtime,
            byte[] linkName, String magic, String version) {
        byte[] header = new byte[BLOCK];
        System.arraycopy(name, 0, header, 0, Math.min(100, name.length));
        octal(header, 100, 8, mode & 07777);
        octal(header, 108, 8, Math.max(0, uid));
        octal(header, 116, 8, Math.max(0, gid));
        octal(header, 124, 12, size);
        octal(header, 136, 12, Math.max(0, mtime));
        Arrays.fill(header, 148, 156, (byte) ' ');
        header[156] = (byte) type;
        System.arraycopy(linkName, 0, header, 157, Math.min(100, linkName.length));
        byte[] magicBytes = magic.getBytes(UTF8);
        System.arraycopy(magicBytes, 0, header, 257, Math.min(8, magicBytes.length));
        byte[] versionBytes = version.getBytes(UTF8);
        System.arraycopy(versionBytes, 0, header, 263, Math.min(2, versionBytes.length));
        long sum = 0;
        for (byte value : header) {
            sum += value & 0xff;
        }
        String checksum = String.format(java.util.Locale.ROOT, "%06o", sum);
        System.arraycopy(checksum.getBytes(UTF8), 0, header, 148, 6);
        header[154] = 0;
        header[155] = ' ';
        return header;
    }

    private static void octal(byte[] header, int offset, int width, long value) {
        String text = Long.toOctalString(value);
        if (text.length() > width - 1) {
            throw new IllegalArgumentException("Value too large for the tar header: " + value);
        }
        Arrays.fill(header, offset, offset + width, (byte) '0');
        byte[] digits = text.getBytes(UTF8);
        System.arraycopy(digits, 0, header, offset + width - 1 - digits.length, digits.length);
        header[offset + width - 1] = 0;
    }

    private void write(byte[] bytes) throws IOException {
        out.write(bytes);
        written += bytes.length;
    }
}
