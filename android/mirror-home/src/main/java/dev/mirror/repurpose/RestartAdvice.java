package dev.mirror.repurpose;

import android.content.Context;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/**
 * Whether the Mirror should be switched off and on, and why.
 *
 * <p>A Mirror has less than a gigabyte of memory, and a daemon of its
 * factory software grows by some twenty megabytes a day. After about nine
 * days with voice commands on, one Mirror had filled four fifths of its
 * compressed swap. Its kernel then ended a background process every six
 * seconds, the OTA supervisor among them, which could no longer install or
 * roll back anything, while Android went on reporting 300 MB as available:
 * it counts memory that the kernel cannot use for this. Nothing that an app
 * may do ends such a daemon, and only a restart gives the memory back. So
 * Mirror Home reads what the kernel itself says, and tells whoever looks.
 */
final class RestartAdvice {
    /** How often the supervisor is looked for, so that its silence can be timed. */
    static final long SAMPLE_INTERVAL_MS = 5 * 60_000L;

    /** Android restarts the supervisor now and then; half an hour of silence is something else. */
    static final long SUPERVISOR_SILENT_MS = 30 * 60_000L;
    /** A swap that fills within days of starting is in use, not running out. */
    static final long LEAST_UPTIME_SECONDS = 3 * 24 * 3600L;
    static final int SWAP_USED_PERCENT = 75;
    private static final int LARGEST = 5;

    /** Once a restart is advised it stays so: the cause does not go away by itself. */
    private static volatile String standing;

    private RestartAdvice() {
    }

    /** What the kernel says of memory, in kilobytes; -1 where it says nothing. */
    static final class Memory {
        final long freeKb;
        final long cachedKb;
        final long swapTotalKb;
        final long swapFreeKb;

        Memory(long freeKb, long cachedKb, long swapTotalKb, long swapFreeKb) {
            this.freeKb = freeKb;
            this.cachedKb = cachedKb;
            this.swapTotalKb = swapTotalKb;
            this.swapFreeKb = swapFreeKb;
        }

        /** How much of the swap is in use, in percent; 0 without swap. */
        int swapUsedPercent() {
            if (swapTotalKb <= 0 || swapFreeKb < 0) {
                return 0;
            }
            return (int) Math.round(100.0 * (swapTotalKb - swapFreeKb) / swapTotalKb);
        }

        JSONObject toJson() throws JSONException {
            return new JSONObject()
                    .put("freeKb", number(freeKb))
                    .put("cachedKb", number(cachedKb))
                    .put("swapTotalKb", number(swapTotalKb))
                    .put("swapFreeKb", number(swapFreeKb));
        }

        private static Object number(long value) {
            return value < 0 ? JSONObject.NULL : (Object) value;
        }
    }

    /** Reads the lines of /proc/meminfo that matter here. */
    static Memory parse(String meminfo) {
        return new Memory(
                field(meminfo, "MemFree"),
                field(meminfo, "Cached"),
                field(meminfo, "SwapTotal"),
                field(meminfo, "SwapFree"));
    }

    /**
     * The reason to restart, or null if there is none.
     *
     * @param uptimeSeconds how long the device has been up
     * @param memory what the kernel says of memory, or null if it could not be read
     * @param supervisorSilentMs for how long an installed OTA supervisor has not answered; 0 if it
     *     answers or is not installed
     */
    static String reason(long uptimeSeconds, Memory memory, long supervisorSilentMs) {
        // "After 0 days" says nothing; a Mirror that young is short of memory for another reason.
        String running = uptimeSeconds < 36 * 3600L
                ? ""
                : " after " + days(uptimeSeconds) + " without a restart";
        if (supervisorSilentMs >= SUPERVISOR_SILENT_MS) {
            return "The updater has not been able to run for " + span(supervisorSilentMs)
                    + ", which happens when memory runs short" + running;
        }
        if (memory != null
                && uptimeSeconds >= LEAST_UPTIME_SECONDS
                && memory.swapUsedPercent() >= SWAP_USED_PERCENT) {
            return "Memory is running short" + running;
        }
        return null;
    }

    /** Looks for the supervisor, which blocks briefly. Not for the main thread. */
    static void sample(Context context) {
        try {
            SupervisorProbe.check(context);
        } catch (JSONException impossible) {
            // Numbers and strings always fit.
        }
        current();
    }

    /** {advised, reason}, for the status and the health report. Reads one small file. */
    static JSONObject snapshot() throws JSONException {
        String reason = current();
        return new JSONObject()
                .put("advised", reason != null)
                .put("reason", reason == null ? JSONObject.NULL : reason);
    }

    /** What the kernel says of memory and which processes hold most, for the health report. */
    static void describe(JSONObject memory) throws JSONException {
        Memory kernel = read();
        memory.put("kernel", kernel == null ? JSONObject.NULL : kernel.toJson());
        memory.put("largest", largest(new File("/proc"), LARGEST));
    }

    private static String current() {
        if (standing == null) {
            standing = reason(
                    SystemClock.elapsedRealtime() / 1000L,
                    read(),
                    SupervisorProbe.silentForMs(System.currentTimeMillis()));
        }
        return standing;
    }

    private static Memory read() {
        String meminfo = text(new File("/proc/meminfo"), 4096);
        return meminfo.isEmpty() ? null : parse(meminfo);
    }

    /**
     * The processes that hold most memory, in RAM and in swap together,
     * among those an app may look at. Stock Android 6 shows it every
     * process; a Mirror's shows it apps and none of the factory software's
     * daemons; later versions show an app only itself. The list is as short
     * as that makes it.
     */
    static JSONArray largest(File proc, int count) throws JSONException {
        List<long[]> sizes = new ArrayList<>();
        List<String> names = new ArrayList<>();
        File[] entries = proc.listFiles();
        for (File entry : entries == null ? new File[0] : entries) {
            if (!entry.getName().matches("[0-9]+")) {
                continue;
            }
            String status = text(new File(entry, "status"), 4096);
            long rss = field(status, "VmRSS");
            if (rss < 0) {
                // A thread of the kernel, or a process that may not be read.
                continue;
            }
            long swap = Math.max(0, field(status, "VmSwap"));
            String command = text(new File(entry, "cmdline"), 256);
            int end = command.indexOf('\0');
            String name = end < 0 ? command : command.substring(0, end);
            name = name.substring(name.lastIndexOf('/') + 1).trim();
            if (name.isEmpty()) {
                name = line(status, "Name");
            }
            sizes.add(new long[]{rss, swap, names.size()});
            names.add(name);
        }
        Collections.sort(sizes, (one, other) -> Long.compare(other[0] + other[1], one[0] + one[1]));
        JSONArray result = new JSONArray();
        for (int index = 0; index < sizes.size() && index < count; index++) {
            long[] size = sizes.get(index);
            result.put(new JSONObject()
                    .put("name", names.get((int) size[2]))
                    .put("rssKb", size[0])
                    .put("swapKb", size[1]));
        }
        return result;
    }

    /** The number of kilobytes on a line such as "VmRSS:    2524 kB"; -1 if there is none. */
    private static long field(String text, String name) {
        String value = line(text, name);
        int end = 0;
        while (end < value.length() && Character.isDigit(value.charAt(end))) {
            end++;
        }
        try {
            return end == 0 ? -1 : Long.parseLong(value.substring(0, end));
        } catch (NumberFormatException tooLong) {
            return -1;
        }
    }

    /** What follows "Name:" on its line, trimmed; empty if there is no such line. */
    private static String line(String text, String name) {
        String prefix = name + ":";
        int at = text.startsWith(prefix) ? 0 : text.indexOf("\n" + prefix);
        if (at < 0) {
            return "";
        }
        int start = text.indexOf(':', at) + 1;
        int end = text.indexOf('\n', start);
        return text.substring(start, end < 0 ? text.length() : end).trim();
    }

    private static String text(File file, int limit) {
        try (InputStream in = new FileInputStream(file)) {
            byte[] buffer = new byte[limit];
            int length = 0;
            int read;
            while (length < limit && (read = in.read(buffer, length, limit - length)) > 0) {
                length += read;
            }
            return new String(buffer, 0, length, StandardCharsets.UTF_8);
        } catch (IOException | RuntimeException unreadable) {
            return "";
        }
    }

    private static String days(long seconds) {
        return String.format(Locale.US, "%d days", Math.round(seconds / 86_400.0));
    }

    private static String span(long millis) {
        long minutes = millis / 60_000L;
        if (minutes < 90) {
            return minutes + " minutes";
        }
        return String.format(Locale.US, "%d hours", Math.round(minutes / 60.0));
    }
}
