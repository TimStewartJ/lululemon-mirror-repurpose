package dev.mirror.repurpose;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.wifi.SupplicantState;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Keeps Android 6 from scanning for other Wi-Fi networks while it is
 * connected to one.
 *
 * <p>A Mirror hangs on a wall and stays on one network, yet Android goes on
 * scanning every few minutes for a better one. Each scan passes through a
 * daemon of the Mirror's factory software, {@code lowi-server}, which keeps
 * a little more memory every time and never gives it back: about twenty
 * megabytes a day. The kernel may not end that daemon, so after some nine
 * days it ends everything else in the background instead, the OTA
 * supervisor among it. With the scans stopped for an hour on one Mirror the
 * daemon did not grow by a byte and used no processor time; with them back
 * it grew as before. A Mirror does not let an app look at the daemon, so
 * what the guard reports is the scans themselves: none while connected is
 * the guard at work.
 *
 * <p>Android 6 has a switch for this, out of an app's ordinary reach but
 * open to one that may change the Wi-Fi state. It also stops Android from
 * moving to another saved network while connected. Scanning and joining
 * when there is no connection are not governed by the switch; and so that
 * this never has to be relied on, the guard hands the switch back for as
 * long as Wi-Fi has no network, and takes it again once it has one. A
 * Mirror that loses its network therefore looks for it with Android
 * exactly as it came. The switch lasts until Android restarts, so the
 * guard sets it again after every start and looks now and then whether it
 * still holds.
 *
 * <p>Off unless the owner turns it on, and only where the switch exists.
 */
final class ScanGuard {
    static final String STATE_OFF = "off";
    static final String STATE_APPLIED = "applied";
    static final String STATE_WAITING = "waiting";
    static final String STATE_ERROR = "error";

    /** A scan that Android had begun before the switch was set may end this long after. */
    static final long SETTLE_MS = 60_000L;

    private static final String TAG = "ScanGuard";

    /** Android's switch for scanning while connected. */
    interface Switch {
        /** Whether this Android has the switch at all. */
        boolean present();

        /** Whether Wi-Fi is switched on and has no network, so that Android is looking for one. */
        boolean searching() throws Exception;

        /** Whether Android scans while connected. */
        boolean scanning() throws Exception;

        /** Asks Android to scan, or not to scan, while connected. */
        void scan(boolean wanted) throws Exception;
    }

    /** What has to outlive this process. */
    interface Memory {
        boolean wanted();

        void want(boolean wanted);

        /** What {@link #keep} was last given; empty if nothing. */
        String kept();

        void keep(String value);
    }

    private static ScanGuard instance;

    private final Switch android;
    private final Memory memory;
    private final String boot;
    private final ExecutorService worker = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "scan-guard");
        thread.setDaemon(true);
        return thread;
    });

    private String state = STATE_OFF;
    private String detail = "";
    private Boolean scanning;
    private long appliedAt;
    private long checkedAt;
    private int applied;
    private int checks;
    private int scans;
    private int scansSinceApplied;
    private long lastScanAt;

    /**
     * @param boot different for every start of Android, so that what Android was doing before
     *     the guard first touched it is not taken from an earlier start; null if unknown
     */
    ScanGuard(Switch android, Memory memory, String boot) {
        this.android = android;
        this.memory = memory;
        this.boot = boot == null || boot.isEmpty() ? "unknown" : boot;
    }

    static synchronized ScanGuard getInstance(Context context) {
        if (instance == null) {
            Context application = context.getApplicationContext();
            instance = new ScanGuard(
                    new AndroidSwitch(application),
                    new Stored(new ConfigStore(application)),
                    line(new File("/proc/sys/kernel/random/boot_id")));
        }
        return instance;
    }

    /** Sets the switch as the owner wants it, now and whenever Wi-Fi comes up again. */
    void start(Context context) {
        IntentFilter events = new IntentFilter();
        events.addAction(WifiManager.WIFI_STATE_CHANGED_ACTION);
        events.addAction(WifiManager.SUPPLICANT_CONNECTION_CHANGE_ACTION);
        events.addAction(WifiManager.NETWORK_STATE_CHANGED_ACTION);
        context.getApplicationContext().registerReceiver(new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                checkSoon();
            }
        }, events);
        final WifiManager wifi = (WifiManager) context.getApplicationContext()
                .getSystemService(Context.WIFI_SERVICE);
        context.getApplicationContext().registerReceiver(new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                WifiInfo info = wifi == null ? null : wifi.getConnectionInfo();
                scanned(
                        info != null && info.getNetworkId() >= 0
                                && info.getSupplicantState() == SupplicantState.COMPLETED,
                        System.currentTimeMillis());
            }
        }, new IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION));
        checkSoon();
    }

    /** Looks whether the switch stands as wanted and sets it if not, away from this thread. */
    void checkSoon() {
        worker.execute(() -> {
            try {
                check(System.currentTimeMillis());
            } catch (RuntimeException error) {
                Log.w(TAG, "Unable to check the scan guard", error);
            }
        });
    }

    boolean supported() {
        return android.present();
    }

    /**
     * Turns the guard on or off and does it at once.
     *
     * @throws IllegalStateException if it is to be turned on where Android has no such switch
     */
    synchronized void setEnabled(boolean enabled, long now) {
        if (enabled && !android.present()) {
            throw new IllegalStateException(
                    "This Android has no switch for scanning while connected");
        }
        memory.want(enabled);
        check(now);
    }

    /** Looks whether the switch stands as wanted, and sets it if not. Blocks briefly. */
    synchronized void check(long now) {
        checks++;
        checkedAt = now;
        if (!android.present()) {
            state = STATE_OFF;
            detail = "";
            scanning = null;
            return;
        }
        try {
            if (memory.wanted()) {
                hold(now);
            } else {
                release();
            }
        } catch (Exception error) {
            Throwable cause = error instanceof InvocationTargetException && error.getCause() != null
                    ? error.getCause()
                    : error;
            state = STATE_ERROR;
            detail = cause.getClass().getSimpleName()
                    + (cause.getMessage() == null ? "" : ": " + cause.getMessage());
            scanning = null;
            Log.w(TAG, "The scan guard could not use Android's switch: " + detail);
        }
    }

    private void hold(long now) throws Exception {
        boolean found = android.scanning();
        if (android.searching()) {
            // Without a network there is nothing to guard, and nothing may stand in the way of finding one.
            if (mine() && found != before()) {
                android.scan(before());
                found = android.scanning();
                Log.i(TAG, "Wi-Fi has no network: Android looks for one as it came");
            }
            state = STATE_WAITING;
            detail = "";
            scanning = found;
            appliedAt = 0L;
            return;
        }
        if (!mine()) {
            // The first look since Android started: this is what to go back to.
            memory.keep(boot + "=" + found);
        }
        if (found) {
            android.scan(false);
            if (android.scanning()) {
                state = STATE_ERROR;
                detail = "Android went on scanning";
                scanning = true;
                return;
            }
            applied++;
            appliedAt = now;
            scansSinceApplied = 0;
            Log.i(TAG, "Android no longer scans for other networks while connected");
        } else if (appliedAt == 0L) {
            appliedAt = now;
            scansSinceApplied = 0;
        }
        state = STATE_APPLIED;
        detail = "";
        scanning = false;
    }

    private void release() throws Exception {
        boolean found = android.scanning();
        if (mine()) {
            boolean before = before();
            if (found != before) {
                android.scan(before);
                found = android.scanning();
                if (found != before) {
                    state = STATE_ERROR;
                    detail = "Android did not go back to scanning";
                    scanning = found;
                    return;
                }
                Log.i(TAG, "Android scans while connected as it did before");
            }
            memory.keep("");
        }
        state = STATE_OFF;
        detail = "";
        scanning = found;
        appliedAt = 0L;
    }

    /** What Android did before the guard first touched it; scanning, as it comes, if unknown. */
    private boolean before() {
        return !memory.kept().endsWith("=false");
    }

    /** Whether the guard has set the switch since Android last started. */
    private boolean mine() {
        return memory.kept().startsWith(boot + "=");
    }

    /** {enabled, state}, for the status. */
    synchronized JSONObject summary() throws JSONException {
        return new JSONObject()
                .put("enabled", memory.wanted())
                .put("supported", android.present())
                .put("state", state)
                .put("detail", detail);
    }

    /** Everything, for the settings and the health report. */
    synchronized JSONObject snapshot() throws JSONException {
        return summary()
                .put("scanningWhileConnected", scanning == null ? JSONObject.NULL : scanning)
                .put("appliedAt", appliedAt == 0L ? JSONObject.NULL : appliedAt)
                .put("applied", applied)
                .put("checks", checks)
                .put("checkedAt", checkedAt == 0L ? JSONObject.NULL : checkedAt)
                .put("scans", new JSONObject()
                        .put("whileConnected", scans)
                        .put("sinceApplied", scansSinceApplied)
                        .put("lastAt", lastScanAt == 0L ? JSONObject.NULL : lastScanAt));
    }

    /**
     * Counts a scan that Android has just finished. With the guard on there
     * should be none while connected, which is how its effect can be seen
     * from another room: a Mirror does not let an app look at the daemon.
     *
     * @param connected whether Wi-Fi was connected to a network when the scan ended
     */
    synchronized void scanned(boolean connected, long now) {
        if (!connected) {
            // Looking for a network to join is what scans are for.
            return;
        }
        scans++;
        lastScanAt = now;
        // A scan that was under way when the switch was set still ends.
        if (STATE_APPLIED.equals(state) && now - appliedAt >= SETTLE_MS) {
            scansSinceApplied++;
        }
    }

    /** The first line of a small file, trimmed; empty if it cannot be read. */
    private static String line(File file) {
        try (BufferedReader reader = new BufferedReader(new FileReader(file))) {
            String line = reader.readLine();
            return line == null ? "" : line.trim();
        } catch (IOException | RuntimeException unreadable) {
            return "";
        }
    }

    /** The owner's choice and what Android did before, in Mirror Home's settings. */
    private static final class Stored implements Memory {
        private final ConfigStore store;

        Stored(ConfigStore store) {
            this.store = store;
        }

        @Override
        public boolean wanted() {
            return store.isScanGuardEnabled();
        }

        @Override
        public void want(boolean wanted) {
            store.setScanGuardEnabled(wanted);
        }

        @Override
        public String kept() {
            return store.getScanGuardBefore();
        }

        @Override
        public void keep(String value) {
            store.setScanGuardBefore(value);
        }
    }

    /**
     * The switch as Android 6 has it: two methods of WifiManager that the
     * SDK hides. Later versions kept the names for a time and changed what
     * they do, so only Android 6 counts.
     */
    private static final class AndroidSwitch implements Switch {
        private final WifiManager wifi;
        private final Method read;
        private final Method write;

        AndroidSwitch(Context context) {
            WifiManager manager = null;
            Method getter = null;
            Method setter = null;
            if (Build.VERSION.SDK_INT == Build.VERSION_CODES.M) {
                try {
                    manager = (WifiManager) context.getSystemService(Context.WIFI_SERVICE);
                    getter = WifiManager.class.getMethod("getEnableAutoJoinWhenAssociated");
                    setter = WifiManager.class.getMethod(
                            "enableAutoJoinWhenAssociated", boolean.class);
                } catch (NoSuchMethodException | RuntimeException absent) {
                    manager = null;
                }
            }
            wifi = manager;
            read = getter;
            write = setter;
        }

        @Override
        public boolean present() {
            return wifi != null && read != null && write != null;
        }

        @Override
        public boolean searching() {
            if (!wifi.isWifiEnabled()) {
                // Switched off, or a device without Wi-Fi: nobody is looking for a network.
                return false;
            }
            WifiInfo info = wifi.getConnectionInfo();
            return info == null || info.getNetworkId() < 0
                    || info.getSupplicantState() != SupplicantState.COMPLETED;
        }

        @Override
        public boolean scanning() throws Exception {
            return Boolean.TRUE.equals(read.invoke(wifi));
        }

        @Override
        public void scan(boolean wanted) throws Exception {
            write.invoke(wifi, wanted);
        }
    }
}
