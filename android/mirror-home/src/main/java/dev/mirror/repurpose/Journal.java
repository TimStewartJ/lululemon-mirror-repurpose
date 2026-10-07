package dev.mirror.repurpose;

import android.content.Context;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import dev.mirror.repurpose.health.ProcessHealth;

/**
 * What happened on this Mirror, written where a restart does not erase it.
 *
 * <p>Android keeps its log in memory: switching a Mirror off and on, which
 * is how one that has stopped answering is brought back, erases the only
 * account of why it stopped. A Mirror that has lost its network cannot be
 * asked while it lasts, either. So Mirror Home writes down the few things
 * that later explain such a night: that it started and how the run before
 * ended, when Wi-Fi was lost and came back and what Android said of it in
 * between, and what Mirror Home did about it. One line each, in a file of
 * its own, to be read over the network afterwards.
 *
 * <p>The journal is two files of a fixed size: when one is full it becomes
 * the older one and the older one is dropped. Something that writes without
 * end is counted instead of written, so that it cannot push out the rest.
 */
final class Journal {
    /** Each of the two files holds this much; some two thousand lines together. */
    static final long FILE_BYTES = 256 * 1024L;
    /** More lines than this within {@link #BURST_WINDOW_MS} are counted, not written. */
    static final int BURST = 120;
    static final long BURST_WINDOW_MS = 10 * 60_000L;
    static final int LINE_BYTES = 4096;

    private static final String TAG = "Journal";
    private static final String CURRENT = "journal.jsonl";
    private static final String OLDER = "journal.1.jsonl";

    private static Journal instance;
    private static final ExecutorService WRITER = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "journal");
        thread.setDaemon(true);
        return thread;
    });

    private final File current;
    private final File older;
    private final long fileBytes;
    private final String boot;
    private final long run;

    private long windowStartedAt = Long.MIN_VALUE;
    private int inWindow;
    private int leftOut;
    private long leftOutInAll;
    private long written;
    private long failures;
    private boolean measured;
    private boolean endChecked;
    private long olderLines;
    private long currentLines;
    private long oldestAt;
    private long newestAt;

    /**
     * @param boot different for every start of Android; empty if unknown
     * @param run the number of this run of Mirror Home
     */
    Journal(File directory, long fileBytes, String boot, long run) {
        current = new File(directory, CURRENT);
        older = new File(directory, OLDER);
        this.fileBytes = fileBytes;
        this.boot = boot == null ? "" : boot;
        this.run = run;
        if (!directory.isDirectory() && !directory.mkdirs()) {
            Log.w(TAG, "Unable to make room for the journal");
        }
    }

    /** The journal of this process, or null in a process that keeps none. */
    static synchronized Journal get(Context context) {
        if (instance == null) {
            ProcessHealth health = ProcessHealth.get();
            if (health == null) {
                // The recogniser's process: its runs are not the dashboard's.
                return null;
            }
            String boot = health.bootId();
            instance = new Journal(
                    new File(context.getApplicationContext().getFilesDir(), "journal"),
                    FILE_BYTES,
                    boot == null ? "" : boot.substring(0, Math.min(8, boot.length())),
                    health.runId());
        }
        return instance;
    }

    /**
     * Writes one line, away from the calling thread. The time is taken now,
     * so lines stand in the order in which things happened.
     *
     * @param kind what it is about: "home", "wifi", "log"
     * @param what what happened, in a word or two
     * @param more whatever explains it later; may be null
     */
    static void note(Context context, String kind, String what, JSONObject more) {
        Journal journal = get(context);
        if (journal == null) {
            return;
        }
        long at = System.currentTimeMillis();
        long up = SystemClock.elapsedRealtime();
        WRITER.execute(() -> journal.record(at, up, kind, what, more));
    }

    /**
     * Writes one line and waits until it is on the flash.
     *
     * @param at the time of day, in milliseconds since 1970
     * @param up for how long Android has been running, in milliseconds
     */
    synchronized void record(long at, long up, String kind, String what, JSONObject more) {
        if (windowStartedAt == Long.MIN_VALUE || up - windowStartedAt >= BURST_WINDOW_MS || up < windowStartedAt) {
            int missing = leftOut;
            windowStartedAt = up;
            inWindow = 0;
            leftOut = 0;
            if (missing > 0) {
                append(line(at, up, "journal", "left-out", count(missing)));
            }
        }
        if (inWindow >= BURST) {
            leftOut++;
            leftOutInAll++;
            return;
        }
        inWindow++;
        append(line(at, up, kind, what, more));
    }

    /**
     * The lines that match, oldest first.
     *
     * @param since only lines of this time of day or later; 0 for all
     * @param kind only lines of this kind; null or empty for all
     * @param limit at most this many, the newest
     */
    synchronized JSONArray read(long since, String kind, int limit) {
        List<JSONObject> found = new ArrayList<>();
        for (File file : new File[]{older, current}) {
            if (!file.isFile()) {
                continue;
            }
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8))) {
                String text;
                while ((text = reader.readLine()) != null) {
                    JSONObject entry;
                    try {
                        entry = new JSONObject(text);
                    } catch (JSONException torn) {
                        // The line that was being written when the power went.
                        continue;
                    }
                    if (entry.optLong("at") >= since
                            && (kind == null || kind.isEmpty() || kind.equals(entry.optString("kind")))) {
                        found.add(entry);
                    }
                }
            } catch (IOException | RuntimeException unreadable) {
                Log.w(TAG, "Unable to read " + file.getName(), unreadable);
            }
        }
        JSONArray result = new JSONArray();
        for (int index = Math.max(0, found.size() - Math.max(0, limit)); index < found.size(); index++) {
            result.put(found.get(index));
        }
        return result;
    }

    /** How much there is and how far back it reaches, for the health report. */
    synchronized JSONObject summary() throws JSONException {
        if (!measured) {
            // Once per run; from then on every line written is counted as it goes.
            measured = true;
            long[] before = extent(older);
            long[] now = extent(current);
            olderLines = before[0];
            currentLines = now[0];
            oldestAt = before[0] > 0 ? before[1] : now[1];
            newestAt = now[0] > 0 ? now[2] : before[2];
        }
        return new JSONObject()
                .put("lines", olderLines + currentLines)
                .put("bytes", current.length() + older.length())
                .put("oldestAt", oldestAt == 0L ? JSONObject.NULL : (Object) oldestAt)
                .put("newestAt", newestAt == 0L ? JSONObject.NULL : (Object) newestAt)
                .put("writtenThisRun", written)
                .put("leftOut", leftOutInAll)
                .put("failures", failures);
    }

    /** {lines, time of the first, time of the last} of one file; zeros if there is none. */
    private static long[] extent(File file) {
        long[] result = new long[3];
        if (!file.isFile()) {
            return result;
        }
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(new FileInputStream(file), StandardCharsets.UTF_8))) {
            String text;
            while ((text = reader.readLine()) != null) {
                long at = time(text);
                if (at == 0L) {
                    continue;
                }
                result[0]++;
                if (result[1] == 0L) {
                    result[1] = at;
                }
                result[2] = at;
            }
        } catch (IOException | RuntimeException unreadable) {
            Log.w(TAG, "Unable to read " + file.getName(), unreadable);
        }
        return result;
    }

    /** The time of a line, read without taking the line apart; 0 if it has none. */
    private static long time(String text) {
        String start = "{\"at\":";
        if (!text.startsWith(start) || !text.endsWith("}")) {
            return 0L;
        }
        int end = start.length();
        while (end < text.length() && Character.isDigit(text.charAt(end))) {
            end++;
        }
        try {
            return end == start.length() ? 0L : Long.parseLong(text.substring(start.length(), end));
        } catch (NumberFormatException tooLong) {
            return 0L;
        }
    }

    /** One line, with the time first so that it can be read without taking the line apart. */
    private String line(long at, long up, String kind, String what, JSONObject more) {
        String head = "{\"at\":" + at
                + ",\"up\":" + up
                + ",\"boot\":" + JSONObject.quote(boot)
                + ",\"run\":" + run
                + ",\"kind\":" + JSONObject.quote(kind)
                + ",\"what\":" + JSONObject.quote(what);
        if (more == null || more.length() == 0) {
            return head + "}";
        }
        String text = head + ",\"more\":" + more + "}";
        return text.getBytes(StandardCharsets.UTF_8).length > LINE_BYTES
                ? head + ",\"more\":{\"tooLong\":true}}"
                : text;
    }

    private void append(String text) {
        byte[] bytes = (text + "\n").getBytes(StandardCharsets.UTF_8);
        try {
            if (current.length() + bytes.length > fileBytes && current.length() > 0) {
                if (older.exists() && !older.delete()) {
                    throw new IOException("the older file would not go");
                }
                if (!current.renameTo(older)) {
                    throw new IOException("the full file would not move");
                }
                // What was counted of the two files no longer holds; count again when asked.
                measured = false;
            }
            try (FileOutputStream out = new FileOutputStream(current, true)) {
                if (!endChecked) {
                    endChecked = true;
                    if (cutShort(current)) {
                        // The power went in the middle of a line; what follows must not be joined to it.
                        out.write('\n');
                    }
                }
                out.write(bytes);
                out.flush();
                // The next thing that happens may be the power going.
                out.getFD().sync();
            }
            written++;
            if (measured) {
                long at = time(text);
                currentLines++;
                newestAt = at;
                if (oldestAt == 0L) {
                    oldestAt = at;
                }
            }
        } catch (IOException | RuntimeException error) {
            failures++;
            Log.w(TAG, "Unable to write the journal: " + error);
        }
    }

    /** Whether a file ends in the middle of a line. */
    private static boolean cutShort(File file) throws IOException {
        if (file.length() == 0) {
            return false;
        }
        try (RandomAccessFile end = new RandomAccessFile(file, "r")) {
            end.seek(end.length() - 1);
            return end.read() != '\n';
        }
    }

    private static JSONObject count(int lines) {
        try {
            return new JSONObject().put("lines", lines);
        } catch (JSONException impossible) {
            return new JSONObject();
        }
    }
}
