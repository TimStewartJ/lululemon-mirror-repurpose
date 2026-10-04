package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

public class RestartAdviceTest {
    private static final long DAY = 24 * 3600L;
    private static final long MINUTE_MS = 60_000L;
    /** As the owner's Mirror reported it after nine days, while its kernel ended a process every six seconds. */
    private static final String SHORT_OF_MEMORY = "MemTotal:         952236 kB\n"
            + "MemFree:          246588 kB\n"
            + "Buffers:             296 kB\n"
            + "Cached:            80124 kB\n"
            + "SwapCached:        11020 kB\n"
            + "SwapTotal:        524284 kB\n"
            + "SwapFree:          97372 kB\n";

    @Rule
    public TemporaryFolder folder = new TemporaryFolder();

    @Test
    public void whatTheKernelSaysOfMemoryIsRead() {
        RestartAdvice.Memory memory = RestartAdvice.parse(SHORT_OF_MEMORY);
        assertEquals(246_588L, memory.freeKb);
        // Not the swap's own cache, which stands on the line after it.
        assertEquals(80_124L, memory.cachedKb);
        assertEquals(524_284L, memory.swapTotalKb);
        assertEquals(97_372L, memory.swapFreeKb);
        assertEquals(81, memory.swapUsedPercent());
    }

    @Test
    public void whatIsNotThereIsNotMadeUp() throws Exception {
        RestartAdvice.Memory memory = RestartAdvice.parse("MemFree:  12 kB\nSwapTotal:  0 kB\nSwapFree: 0 kB\n");
        assertEquals(-1L, memory.cachedKb);
        assertEquals(0, memory.swapUsedPercent());
        assertTrue(memory.toJson().isNull("cachedKb"));
        assertEquals(12L, memory.toJson().getLong("freeKb"));
        assertEquals(0, RestartAdvice.parse("").swapUsedPercent());
    }

    @Test
    public void aMirrorWhoseSwapHasFilledOverDaysAsksToBeRestarted() {
        RestartAdvice.Memory memory = RestartAdvice.parse(SHORT_OF_MEMORY);
        assertEquals(
                "Memory is running short after 9 days without a restart",
                RestartAdvice.reason(9 * DAY - 7_000, memory, 0));
        // Swap that is full on the second day is in use, not running out.
        assertNull(RestartAdvice.reason(2 * DAY, memory, 0));
        RestartAdvice.Memory roomy = new RestartAdvice.Memory(200_000, 150_000, 524_284, 140_000);
        assertEquals(73, roomy.swapUsedPercent());
        assertNull(RestartAdvice.reason(9 * DAY, roomy, 0));
        assertNull(RestartAdvice.reason(9 * DAY, null, 0));
        assertNull(RestartAdvice.reason(400 * DAY, new RestartAdvice.Memory(1, 1, 0, 0), 0));
    }

    @Test
    public void anUpdaterThatIsKeptFromRunningIsAReasonByItself() {
        assertEquals(
                "The updater has not been able to run for 45 minutes, which happens when memory runs short"
                        + " after 9 days without a restart",
                RestartAdvice.reason(9 * DAY, null, 45 * MINUTE_MS));
        assertEquals(
                "The updater has not been able to run for 3 hours, which happens when memory runs short",
                RestartAdvice.reason(DAY, null, 157 * MINUTE_MS));
        // Android starts the supervisor again now and then; that takes seconds, not half an hour.
        assertNull(RestartAdvice.reason(9 * DAY, null, 29 * MINUTE_MS));
    }

    @Test
    public void theProcessesThatHoldMostAreNamedLargestFirst() throws Exception {
        File proc = folder.newFolder("proc");
        process(proc, "361", "lowi-server\0", "Name:\tlowi-server\nVmRSS:\t    2524 kB\nVmSwap:\t  217508 kB\n");
        process(proc, "31693", "dev.mirror.repurpose:voice\0\0", "Name:\tpurpose:voice\nVmRSS:\t57136 kB\nVmSwap:\t95432 kB\n");
        process(proc, "31663", "dev.mirror.repurpose\0", "Name:\tmirror.repurpose\nVmRSS:\t82616 kB\nVmSwap:\t33980 kB\n");
        process(proc, "275", "/system/bin/mediaserver\0--flag\0", "Name:\tmediaserver\nVmRSS:\t1480 kB\n");
        // A thread of the kernel holds nothing that is counted, and has no command line.
        process(proc, "2", "", "Name:\tkthreadd\nState:\tS (sleeping)\n");
        process(proc, "9", "", "Name:\tnameless\nVmRSS:\t10 kB\nVmSwap:\t0 kB\n");
        new File(proc, "meminfo").createNewFile();
        JSONArray largest = RestartAdvice.largest(proc, 3);
        assertEquals(3, largest.length());
        assertEquals("lowi-server", largest.getJSONObject(0).getString("name"));
        assertEquals(217_508L, largest.getJSONObject(0).getLong("swapKb"));
        assertEquals("dev.mirror.repurpose:voice", largest.getJSONObject(1).getString("name"));
        assertEquals("dev.mirror.repurpose", largest.getJSONObject(2).getString("name"));
        JSONArray all = RestartAdvice.largest(proc, 9);
        assertEquals(5, all.length());
        assertEquals("mediaserver", all.getJSONObject(3).getString("name"));
        assertEquals(0L, all.getJSONObject(3).getLong("swapKb"));
        assertEquals("nameless", all.getJSONObject(4).getString("name"));
        assertEquals(0, RestartAdvice.largest(new File(proc, "absent"), 5).length());
    }

    private static void process(File proc, String pid, String commandLine, String status) throws Exception {
        File directory = new File(proc, pid);
        assertTrue(directory.mkdir());
        try (FileOutputStream out = new FileOutputStream(new File(directory, "cmdline"))) {
            out.write(commandLine.getBytes(StandardCharsets.UTF_8));
        }
        try (FileOutputStream out = new FileOutputStream(new File(directory, "status"))) {
            out.write(status.getBytes(StandardCharsets.UTF_8));
        }
    }
}
