package dev.mirror.repurpose.health;

import static org.junit.Assert.assertEquals;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

public class HoldLinkTest {
    @Rule
    public TemporaryFolder folder = new TemporaryFolder();

    @Test
    public void theKernelsNumberIsReadAsItStands() throws Exception {
        // What is on the display, what that needs, a background service, and what the kernel never ends.
        assertEquals(0, HoldLink.score(file("0\n")));
        assertEquals(58, HoldLink.score(file("58\n")));
        assertEquals(294, HoldLink.score(file("294\n")));
        assertEquals(-1000, HoldLink.score(file("-1000\n")));
    }

    @Test
    public void whatCannotBeReadIsNotTakenForANumber() throws Exception {
        assertEquals(HoldLink.UNKNOWN, HoldLink.score(file("")));
        assertEquals(HoldLink.UNKNOWN, HoldLink.score(file("not a number\n")));
        assertEquals(HoldLink.UNKNOWN, HoldLink.score(new File(folder.getRoot(), "missing")));
    }

    private File file(String text) throws Exception {
        File file = folder.newFile();
        try (FileOutputStream out = new FileOutputStream(file)) {
            out.write(text.getBytes(StandardCharsets.UTF_8));
        }
        return file;
    }
}
