package dev.mirror.repurpose;

import android.annotation.SuppressLint;
import android.app.ActivityManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.wifi.ScanResult;
import android.net.wifi.SupplicantState;
import android.net.wifi.WifiConfiguration;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Brings the Mirror back to its Wi-Fi network when Android does not.
 *
 * <p>Android looks for its saved network by itself after losing it, and
 * nearly always finds it. One evening a Mirror did not: it stood for hours
 * with its network a room away, showing its setup screen, until somebody
 * switched it off and on. Why Android did not go back is not known, since
 * the restart erased its log. What is known: Android 6 sets a saved network
 * aside once joining it has failed more than four times in a row, as it
 * does while a router restarts or changes channel, and that Mirror's
 * network carried the mark of having been set aside so. Android is meant
 * to take such a network back by itself after five minutes; picking the
 * network, as a person does in Wi-Fi settings, takes it back at once.
 *
 * <p>Nobody can open Wi-Fi settings on a Mirror, and a Mirror without a
 * network cannot be reached to be told anything. So Mirror Home does what a
 * person would: after a minute without a network it picks the saved one
 * again, then every few minutes; and if that has not helped after ten
 * minutes it switches Wi-Fi off and on, which makes Android take every
 * saved network back and starts afresh whatever may have got stuck.
 *
 * <p>All of it is written to the {@link Journal}, with what Android said
 * of the network each time, and Android's own log is copied at the moment
 * of the loss ({@link SystemLog}), so that the next such evening explains
 * itself.
 */
final class WifiKeeper {
    static final String STATE_UNSUPPORTED = "unsupported";
    static final String STATE_CONNECTED = "connected";
    static final String STATE_NOTHING_SAVED = "nothing-saved";
    static final String STATE_WATCHING = "watching";
    static final String STATE_REJOINING = "rejoining";
    static final String STATE_RESTARTING = "restarting-wifi";

    /** Android gets this long to find the network by itself. */
    static final long FIRST_REJOIN_MS = 60_000L;
    static final long REJOIN_EVERY_MS = 2 * 60_000L;
    /** After half an hour the network is probably gone for a while. */
    static final long SETTLED_MS = 30 * 60_000L;
    static final long REJOIN_LATER_EVERY_MS = 5 * 60_000L;
    static final long FIRST_RESTART_MS = 10 * 60_000L;
    static final long SECOND_RESTART_MS = 30 * 60_000L;
    static final long RESTART_EVERY_MS = 60 * 60_000L;
    /** Wi-Fi stays off this long when it is switched off and on. */
    static final long OFF_FOR_MS = 5_000L;
    /** How often to look while there is no network, and while there is one. */
    static final long LOOK_EVERY_MS = 15_000L;
    static final long LOOK_RARELY_MS = 5 * 60_000L;
    /** Of what Wi-Fi does while it searches, this much is written per outage; the rest is counted. */
    static final int STATES_WRITTEN = 40;
    static final int REJOINS_WRITTEN = 10;
    static final int LATER_EVERY_NTH_REJOIN = 12;

    private static final String TAG = "WifiKeeper";
    private static final long NEVER = Long.MIN_VALUE;
    /** What Wi-Fi does on the way to a connection; searching and resting are written once. */
    private static final Set<String> STEPS = new HashSet<>(Arrays.asList(
            "ASSOCIATING", "AUTHENTICATING", "ASSOCIATED", "FOUR_WAY_HANDSHAKE", "GROUP_HANDSHAKE",
            "COMPLETED", "INTERFACE_DISABLED", "INVALID", "UNINITIALIZED", "OFF"));
    /*
     * What Android 6 keeps of a saved network in fields that the SDK hides. Named here and not
     * where they are read: see the note on lists in SystemLog.
     */
    private static final String[] HELD = {
            "disableReason", "autoJoinStatus", "numConnectionFailures", "numIpConfigFailures",
            "numAuthFailures", "numAssociation"};

    /** Android's Wi-Fi as far as the keeper needs it. */
    interface Radio {
        /** Whether this Android lets an app manage Wi-Fi at all. */
        boolean present();

        boolean on();

        /** Whether the Mirror has an address on a household network. */
        boolean connected();

        /** Whether there is a network to go back to. */
        boolean known();

        /** Whether the setup network is up, which switching Wi-Fi off would take from a phone. */
        boolean busy();

        /** Android's word for what Wi-Fi is doing, such as SCANNING. */
        String supplicant();

        /** Asks Android to join the saved network again and to look for it. */
        void rejoin();

        void power(boolean on);

        /** What Android says of Wi-Fi and of the saved network right now, for the journal. */
        JSONObject look();

        /** How well the network is received, while there is one; null otherwise. */
        JSONObject signal();
    }

    /** Where the keeper writes what it saw and did. */
    interface Book {
        void write(String what, JSONObject more);

        /** Keeps a copy of Android's log as it is now. */
        void copyLog(String reason);
    }

    private static WifiKeeper instance;

    private final Radio radio;
    private final Book book;

    private String state = STATE_CONNECTED;
    /** Null until the first look. */
    private Boolean connected;
    private long lostAt = NEVER;
    private long lostAtWall;
    /** Whether this time without a network began with losing one, rather than with starting. */
    private boolean lost;
    private long lastRejoinAt = NEVER;
    private long lastPowerAt = NEVER;
    private boolean switchedOff;
    private int rejoins;
    private int restarts;
    private String lastState = "";
    private int statesWritten;
    private int refusals;
    private final Map<String, Integer> states = new LinkedHashMap<>();
    private JSONObject lastSignal;
    private long lastSignalAt = NEVER;
    private int outages;
    private JSONObject lastOutage;
    private String lastAction = "";
    private long lastActionAt;
    private long looks;

    WifiKeeper(Radio radio, Book book) {
        this.radio = radio;
        this.book = book;
    }

    static synchronized WifiKeeper getInstance(Context context) {
        if (instance == null) {
            Context application = context.getApplicationContext();
            instance = new WifiKeeper(new AndroidRadio(application), new Kept(application));
        }
        return instance;
    }

    /** Begins looking: when Wi-Fi says something, and by the clock. */
    void start(Context context) {
        Context application = context.getApplicationContext();
        HandlerThread thread = new HandlerThread("wifi-keeper");
        thread.start();
        Handler handler = new Handler(thread.getLooper());
        Runnable look = new Runnable() {
            @Override
            public void run() {
                handler.removeCallbacks(this);
                long next = LOOK_RARELY_MS;
                try {
                    next = check(SystemClock.elapsedRealtime(), System.currentTimeMillis());
                } catch (RuntimeException error) {
                    Log.w(TAG, "Unable to look after Wi-Fi", error);
                }
                handler.postDelayed(this, next);
            }
        };
        IntentFilter events = new IntentFilter();
        events.addAction(WifiManager.WIFI_STATE_CHANGED_ACTION);
        events.addAction(WifiManager.NETWORK_STATE_CHANGED_ACTION);
        events.addAction(WifiManager.SUPPLICANT_STATE_CHANGED_ACTION);
        events.addAction(WifiManager.RSSI_CHANGED_ACTION);
        application.registerReceiver(new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                if (WifiManager.RSSI_CHANGED_ACTION.equals(intent.getAction())) {
                    remember(SystemClock.elapsedRealtime());
                    return;
                }
                if (intent.getIntExtra(WifiManager.EXTRA_SUPPLICANT_ERROR, 0)
                        == WifiManager.ERROR_AUTHENTICATING) {
                    refused();
                }
                look.run();
            }
        }, events, null, handler);
        handler.post(look);
    }

    /**
     * Looks at Wi-Fi and does what is due.
     *
     * @param now for how long Android has been running, in milliseconds
     * @param wall the time of day, in milliseconds since 1970
     * @return in how many milliseconds to look again
     */
    synchronized long check(long now, long wall) {
        looks++;
        if (!radio.present()) {
            state = STATE_UNSUPPORTED;
            return LOOK_RARELY_MS;
        }
        if (radio.connected()) {
            arrived(now, wall);
            return LOOK_RARELY_MS;
        }
        if (lostAt == NEVER) {
            departed(now, wall);
        }
        connected = Boolean.FALSE;
        step(radio.supplicant());
        long out = now - lostAt;
        if (!radio.known()) {
            // A Mirror that was never on a network: setting it up is a person's part.
            state = STATE_NOTHING_SAVED;
            return LOOK_EVERY_MS;
        }
        if (switchedOff) {
            if (now - lastPowerAt < OFF_FOR_MS) {
                // Let Android finish switching it off first.
                state = STATE_RESTARTING;
                return OFF_FOR_MS;
            }
            switchedOff = false;
            lastRejoinAt = NEVER;
            lastPowerAt = now;
            radio.power(true);
            did("wifi-on", wall, null);
            state = STATE_RESTARTING;
            return LOOK_EVERY_MS;
        }
        if (!radio.on()) {
            // Switched off, and not by the keeper. With a network to go back to, that is not meant to last.
            if (out >= FIRST_REJOIN_MS && (lastPowerAt == NEVER || now - lastPowerAt >= FIRST_REJOIN_MS)) {
                lastPowerAt = now;
                lastRejoinAt = NEVER;
                radio.power(true);
                did("switched-on", wall, seconds(out));
            }
            state = out < FIRST_REJOIN_MS ? STATE_WATCHING : STATE_REJOINING;
            return LOOK_EVERY_MS;
        }
        if (out < FIRST_REJOIN_MS) {
            state = STATE_WATCHING;
            return LOOK_EVERY_MS;
        }
        if (out >= restartDue(restarts) && !radio.busy()) {
            restarts++;
            JSONObject seen = with(radio.look(), "without", out / 1000L);
            if (restarts == 1) {
                book.copyLog("wifi-restart");
            }
            switchedOff = true;
            lastPowerAt = now;
            radio.power(false);
            did("restart-wifi", wall, with(seen, "restart", restarts));
            state = STATE_RESTARTING;
            return LOOK_EVERY_MS;
        }
        long every = out < SETTLED_MS ? REJOIN_EVERY_MS : REJOIN_LATER_EVERY_MS;
        if (lastRejoinAt == NEVER || now - lastRejoinAt >= every) {
            rejoins++;
            lastRejoinAt = now;
            JSONObject seen = with(with(radio.look(), "without", out / 1000L), "attempt", rejoins);
            radio.rejoin();
            lastAction = "rejoin";
            lastActionAt = wall;
            if (rejoins <= REJOINS_WRITTEN || rejoins % LATER_EVERY_NTH_REJOIN == 0) {
                book.write("rejoin", seen);
            }
            if (rejoins == 1) {
                book.copyLog("wifi-rejoin");
            }
        }
        state = STATE_REJOINING;
        return LOOK_EVERY_MS;
    }

    /** Android said that the network's password was not accepted. */
    synchronized void refused() {
        refusals++;
        if (refusals <= 5) {
            book.write("authentication-failed", null);
        }
    }

    /** Keeps how well the network is received, to say it when the network is lost. */
    synchronized void remember(long now) {
        JSONObject signal = radio.signal();
        if (signal != null) {
            lastSignal = signal;
            lastSignalAt = now;
        }
    }

    /** For the health report. */
    synchronized JSONObject snapshot(long now) throws JSONException {
        boolean out = lostAt != NEVER;
        return new JSONObject()
                .put("state", state)
                .put("supported", radio.present())
                .put("withoutNetworkSeconds", out ? (Object) ((now - lostAt) / 1000L) : JSONObject.NULL)
                .put("since", out ? (Object) lostAtWall : JSONObject.NULL)
                .put("rejoins", rejoins)
                .put("wifiRestarts", restarts)
                .put("outages", outages)
                .put("lastOutage", lastOutage == null ? JSONObject.NULL : lastOutage)
                .put("lastAction", lastAction.isEmpty()
                        ? JSONObject.NULL
                        : new JSONObject().put("what", lastAction).put("at", lastActionAt))
                .put("looks", looks);
    }

    /** After how long without a network Wi-Fi is switched off and on for the next time. */
    static long restartDue(int restartsSoFar) {
        if (restartsSoFar == 0) {
            return FIRST_RESTART_MS;
        }
        if (restartsSoFar == 1) {
            return SECOND_RESTART_MS;
        }
        return RESTART_EVERY_MS * (restartsSoFar - 1);
    }

    private void arrived(long now, long wall) {
        remember(now);
        if (lostAt != NEVER) {
            long out = now - lostAt;
            JSONObject more = with(radio.look(), "after", out / 1000L);
            try {
                more.put("rejoins", rejoins).put("wifiRestarts", restarts).put("states", new JSONObject(states));
                if (lost) {
                    lastOutage = new JSONObject()
                            .put("lostAt", lostAtWall)
                            .put("backAt", wall)
                            .put("seconds", out / 1000L)
                            .put("rejoins", rejoins)
                            .put("wifiRestarts", restarts);
                }
            } catch (JSONException impossible) {
                // Numbers and words always fit.
            }
            book.write(lost ? "back" : "connected", more);
            if (lost && out >= FIRST_REJOIN_MS) {
                book.copyLog("wifi-back");
            }
        } else if (connected == null) {
            book.write("connected", radio.look());
        }
        connected = Boolean.TRUE;
        state = STATE_CONNECTED;
        lostAt = NEVER;
        lost = false;
        lastRejoinAt = NEVER;
        lastPowerAt = NEVER;
        switchedOff = false;
        rejoins = 0;
        restarts = 0;
        refusals = 0;
        statesWritten = 0;
        lastState = "";
        states.clear();
    }

    private void departed(long now, long wall) {
        lostAt = now;
        lostAtWall = wall;
        lost = Boolean.TRUE.equals(connected);
        JSONObject seen = radio.look();
        if (seen == null) {
            seen = new JSONObject();
        }
        if (lost) {
            outages++;
            if (lastSignal != null) {
                try {
                    seen.put("before", new JSONObject(lastSignal.toString())
                            .put("secondsAgo", (now - lastSignalAt) / 1000L));
                } catch (JSONException impossible) {
                    // It was read from JSON a moment ago.
                }
            }
            // At once: Android's log reaches back minutes, and the reason is said in it now.
            book.copyLog(SystemLog.LOSS);
        }
        book.write(lost ? "lost" : "without", seen);
    }

    /** Counts what Wi-Fi is doing, and writes the steps towards a connection while the allowance lasts. */
    private void step(String doing) {
        if (doing == null || doing.isEmpty() || doing.equals(lastState)) {
            return;
        }
        lastState = doing;
        Integer before = states.get(doing);
        states.put(doing, before == null ? 1 : before + 1);
        if (before == null || (STEPS.contains(doing) && statesWritten < STATES_WRITTEN)) {
            statesWritten++;
            try {
                book.write("state", new JSONObject().put("state", doing));
            } catch (JSONException impossible) {
                // A word always fits.
            }
        }
    }

    private void did(String what, long wall, JSONObject more) {
        lastAction = what;
        lastActionAt = wall;
        book.write(what, more);
    }

    private static JSONObject seconds(long millis) {
        return with(new JSONObject(), "without", millis / 1000L);
    }

    private static JSONObject with(JSONObject target, String name, long value) {
        JSONObject result = target == null ? new JSONObject() : target;
        try {
            result.put(name, value);
        } catch (JSONException impossible) {
            // A number always fits.
        }
        return result;
    }

    /** The journal and the copies of the log. */
    private static final class Kept implements Book {
        private final Context context;
        private final ExecutorService copier = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "system-log");
            thread.setDaemon(true);
            return thread;
        });

        Kept(Context context) {
            this.context = context;
        }

        @Override
        public void write(String what, JSONObject more) {
            Journal.note(context, "wifi", what, more);
        }

        @Override
        public void copyLog(String reason) {
            long at = System.currentTimeMillis();
            copier.execute(() -> {
                JSONObject copy = SystemLog.getInstance(context).capture(reason, at);
                if (copy != null) {
                    Journal.note(context, "log", "copied", copy);
                }
            });
        }
    }

    /** Wi-Fi as Android 6 to 9 give it to an app; later versions manage it themselves. */
    @SuppressLint("MissingPermission")
    private static final class AndroidRadio implements Radio {
        private final Context context;
        private final WifiManager wifi;
        private final ConfigStore settings;

        AndroidRadio(Context context) {
            this.context = context;
            wifi = (WifiManager) context.getSystemService(Context.WIFI_SERVICE);
            settings = new ConfigStore(context);
        }

        @Override
        public boolean present() {
            return wifi != null && Build.VERSION.SDK_INT < 29;
        }

        @Override
        public boolean on() {
            return wifi.isWifiEnabled();
        }

        @Override
        public boolean connected() {
            return !LanAddress.current(context).isEmpty();
        }

        @Override
        public boolean known() {
            return !settings.getManagedWifiSsid().isEmpty() || !settings.getLastWifiSsid().isEmpty();
        }

        @Override
        public boolean busy() {
            return WifiDirectOnboarding.getInstance(context).snapshot().active;
        }

        @Override
        public String supplicant() {
            if (!wifi.isWifiEnabled()) {
                return "OFF";
            }
            WifiInfo info = wifi.getConnectionInfo();
            SupplicantState doing = info == null ? null : info.getSupplicantState();
            return doing == null ? "" : doing.name();
        }

        @Override
        public void rejoin() {
            // Nothing may stand in the way of finding a network; the guard gives its switch back here.
            ScanGuard.getInstance(context).check(System.currentTimeMillis());
            String managed = settings.getManagedWifiSsid();
            List<WifiConfiguration> saved = wifi.getConfiguredNetworks();
            boolean chosen = false;
            if (saved != null) {
                for (WifiConfiguration network : saved) {
                    // The network set up through Mirror Home, or the only one there is.
                    if (saved.size() == 1
                            || (!managed.isEmpty() && WifiProvisioner.cleanSsid(network.SSID).equals(managed))) {
                        // As if a person had picked it: Android then takes back a network it had set aside.
                        chosen = wifi.enableNetwork(network.networkId, true) || chosen;
                    }
                }
            }
            if (saved != null && !chosen) {
                for (WifiConfiguration network : saved) {
                    wifi.enableNetwork(network.networkId, false);
                }
            }
            wifi.reconnect();
            wifi.startScan();
        }

        @Override
        public void power(boolean on) {
            if (!wifi.setWifiEnabled(on)) {
                Log.w(TAG, "Android would not switch Wi-Fi " + (on ? "on" : "off"));
            }
        }

        @Override
        public JSONObject signal() {
            WifiInfo info = wifi.getConnectionInfo();
            if (info == null || info.getNetworkId() < 0 || info.getIpAddress() == 0) {
                return null;
            }
            String name = WifiProvisioner.cleanSsid(info.getSSID());
            if (!name.isEmpty() && !name.startsWith("<") && !name.equals(settings.getLastWifiSsid())) {
                // What to go back to, for a Mirror that was put on its network by other means than Mirror Home.
                settings.setLastWifiSsid(name);
            }
            try {
                return new JSONObject()
                        .put("rssi", info.getRssi())
                        .put("frequencyMhz", info.getFrequency())
                        .put("linkSpeedMbps", info.getLinkSpeed());
            } catch (JSONException impossible) {
                return null;
            }
        }

        @Override
        public JSONObject look() {
            JSONObject result = new JSONObject();
            try {
                result.put("wifi", wifi.isWifiEnabled() ? "on" : "off");
                result.put("doing", supplicant());
                PowerManager power = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
                result.put("display", power != null && power.isInteractive() ? "on" : "off");
                ActivityManager manager = (ActivityManager) context.getSystemService(Context.ACTIVITY_SERVICE);
                if (manager != null) {
                    ActivityManager.MemoryInfo memory = new ActivityManager.MemoryInfo();
                    manager.getMemoryInfo(memory);
                    result.put("freeMb", memory.availMem / (1024L * 1024L));
                }
                result.put("guard", ScanGuard.getInstance(context).summary().optString("state"));
                JSONObject signal = signal();
                if (signal != null) {
                    result.put("signal", signal);
                }
                String name = settings.getManagedWifiSsid().isEmpty()
                        ? settings.getLastWifiSsid()
                        : settings.getManagedWifiSsid();
                result.put("network", name);
                List<WifiConfiguration> saved = wifi.getConfiguredNetworks();
                result.put("saved", saved == null ? JSONObject.NULL : (Object) saved.size());
                if (saved != null) {
                    for (WifiConfiguration network : saved) {
                        if (WifiProvisioner.cleanSsid(network.SSID).equals(name)) {
                            result.put("held", held(network));
                        }
                    }
                }
                result.put("inRange", inRange(name));
            } catch (JSONException | RuntimeException error) {
                Log.w(TAG, "Unable to look at Wi-Fi: " + error);
            }
            return result;
        }

        /**
         * What Android holds against a saved network: whether it has set it
         * aside, why, and how often joining it failed. Android 6 keeps this
         * in fields that the SDK hides.
         */
        private static JSONObject held(WifiConfiguration network) throws JSONException {
            JSONObject result = new JSONObject()
                    .put("status", network.status == WifiConfiguration.Status.CURRENT
                            ? "current"
                            : network.status == WifiConfiguration.Status.DISABLED ? "disabled" : "enabled");
            for (String name : HELD) {
                try {
                    result.put(name, WifiConfiguration.class.getField(name).getInt(network));
                } catch (ReflectiveOperationException | RuntimeException absent) {
                    // Another Android; it keeps this elsewhere.
                }
            }
            return result;
        }

        /**
         * Whether the saved network is among those that Wi-Fi last saw, and
         * how well. Android 6 only tells an app that may know its location.
         */
        private Object inRange(String name) throws JSONException {
            List<ScanResult> seen;
            try {
                seen = wifi.getScanResults();
            } catch (SecurityException notAllowed) {
                return JSONObject.NULL;
            }
            if (seen == null || seen.isEmpty()) {
                return JSONObject.NULL;
            }
            int best = Integer.MIN_VALUE;
            Set<Integer> frequencies = new TreeSet<>();
            for (ScanResult result : seen) {
                if (name.equals(result.SSID)) {
                    best = Math.max(best, result.level);
                    frequencies.add(result.frequency);
                }
            }
            JSONObject result = new JSONObject().put("networks", seen.size()).put("seen", !frequencies.isEmpty());
            if (!frequencies.isEmpty()) {
                result.put("rssi", best).put("frequenciesMhz", new JSONArray(frequencies));
            }
            return result;
        }
    }
}
