package dev.mirror.repurpose.health;

import android.app.ActivityManager;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Debug;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.os.StatFs;
import android.os.SystemClock;
import android.system.ErrnoException;
import android.system.Os;
import android.system.OsConstants;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileReader;
import java.io.IOException;
import java.lang.reflect.Field;

/**
 * Keeps the evidence a restart would otherwise erase: when this process
 * started, how the one before it ended, and the last uncaught exception.
 * An appliance nobody can attach a debugger to has to remember its own
 * failures for them to be diagnosed later over the network.
 */
public final class ProcessHealth {
    private static final String TAG = "ProcessHealth";
    private static final String PREFERENCES = "process_health";
    private static final String KEY_RUN_ID = "run_id";
    private static final String KEY_RUN_STARTED_AT = "run_started_at";
    private static final String KEY_RUN_STARTED_ELAPSED = "run_started_elapsed";
    private static final String KEY_RUN_BOOT_ID = "run_boot_id";
    private static final String KEY_RUN_VERSION_CODE = "run_version_code";
    private static final String KEY_RUN_VERSION_NAME = "run_version_name";
    private static final String KEY_LAST_SEEN_AT = "last_seen_at";
    private static final String KEY_LAST_SEEN_ELAPSED = "last_seen_elapsed";
    private static final String KEY_PREVIOUS_RUN = "previous_run";
    private static final String KEY_EARLY_STOPS = "early_stops";
    private static final String KEY_CRASH_COUNT = "crash_count";
    private static final String KEY_CRASH_RUN_ID = "crash_run_id";
    private static final String KEY_LAST_CRASH = "last_crash";
    private static final long HEARTBEAT_MS = 10 * 60 * 1000L;
    private static final String BOOT_ID_FILE = "/proc/sys/kernel/random/boot_id";
    private static final int MAX_PROBED_DESCRIPTORS = 4096;

    private static volatile ProcessHealth instance;

    private final Context context;
    private final SharedPreferences preferences;
    private final String versionName;
    private final long versionCode;
    private final long runId;
    private final long startedAt;
    private final long startedElapsed;
    private final String bootId;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable heartbeat = new Runnable() {
        @Override
        public void run() {
            preferences.edit()
                    .putLong(KEY_LAST_SEEN_AT, System.currentTimeMillis())
                    .putLong(KEY_LAST_SEEN_ELAPSED, SystemClock.elapsedRealtime())
                    .apply();
            handler.postDelayed(this, HEARTBEAT_MS);
        }
    };

    private volatile int trimCount;
    private volatile int lastTrimLevel = -1;
    private volatile long lastTrimAt;

    private ProcessHealth(Context context, String versionName, long versionCode) {
        this.context = context.getApplicationContext();
        this.versionName = versionName;
        this.versionCode = versionCode;
        preferences = this.context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
        startedAt = System.currentTimeMillis();
        startedElapsed = SystemClock.elapsedRealtime();
        bootId = readBootId();

        long previousRunId = preferences.getLong(KEY_RUN_ID, 0L);
        runId = previousRunId + 1;
        SharedPreferences.Editor editor = preferences.edit()
                .putLong(KEY_RUN_ID, runId)
                .putLong(KEY_RUN_STARTED_AT, startedAt)
                .putLong(KEY_RUN_STARTED_ELAPSED, startedElapsed)
                .putString(KEY_RUN_BOOT_ID, bootId)
                .putLong(KEY_RUN_VERSION_CODE, versionCode)
                .putString(KEY_RUN_VERSION_NAME, versionName)
                .putLong(KEY_LAST_SEEN_AT, startedAt)
                .putLong(KEY_LAST_SEEN_ELAPSED, startedElapsed);
        if (previousRunId > 0) {
            String end = previousEnd(previousRunId);
            if (RunHistory.stoppedWhileStarting(
                    end,
                    preferences.getLong(KEY_RUN_STARTED_ELAPSED, -1L),
                    startedElapsed)) {
                // Keep describing the run before it, and count this one.
                editor.putInt(KEY_EARLY_STOPS, preferences.getInt(KEY_EARLY_STOPS, 0) + 1);
            } else {
                editor.putString(KEY_PREVIOUS_RUN, describePreviousRun(previousRunId, end))
                        .putInt(KEY_EARLY_STOPS, 0);
            }
        }
        if (!editor.commit()) {
            Log.w(TAG, "Unable to record this process start");
        }

        Thread.UncaughtExceptionHandler downstream =
                Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, error) -> {
            try {
                recordCrash(thread, error);
            } catch (Throwable ignored) {
                // Recording must never replace the crash being reported.
            }
            if (downstream != null) {
                downstream.uncaughtException(thread, error);
            } else {
                Process.killProcess(Process.myPid());
                System.exit(10);
            }
        });
        handler.postDelayed(heartbeat, HEARTBEAT_MS);
    }

    /** Starts recording for this process; later calls return the same instance. */
    public static ProcessHealth start(Context context, String versionName, long versionCode) {
        ProcessHealth health = instance;
        if (health == null) {
            synchronized (ProcessHealth.class) {
                health = instance;
                if (health == null) {
                    health = new ProcessHealth(context, versionName, versionCode);
                    instance = health;
                }
            }
        }
        return health;
    }

    /** The running recorder, or null before {@link #start}. */
    public static ProcessHealth get() {
        return instance;
    }

    public void recordTrimMemory(int level) {
        trimCount++;
        lastTrimLevel = level;
        lastTrimAt = System.currentTimeMillis();
    }

    public JSONObject snapshot() throws JSONException {
        long now = System.currentTimeMillis();
        return new JSONObject()
                .put("process", new JSONObject()
                        .put("pid", Process.myPid())
                        .put("runId", runId)
                        .put("startedAt", startedAt)
                        .put(
                                "uptimeSeconds",
                                (SystemClock.elapsedRealtime() - startedElapsed) / 1000L)
                        .put("previousRun", stored(KEY_PREVIOUS_RUN))
                        .put("earlyStops", preferences.getInt(KEY_EARLY_STOPS, 0)))
                .put("crashes", new JSONObject()
                        .put("count", preferences.getLong(KEY_CRASH_COUNT, 0L))
                        .put("last", stored(KEY_LAST_CRASH)))
                .put("memory", memory())
                .put("storage", storage())
                .put("device", new JSONObject()
                        .put("bootedAt", now - SystemClock.elapsedRealtime())
                        .put("bootId", bootId == null ? JSONObject.NULL : bootId)
                        .put("uptimeSeconds", SystemClock.elapsedRealtime() / 1000L));
    }

    private String previousEnd(long previousRunId) {
        return RunHistory.previousEnd(
                preferences.getLong(KEY_CRASH_RUN_ID, -1L) == previousRunId,
                preferences.getLong(KEY_RUN_VERSION_CODE, versionCode),
                versionCode,
                RunHistory.rebooted(
                        preferences.getString(KEY_RUN_BOOT_ID, null),
                        bootId,
                        preferences.getLong(KEY_LAST_SEEN_ELAPSED, 0L),
                        startedElapsed));
    }

    private String describePreviousRun(long previousRunId, String end) {
        try {
            return new JSONObject()
                    .put("runId", previousRunId)
                    .put("startedAt", preferences.getLong(KEY_RUN_STARTED_AT, 0L))
                    .put("lastSeenAt", preferences.getLong(KEY_LAST_SEEN_AT, 0L))
                    .put("versionName", preferences.getString(KEY_RUN_VERSION_NAME, ""))
                    .put("end", end)
                    .toString();
        } catch (JSONException impossible) {
            return "{}";
        }
    }

    private void recordCrash(Thread thread, Throwable error) throws JSONException {
        JSONObject crash = RunHistory.describeCrash(
                error,
                thread == null ? "" : thread.getName(),
                System.currentTimeMillis(),
                runId,
                versionName,
                versionCode,
                SystemClock.elapsedRealtime() - startedElapsed);
        // Synchronous on purpose: the process is about to die.
        boolean saved = preferences.edit()
                .putString(KEY_LAST_CRASH, crash.toString())
                .putLong(KEY_CRASH_RUN_ID, runId)
                .putLong(KEY_CRASH_COUNT, preferences.getLong(KEY_CRASH_COUNT, 0L) + 1)
                .putLong(KEY_LAST_SEEN_AT, System.currentTimeMillis())
                .putLong(KEY_LAST_SEEN_ELAPSED, SystemClock.elapsedRealtime())
                .commit();
        if (!saved) {
            Log.w(TAG, "Unable to record the crash");
        }
    }

    private Object stored(String key) {
        String serialized = preferences.getString(key, null);
        if (serialized == null) {
            return JSONObject.NULL;
        }
        try {
            return new JSONObject(serialized);
        } catch (JSONException error) {
            return JSONObject.NULL;
        }
    }

    private JSONObject memory() throws JSONException {
        Runtime runtime = Runtime.getRuntime();
        int openFiles = openFileCount();
        JSONObject result = new JSONObject()
                .put("javaHeapUsedKb", (runtime.totalMemory() - runtime.freeMemory()) / 1024L)
                .put("javaHeapMaxKb", runtime.maxMemory() / 1024L)
                .put("nativeHeapKb", Debug.getNativeHeapAllocatedSize() / 1024L)
                .put("threads", Thread.activeCount())
                .put("openFiles", openFiles < 0 ? JSONObject.NULL : openFiles)
                .put("trimEvents", trimCount)
                .put("lastTrimLevel", lastTrimLevel < 0 ? JSONObject.NULL : lastTrimLevel)
                .put("lastTrimAt", lastTrimAt == 0 ? JSONObject.NULL : lastTrimAt);
        ActivityManager manager =
                (ActivityManager) context.getSystemService(Context.ACTIVITY_SERVICE);
        if (manager != null) {
            try {
                ActivityManager.MemoryInfo system = new ActivityManager.MemoryInfo();
                manager.getMemoryInfo(system);
                result.put("systemAvailableKb", system.availMem / 1024L)
                        .put("systemTotalKb", system.totalMem / 1024L)
                        .put("systemLow", system.lowMemory);
                Debug.MemoryInfo[] process =
                        manager.getProcessMemoryInfo(new int[]{Process.myPid()});
                if (process != null && process.length == 1) {
                    result.put("pssKb", process[0].getTotalPss());
                }
            } catch (RuntimeException error) {
                Log.w(TAG, "Unable to read memory statistics", error);
            }
        }
        return result;
    }

    private JSONObject storage() throws JSONException {
        JSONObject result = new JSONObject();
        try {
            StatFs data = new StatFs(context.getFilesDir().getAbsolutePath());
            result.put("dataFreeBytes", data.getAvailableBytes())
                    .put("dataTotalBytes", data.getTotalBytes());
        } catch (RuntimeException error) {
            Log.w(TAG, "Unable to read storage statistics", error);
        }
        return result;
    }

    /** Different for every boot of the device; null where it cannot be read. */
    private static String readBootId() {
        try (BufferedReader reader = new BufferedReader(new FileReader(BOOT_ID_FILE))) {
            String line = reader.readLine();
            String value = line == null ? "" : line.trim();
            return value.isEmpty() || value.length() > 64 ? null : value;
        } catch (IOException | RuntimeException unreadable) {
            return null;
        }
    }

    /**
     * How many descriptors this process holds, or -1 when that cannot be told.
     * A count that climbs over weeks of uptime is the signature of a leak.
     *
     * <p>The kernel is asked about each descriptor number in turn. Listing
     * /proc/self/fd would be simpler, but the Mirror's kernel hides that
     * directory from a process that is not debuggable. Asking only looks:
     * it never opens, duplicates or closes a descriptor.
     */
    private static int openFileCount() {
        try {
            Field number = FileDescriptor.class.getDeclaredField("descriptor");
            number.setAccessible(true);
            FileDescriptor probe = new FileDescriptor();
            long limit = Os.sysconf(OsConstants._SC_OPEN_MAX);
            int end = (int) Math.max(0L, Math.min(limit, MAX_PROBED_DESCRIPTORS));
            int open = 0;
            for (int candidate = 0; candidate < end; candidate++) {
                number.setInt(probe, candidate);
                try {
                    Os.fstat(probe);
                    open++;
                } catch (ErrnoException notOpen) {
                    // No descriptor has this number.
                }
            }
            number.setInt(probe, -1);
            return open;
        } catch (ReflectiveOperationException | RuntimeException unavailable) {
            String[] entries = new File("/proc/self/fd").list();
            return entries == null ? -1 : entries.length;
        }
    }
}
