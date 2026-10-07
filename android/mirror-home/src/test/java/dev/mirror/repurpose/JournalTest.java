package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

public class JournalTest {
    private static final long NOON = 1_791_100_000_000L;

    @Rule
    public final TemporaryFolder folder = new TemporaryFolder();

    private Journal journal(long fileBytes, String boot, long run) {
        return new Journal(new File(folder.getRoot(), "journal"), fileBytes, boot, run);
    }

    @Test
    public void aLineSaysWhenWhatAndInWhichRun() throws Exception {
        Journal journal = journal(Journal.FILE_BYTES, "d517b087", 61);
        journal.record(NOON, 5_000, "wifi", "lost", new JSONObject().put("doing", "SCANNING"));
        journal.record(NOON + 1, 5_001, "home", "started", null);

        JSONArray lines = journal.read(0, null, 100);
        assertEquals(2, lines.length());
        JSONObject first = lines.getJSONObject(0);
        assertEquals(NOON, first.getLong("at"));
        assertEquals(5_000, first.getLong("up"));
        assertEquals("d517b087", first.getString("boot"));
        assertEquals(61, first.getLong("run"));
        assertEquals("wifi", first.getString("kind"));
        assertEquals("lost", first.getString("what"));
        assertEquals("SCANNING", first.getJSONObject("more").getString("doing"));
        assertFalse(lines.getJSONObject(1).has("more"));
    }

    @Test
    public void whatWasWrittenIsStillThereForTheNextRunAndTheNextBoot() throws Exception {
        journal(Journal.FILE_BYTES, "boot-one", 61).record(NOON, 9_000_000, "wifi", "lost", null);
        Journal next = journal(Journal.FILE_BYTES, "boot-two", 62);
        next.record(NOON + 60_000, 20_000, "home", "started", null);

        JSONArray lines = next.read(0, null, 100);
        assertEquals(2, lines.length());
        assertEquals("boot-one", lines.getJSONObject(0).getString("boot"));
        assertEquals("boot-two", lines.getJSONObject(1).getString("boot"));
        JSONObject summary = next.summary();
        assertEquals(2, summary.getLong("lines"));
        assertEquals(NOON, summary.getLong("oldestAt"));
        assertEquals(NOON + 60_000, summary.getLong("newestAt"));
        assertEquals(1, summary.getLong("writtenThisRun"));
    }

    @Test
    public void linesCanBeAskedForByTimeByKindAndTheNewestOnly() throws Exception {
        Journal journal = journal(Journal.FILE_BYTES, "boot", 1);
        for (int index = 0; index < 10; index++) {
            journal.record(NOON + index, index, index % 2 == 0 ? "wifi" : "home", "n" + index, null);
        }
        assertEquals(5, journal.read(0, "wifi", 100).length());
        assertEquals(10, journal.read(0, "", 100).length());
        JSONArray late = journal.read(NOON + 7, null, 100);
        assertEquals(3, late.length());
        assertEquals("n7", late.getJSONObject(0).getString("what"));
        JSONArray newest = journal.read(0, null, 2);
        assertEquals("n8", newest.getJSONObject(0).getString("what"));
        assertEquals("n9", newest.getJSONObject(1).getString("what"));
    }

    @Test
    public void aFullFileBecomesTheOlderOneAndTheOneBeforeItGoes() throws Exception {
        Journal journal = journal(600, "boot", 1);
        for (int index = 0; index < 40; index++) {
            // Slowly, so that nothing is taken for a burst.
            journal.record(NOON + index, index * Journal.BURST_WINDOW_MS, "wifi", "line-" + index, null);
        }
        JSONArray lines = journal.read(0, null, 100);
        assertTrue("Nothing was dropped: " + lines.length(), lines.length() < 40);
        assertTrue("Too little was kept: " + lines.length(), lines.length() >= 7);
        // What is kept is the newest, in order and without a gap.
        for (int index = 0; index < lines.length(); index++) {
            assertEquals("line-" + (40 - lines.length() + index), lines.getJSONObject(index).getString("what"));
        }
        File[] files = new File(folder.getRoot(), "journal").listFiles();
        assertEquals(2, files.length);
        for (File file : files) {
            assertTrue(file.getName() + " holds " + file.length(), file.length() <= 600);
        }
        JSONObject summary = journal.summary();
        assertEquals(lines.length(), summary.getLong("lines"));
        assertEquals(lines.getJSONObject(0).getLong("at"), summary.getLong("oldestAt"));
        assertEquals(NOON + 39, summary.getLong("newestAt"));
    }

    @Test
    public void somethingThatWritesWithoutEndIsCountedAndDoesNotPushOutTheRest() throws Exception {
        Journal journal = journal(Journal.FILE_BYTES, "boot", 1);
        journal.record(NOON, 0, "home", "started", null);
        for (int index = 0; index < 1_000; index++) {
            journal.record(NOON + index, 1_000 + index, "wifi", "state", null);
        }
        assertEquals(Journal.BURST, journal.read(0, null, 5_000).length());
        assertEquals(1_001 - Journal.BURST, journal.summary().getLong("leftOut"));

        // Once it has been quiet for a while, the journal says what is missing and goes on.
        journal.record(NOON + 2_000, 2_000 + Journal.BURST_WINDOW_MS, "wifi", "back", null);
        JSONArray lines = journal.read(0, null, 5_000);
        assertEquals(Journal.BURST + 2, lines.length());
        JSONObject gap = lines.getJSONObject(lines.length() - 2);
        assertEquals("journal", gap.getString("kind"));
        assertEquals("left-out", gap.getString("what"));
        assertEquals(1_001 - Journal.BURST, gap.getJSONObject("more").getInt("lines"));
        assertEquals("back", lines.getJSONObject(lines.length() - 1).getString("what"));
    }

    @Test
    public void aLineThatThePowerCutShortIsPassedOver() throws Exception {
        Journal journal = journal(Journal.FILE_BYTES, "boot", 1);
        journal.record(NOON, 0, "wifi", "lost", null);
        try (FileOutputStream out = new FileOutputStream(
                new File(folder.getRoot(), "journal/journal.jsonl"), true)) {
            out.write("{\"at\":1791100000500,\"up\":3,\"bo".getBytes(StandardCharsets.UTF_8));
        }
        Journal next = journal(Journal.FILE_BYTES, "boot", 2);
        next.record(NOON + 1_000, 10, "wifi", "back", null);
        JSONArray lines = next.read(0, null, 100);
        assertEquals(2, lines.length());
        assertEquals("back", lines.getJSONObject(1).getString("what"));
    }

    @Test
    public void aLineTooLongToKeepSaysSoInsteadOfItsDetails() throws Exception {
        Journal journal = journal(Journal.FILE_BYTES, "boot", 1);
        StringBuilder much = new StringBuilder();
        for (int index = 0; index < Journal.LINE_BYTES; index++) {
            much.append('x');
        }
        journal.record(NOON, 0, "wifi", "lost", new JSONObject().put("much", much));
        JSONObject line = journal.read(0, null, 1).getJSONObject(0);
        assertEquals("lost", line.getString("what"));
        assertTrue(line.getJSONObject("more").getBoolean("tooLong"));
    }

    @Test
    public void wordsWithQuotesAndLineBreaksStayOneLine() throws Exception {
        Journal journal = journal(Journal.FILE_BYTES, "boot", 1);
        journal.record(NOON, 0, "wifi", "said \"no\"\nand left", new JSONObject().put("network", "Caf\u00e9 \"5G\""));
        journal.record(NOON + 1, 1, "wifi", "next", null);
        JSONArray lines = journal.read(0, null, 10);
        assertEquals(2, lines.length());
        assertEquals("said \"no\"\nand left", lines.getJSONObject(0).getString("what"));
        assertEquals("Caf\u00e9 \"5G\"", lines.getJSONObject(0).getJSONObject("more").getString("network"));
    }
}
