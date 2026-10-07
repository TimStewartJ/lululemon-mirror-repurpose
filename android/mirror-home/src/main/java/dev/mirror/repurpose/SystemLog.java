package dev.mirror.repurpose;

import android.content.Context;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.FileReader;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Copies of Android's own log, taken at the moments that will be asked
 * about later and kept where a restart does not erase them.
 *
 * <p>Why a Mirror lost its network, and why it did not find it again, is
 * said in Android's log by the parts of Android that handle Wi-Fi, and
 * nowhere else. That log lives in memory, reaches back minutes on a Mirror
 * (its factory software fills it), and is gone after a restart. So at such
 * a moment Mirror Home copies what the log says of Wi-Fi, and the last
 * lines of everything, into a file.
 *
 * <p>Android lets an app read only its own lines unless it holds
 * {@code READ_LOGS}, which nobody but a person at a computer can give:
 * {@code adb shell pm grant dev.mirror.repurpose android.permission.READ_LOGS},
 * once, after which Mirror Home has to start again. Without it a copy holds
 * Mirror Home's own lines and says so.
 */
final class SystemLog {
    /** The copies kept of each sort; the oldest go first. */
    static final int KEEP = 12;
    /** The reason under which a copy is taken at the moment Wi-Fi is lost. */
    static final String LOSS = "wifi-lost";
    /** Of what is said of Wi-Fi, the newest this many bytes are kept. */
    static final int FILTERED_BYTES = 96 * 1024;
    /** Of everything else, this many bytes. */
    static final int TAIL_BYTES = 32 * 1024;
    static final int TAIL_LINES = 300;
    /** Android's group of those that may read the whole log. */
    static final int LOG_GROUP = 1007;

    private static final String TAG = "SystemLog";
    private static final long PATIENCE_MS = 15_000L;
    private static final Pattern NAME = Pattern.compile("log-([0-9]{1,19})-([a-z0-9-]{1,24})\\.txt");
    /** The parts of Android 6 that speak of Wi-Fi and of the network, and Mirror Home's own. */
    private static final String[] WIFI_TAGS = {
            "wpa_supplicant", "WifiStateMachine", "WifiConfigStore", "WifiAutoJoinController",
            "WifiService", "WifiController", "WifiMonitor", "WifiNative-wlan0", "WifiHAL",
            "WifiScanningService", "WifiWatchdogStateMachine", "SupplicantStateTracker",
            "WifiP2pService", "DhcpClient", "DhcpStateMachine", "dhcpcd", "ConnectivityService",
            "NetworkMonitor", "NetworkAgentInfo", "IpReachabilityMonitor", "Netd", "NetdConnector",
            "wcnss_service", "WCNSS_FILTER", "cnss-daemon", "QCNEJ", "lowi-server", "LOWI",
            "ScanGuard", "WifiKeeper", "WifiProvisioner", "WifiDirectOnboarding",
    };

    private static SystemLog instance;

    /*
     * The two commands are put together here and not where they are run. Android 6 compiles
     * everything but a class's initializer, and its compiler for x86 processors, which the
     * emulator has, writes wrong code for a list of more than five things made in one step:
     * the app is ended the moment that runs. See tools/dex_guard.py.
     */
    private static final List<String> WIFI_COMMAND = new ArrayList<>();
    private static final List<String> TAIL_COMMAND = new ArrayList<>();

    static {
        for (List<String> command : Arrays.asList(WIFI_COMMAND, TAIL_COMMAND)) {
            command.add("logcat");
            command.add("-d");
            command.add("-v");
            command.add("threadtime");
        }
        for (String tag : WIFI_TAGS) {
            WIFI_COMMAND.add(tag + ":V");
        }
        WIFI_COMMAND.add("*:S");
        TAIL_COMMAND.add("-t");
        TAIL_COMMAND.add(Integer.toString(TAIL_LINES));
    }

    private final File directory;
    private final String who;

    /** @param who names the build and the run in the head of each copy */
    SystemLog(File directory, String who) {
        this.directory = directory;
        this.who = who;
    }

    static synchronized SystemLog getInstance(Context context) {
        if (instance == null) {
            instance = new SystemLog(
                    new File(context.getApplicationContext().getFilesDir(), "journal/logs"),
                    "Mirror Home " + BuildConfig.VERSION_NAME + " (" + BuildConfig.VERSION_CODE + ")");
        }
        return instance;
    }

    /** Whether this process may read what other processes wrote to the log. */
    static boolean whole() {
        StringBuilder status = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new FileReader("/proc/self/status"))) {
            String text;
            while ((text = reader.readLine()) != null) {
                status.append(text).append('\n');
            }
        } catch (IOException | RuntimeException unreadable) {
            return false;
        }
        return member(status.toString(), LOG_GROUP);
    }

    /** Whether a process whose /proc status reads so belongs to a group. */
    static boolean member(String status, int group) {
        for (String text : status.split("\n")) {
            if (text.startsWith("Groups:")) {
                return Arrays.asList(text.substring(7).trim().split("\\s+")).contains(Integer.toString(group));
            }
        }
        return false;
    }

    /** What a reason becomes in a file name: small letters, digits and hyphens. */
    static String label(String reason) {
        String cleaned = (reason == null ? "" : reason).toLowerCase(Locale.US)
                .replaceAll("[^a-z0-9]+", "-")
                .replaceAll("^-+|-+$", "");
        if (cleaned.length() > 24) {
            cleaned = cleaned.substring(0, 24).replaceAll("-+$", "");
        }
        return cleaned.isEmpty() ? "asked" : cleaned;
    }

    /** The end of a text of at most so many bytes, begun at the start of a line. */
    static byte[] tail(byte[] text, int bytes) {
        if (text.length <= bytes) {
            return text;
        }
        int from = text.length - bytes;
        while (from < text.length && text[from - 1] != '\n') {
            from++;
        }
        return Arrays.copyOfRange(text, from, text.length);
    }

    /**
     * Copies the log now. Blocks for as long as Android takes to hand it
     * over, a second or two; not for the main thread.
     *
     * @param reason what happened, for the name of the copy
     * @return what {@link #list} says of the copy, or null if none could be made
     */
    synchronized JSONObject capture(String reason, long now) {
        boolean whole = whole();
        byte[] filtered = tail(run(WIFI_COMMAND), FILTERED_BYTES);
        byte[] last = tail(run(TAIL_COMMAND), TAIL_BYTES);
        String head = "# " + who + "\n"
                + "# " + new SimpleDateFormat("yyyy-MM-dd HH:mm:ss Z", Locale.US).format(new Date(now))
                + ", because: " + label(reason) + "\n"
                + (whole
                        ? "# Android's whole log.\n"
                        : "# Only Mirror Home's own lines: it has not been given READ_LOGS, or has not"
                                + " started again since.\n");
        File file = new File(directory, "log-" + now + "-" + label(reason) + ".txt");
        try {
            if (!directory.isDirectory() && !directory.mkdirs()) {
                throw new IOException("no room for the copies");
            }
            try (FileOutputStream out = new FileOutputStream(file)) {
                out.write(head.getBytes(StandardCharsets.UTF_8));
                out.write("\n# What the log says of Wi-Fi and the network\n".getBytes(StandardCharsets.UTF_8));
                out.write(filtered);
                out.write(("\n# The last " + TAIL_LINES + " lines of everything\n").getBytes(StandardCharsets.UTF_8));
                out.write(last);
                out.getFD().sync();
            }
            prune();
            return describe(file).put("whole", whole);
        } catch (IOException | JSONException | RuntimeException error) {
            Log.w(TAG, "Unable to keep a copy of the log: " + error);
            return null;
        }
    }

    /** The copies there are, oldest first: {name, at, reason, bytes}. */
    synchronized JSONArray list() throws JSONException {
        JSONArray result = new JSONArray();
        for (File file : copies()) {
            result.put(describe(file));
        }
        return result;
    }

    /** One copy, or null if there is none of that name. */
    synchronized byte[] read(String name) {
        if (name == null || !NAME.matcher(name).matches()) {
            return null;
        }
        File file = new File(directory, name);
        if (!file.isFile()) {
            return null;
        }
        try (InputStream in = new FileInputStream(file)) {
            return drain(in, FILTERED_BYTES + TAIL_BYTES + 8192);
        } catch (IOException | RuntimeException unreadable) {
            return null;
        }
    }

    private JSONObject describe(File file) throws JSONException {
        Matcher parts = NAME.matcher(file.getName());
        long at = 0L;
        String reason = "";
        if (parts.matches()) {
            at = Long.parseLong(parts.group(1));
            reason = parts.group(2);
        }
        return new JSONObject()
                .put("name", file.getName())
                .put("at", at)
                .put("reason", reason)
                .put("bytes", file.length());
    }

    private List<File> copies() {
        List<File> result = new ArrayList<>();
        File[] files = directory.listFiles();
        for (File file : files == null ? new File[0] : files) {
            if (NAME.matcher(file.getName()).matches()) {
                result.add(file);
            }
        }
        // The time of day leads each name, and has as many digits in every one for centuries to come.
        Collections.sort(result, (one, other) -> one.getName().compareTo(other.getName()));
        return result;
    }

    /**
     * Drops the oldest copies. Those taken at the moment of a loss are counted by themselves:
     * a network that comes and goes all day must not push out what was kept of a long outage.
     */
    private void prune() {
        int losses = 0;
        int others = 0;
        List<File> files = copies();
        for (int index = files.size() - 1; index >= 0; index--) {
            boolean loss = files.get(index).getName().endsWith("-" + LOSS + ".txt");
            int count = loss ? ++losses : ++others;
            if (count > KEEP && !files.get(index).delete()) {
                Log.w(TAG, "An old copy of the log would not go: " + files.get(index).getName());
            }
        }
    }

    /** What a command prints; what it has printed so far if it does not end in time. */
    private static byte[] run(List<String> command) {
        Process process = null;
        try {
            process = new ProcessBuilder(command).redirectErrorStream(true).start();
            process.getOutputStream().close();
            final Process running = process;
            Thread patience = new Thread(() -> {
                try {
                    Thread.sleep(PATIENCE_MS);
                    running.destroy();
                } catch (InterruptedException done) {
                    // The command ended by itself.
                }
            }, "system-log-patience");
            patience.setDaemon(true);
            patience.start();
            try (InputStream in = process.getInputStream()) {
                return drain(in, 8 * 1024 * 1024);
            } finally {
                patience.interrupt();
            }
        } catch (IOException | RuntimeException error) {
            return ("(" + command.get(0) + " could not be run: " + error + ")\n").getBytes(StandardCharsets.UTF_8);
        } finally {
            if (process != null) {
                process.destroy();
            }
        }
    }

    /** Reads to the end and keeps the newest so many bytes. */
    private static byte[] drain(InputStream in, int limit) throws IOException {
        ByteArrayOutputStream kept = new ByteArrayOutputStream();
        byte[] buffer = new byte[16 * 1024];
        int count;
        while ((count = in.read(buffer)) != -1) {
            kept.write(buffer, 0, count);
            if (kept.size() > 2 * limit) {
                byte[] all = kept.toByteArray();
                kept.reset();
                kept.write(all, all.length - limit, limit);
            }
        }
        byte[] all = kept.toByteArray();
        return all.length <= limit ? all : Arrays.copyOfRange(all, all.length - limit, all.length);
    }
}
