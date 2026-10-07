package dev.mirror.repurpose;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.nio.charset.StandardCharsets;

public class SystemLogTest {
    private static final long NOON = 1_791_100_000_000L;

    @Rule
    public final TemporaryFolder folder = new TemporaryFolder();

    private SystemLog log() {
        return new SystemLog(new File(folder.getRoot(), "logs"), "Mirror Home 2.3.0 (99)");
    }

    @Test
    public void aProcessMayReadTheWholeLogWhenItIsInAndroidsLogGroup() {
        String granted = "Name:\tirror.repurpose\nUid:\t10055\t10055\nGroups:\t1007 3003 9997 50055 \nVmPeak:\t1 kB\n";
        String plain = "Name:\tirror.repurpose\nGroups:\t3003 9997 50055 \n";
        assertTrue(SystemLog.member(granted, SystemLog.LOG_GROUP));
        assertFalse(SystemLog.member(plain, SystemLog.LOG_GROUP));
        // Not a group whose number merely contains it.
        assertFalse(SystemLog.member("Groups:\t10070 11007 \n", SystemLog.LOG_GROUP));
        assertFalse(SystemLog.member("Groups:\t\n", SystemLog.LOG_GROUP));
        assertFalse(SystemLog.member("", SystemLog.LOG_GROUP));
    }

    @Test
    public void aReasonBecomesPartOfAFileNameAndNothingElse() {
        assertEquals("wifi-lost", SystemLog.label("wifi-lost"));
        assertEquals("before-i-restart-it", SystemLog.label("Before I restart it!"));
        assertEquals("etc-passwd", SystemLog.label("../../etc/passwd"));
        assertEquals("asked", SystemLog.label(""));
        assertEquals("asked", SystemLog.label(null));
        assertEquals("asked", SystemLog.label("???"));
        String cut = SystemLog.label("a very long reason that goes on and on and on");
        assertTrue(cut, cut.length() <= 24 && cut.matches("[a-z0-9-]+") && !cut.endsWith("-"));
    }

    @Test
    public void theEndOfALogBeginsAtTheStartOfALine() {
        byte[] text = "first line\nsecond line\nthird\n".getBytes(StandardCharsets.UTF_8);
        assertArrayEquals(text, SystemLog.tail(text, 1_000));
        assertEquals("third\n", new String(SystemLog.tail(text, 8), StandardCharsets.UTF_8));
        assertEquals("second line\nthird\n", new String(SystemLog.tail(text, 18), StandardCharsets.UTF_8));
        assertEquals("", new String(SystemLog.tail(text, 3), StandardCharsets.UTF_8));
    }

    @Test
    public void aCopyIsNamedByItsTimeAndReasonAndCanBeReadBack() throws Exception {
        SystemLog log = log();
        JSONObject copy = log.capture("Wi-Fi lost", NOON);
        assertNotNull(copy);
        assertEquals("log-" + NOON + "-wi-fi-lost.txt", copy.getString("name"));
        assertEquals(NOON, copy.getLong("at"));
        assertEquals("wi-fi-lost", copy.getString("reason"));
        assertEquals(SystemLog.whole(), copy.getBoolean("whole"));

        String text = new String(log.read(copy.getString("name")), StandardCharsets.UTF_8);
        assertTrue(text, text.startsWith("# Mirror Home 2.3.0 (99)\n"));
        assertTrue(text, text.contains("because: wi-fi-lost"));
        assertTrue(text, text.contains(
                SystemLog.whole() ? "# Android's whole log.\n" : "Only Mirror Home's own lines"));
        assertTrue(text, text.contains("# What the log says of Wi-Fi and the network\n"));
        assertEquals(copy.getLong("bytes"), text.getBytes(StandardCharsets.UTF_8).length);

        JSONArray list = log.list();
        assertEquals(1, list.length());
        assertEquals(copy.getString("name"), list.getJSONObject(0).getString("name"));
    }

    @Test
    public void onlyTheNewestCopiesAreKept() throws Exception {
        SystemLog log = log();
        for (int index = 0; index < SystemLog.KEEP + 3; index++) {
            assertNotNull(log.capture("n" + index, NOON + index));
        }
        JSONArray list = log.list();
        assertEquals(SystemLog.KEEP, list.length());
        assertEquals("n3", list.getJSONObject(0).getString("reason"));
        assertEquals("n" + (SystemLog.KEEP + 2), list.getJSONObject(SystemLog.KEEP - 1).getString("reason"));
    }

    @Test
    public void aNetworkThatComesAndGoesDoesNotPushOutWhatWasKeptOfALongOutage() throws Exception {
        SystemLog log = log();
        log.capture("wifi-rejoin", NOON);
        log.capture("wifi-restart", NOON + 1);
        log.capture("wifi-back", NOON + 2);
        for (int index = 0; index < 40; index++) {
            assertNotNull(log.capture(SystemLog.LOSS, NOON + 10 + index));
        }
        JSONArray list = log.list();
        assertEquals(3 + SystemLog.KEEP, list.length());
        assertEquals("wifi-rejoin", list.getJSONObject(0).getString("reason"));
        assertEquals("wifi-back", list.getJSONObject(2).getString("reason"));
        // Of the losses, the newest.
        assertEquals(NOON + 10 + 40 - SystemLog.KEEP, list.getJSONObject(3).getLong("at"));
        assertEquals(NOON + 49, list.getJSONObject(list.length() - 1).getLong("at"));
    }

    @Test
    public void nothingButACopyOfTheLogCanBeRead() throws Exception {
        SystemLog log = log();
        log.capture("asked", NOON);
        assertTrue(new File(folder.getRoot(), "secret.txt").createNewFile());
        assertNull(log.read("../secret.txt"));
        assertNull(log.read("log-1-../../secret.txt"));
        assertNull(log.read("log-" + (NOON + 1) + "-asked.txt"));
        assertNull(log.read(""));
        assertNull(log.read(null));
        assertEquals(0, new SystemLog(new File(folder.getRoot(), "none"), "x").list().length());
    }
}
