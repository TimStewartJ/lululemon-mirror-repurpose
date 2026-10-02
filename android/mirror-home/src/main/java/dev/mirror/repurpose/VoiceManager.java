package dev.mirror.repurpose;

import android.Manifest;
import android.app.ActivityManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageInstaller;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Debug;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Message;
import android.os.Messenger;
import android.os.RemoteException;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.util.ArrayDeque;

/**
 * Spoken commands: keeps the recogniser's process running while voice is
 * switched on, a speech model is installed, the microphone may be used and
 * Android is installing nothing; decides what each recognised sentence means;
 * and carries commands out the way the controls would.
 */
public final class VoiceManager {
    /** Sent to the dashboard so that the glass can show what was heard. */
    static final String ACTION_VOICE_EVENT = "dev.mirror.repurpose.VOICE_EVENT";
    static final String EXTRA_KIND = "kind";
    static final String EXTRA_CAPTION = "caption";
    static final String KIND_LISTENING = "listening";
    static final String KIND_COMMAND = "command";
    static final String KIND_NOT_UNDERSTOOD = "not-understood";

    private static final String TAG = "VoiceManager";
    private static final long CHECK_INTERVAL_MS = 10_000L;
    private static final long RETRY_AFTER_ERROR_MS = 30_000L;
    /** A recogniser that stops this often is left alone for a while. */
    private static final int MAX_STOPS = 5;
    private static final long STOPS_WINDOW_MS = 10 * 60_000L;
    private static final int BRIGHTNESS_STEP = 40;
    /** Measuring another process's memory takes a tenth of a second on a Mirror. */
    private static final long MEMORY_INTERVAL_MS = 30_000L;
    private static final int MAX_RECENT = 20;

    private static volatile VoiceManager instance;

    private final Context context;
    private final ConfigStore configStore;
    private final VoiceModelStore models;
    private final VoiceInterpreter interpreter = new VoiceInterpreter();
    private final VoiceStandDown standDown = new VoiceStandDown();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Messenger incoming = new Messenger(new Handler(Looper.getMainLooper(), message -> {
        handleMessage(message);
        return true;
    }));
    private final ArrayDeque<JSONObject> recent = new ArrayDeque<>();
    private final ArrayDeque<Long> stops = new ArrayDeque<>();
    private final Runnable check = new Runnable() {
        @Override
        public void run() {
            evaluate();
            handler.postDelayed(this, CHECK_INTERVAL_MS);
        }
    };
    private final ServiceConnection connection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder binder) {
            synchronized (VoiceManager.this) {
                service = new Messenger(binder);
                serviceState = "";
                detail = "";
            }
            sendStart();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            recogniserStopped();
        }
    };

    private boolean started;
    private boolean bound;
    private boolean installing;
    private Messenger service;
    private String serviceState = "";
    private String detail = "";
    private int pid;
    private int restarts;
    private long modelLoadMs;
    private long errorAtElapsed;
    private long pausedUntilElapsed;
    private double levelDb = Double.NaN;
    private double peakDb = Double.NaN;
    private boolean silent;
    private double cpuShare = Double.NaN;
    private long behindMs;
    private long listenedMs;
    private long sentences;
    private long wakeWords;
    private long commands;
    private long notUnderstood;
    private long unsure;
    private JSONObject lastCommand;
    private int measuredPid;
    private long measuredAtElapsed;
    private Object measuredPssKb = JSONObject.NULL;

    private VoiceManager(Context context) {
        this.context = context.getApplicationContext();
        configStore = new ConfigStore(this.context);
        models = new VoiceModelStore(new File(this.context.getFilesDir(), "voice"));
    }

    public static VoiceManager getInstance(Context context) {
        if (instance == null) {
            synchronized (VoiceManager.class) {
                if (instance == null) {
                    instance = new VoiceManager(context);
                }
            }
        }
        return instance;
    }

    /** Begins looking after the recogniser; called once when Mirror Home starts. */
    public synchronized void start() {
        if (started) {
            return;
        }
        started = true;
        models.recover();
        watchInstallations();
        handler.post(check);
    }

    /** Has voice step aside whenever Android installs an app; see {@link VoiceStandDown}. */
    private void watchInstallations() {
        try {
            PackageInstaller installer = context.getPackageManager().getPackageInstaller();
            installer.registerSessionCallback(new PackageInstaller.SessionCallback() {
                @Override
                public void onCreated(int sessionId) {
                    installationBegan(sessionId);
                }

                @Override
                public void onBadgingChanged(int sessionId) {
                }

                @Override
                public void onActiveChanged(int sessionId, boolean active) {
                    if (active) {
                        installationBegan(sessionId);
                    }
                }

                @Override
                public void onProgressChanged(int sessionId, float progress) {
                }

                @Override
                public void onFinished(int sessionId, boolean success) {
                    standDown.ended(sessionId);
                    evaluate();
                }
            }, handler);
            // One that was under way before Mirror Home started.
            for (PackageInstaller.SessionInfo session : installer.getAllSessions()) {
                if (session.isActive()) {
                    standDown.began(session.getSessionId(), SystemClock.elapsedRealtime());
                }
            }
        } catch (RuntimeException unavailable) {
            Log.w(TAG, "Unable to watch for installations", unavailable);
        }
    }

    private void installationBegan(int sessionId) {
        long now = SystemClock.elapsedRealtime();
        if (!standDown.active(now)) {
            Log.i(TAG, "An installation began; the recogniser steps aside");
        }
        standDown.began(sessionId, now);
        evaluate();
    }

    public void setEnabled(boolean enabled) {
        configStore.setVoiceEnabled(enabled);
        synchronized (this) {
            if (enabled) {
                // Switching it on again is also how an owner asks for another try.
                pausedUntilElapsed = 0;
                stops.clear();
            }
        }
        handler.post(this::evaluate);
    }

    /**
     * Installs a speech model from a zip archive, replacing the one before.
     *
     * @throws IOException with the reason, if the archive is not a usable model
     */
    public JSONObject installModel(File archive, String sha256) throws IOException, JSONException {
        synchronized (this) {
            if (installing) {
                throw new IOException("Another speech model is being installed");
            }
            installing = true;
        }
        try {
            JSONObject installed = models.install(archive, sha256, System.currentTimeMillis());
            Log.i(TAG, "Speech model installed: " + installed.optString("name"));
            return installed;
        } finally {
            synchronized (this) {
                installing = false;
            }
            handler.post(() -> {
                // A recogniser that is running still holds the model from before.
                boolean running;
                synchronized (VoiceManager.this) {
                    running = service != null;
                }
                if (running) {
                    sendStart();
                }
                evaluate();
            });
        }
    }

    public boolean removeModel() {
        boolean removed = models.remove();
        handler.post(this::evaluate);
        return removed;
    }

    /** Takes a sentence as if the recogniser had heard it; for the validation suite. */
    void injectSentence(String text, double confidence) throws IOException {
        synchronized (this) {
            if (!VoiceService.STATE_LISTENING.equals(serviceState) || service == null) {
                throw new IOException("Voice is not listening");
            }
        }
        handler.post(() -> heard(new VoiceSentence(VoiceCommands.normalize(text), confidence, 0, 0)));
    }

    /**
     * Has the recogniser hear a WAV file in place of the microphone; for the
     * validation suite, since an emulator has nothing to speak into.
     */
    void injectClip(File wav) throws IOException {
        Messenger target;
        synchronized (this) {
            target = VoiceService.STATE_LISTENING.equals(serviceState) ? service : null;
        }
        if (target == null) {
            throw new IOException("Voice is not listening");
        }
        Bundle data = new Bundle();
        data.putString(VoiceService.KEY_PATH, wav.getAbsolutePath());
        Message message = Message.obtain(null, VoiceService.MSG_CLIP);
        message.setData(data);
        try {
            target.send(message);
        } catch (RemoteException gone) {
            throw new IOException("Voice is not listening");
        }
    }

    /** Where voice stands, in brief: for the status, which the controls ask for every few seconds. */
    public synchronized JSONObject summary() throws JSONException {
        boolean enabled = configStore.isVoiceEnabled();
        boolean modelInstalled = models.installed();
        boolean permission = mayRecord();
        return new JSONObject()
                .put("enabled", enabled)
                .put("state", state(enabled, modelInstalled, permission))
                .put("detail", describeState(enabled, modelInstalled, permission));
    }

    public synchronized JSONObject snapshot() throws JSONException {
        boolean enabled = configStore.isVoiceEnabled();
        JSONObject model = models.describe();
        boolean permission = mayRecord();
        JSONObject result = new JSONObject()
                .put("enabled", enabled)
                .put("state", state(enabled, model != null, permission))
                .put("detail", describeState(enabled, model != null, permission))
                .put("wakeWord", VoiceCommands.WAKE_WORD)
                .put("commands", VoiceCommands.describe())
                .put("model", model == null ? JSONObject.NULL : model)
                .put("maxModelBytes", VoiceModelArchive.MAX_ARCHIVE_BYTES)
                .put("permissionGranted", permission)
                .put("process", new JSONObject()
                        .put("pid", pid == 0 ? JSONObject.NULL : pid)
                        .put("pssKb", pid == 0 ? JSONObject.NULL : processPssKb(pid))
                        .put("restarts", restarts))
                .put("recogniser", new JSONObject()
                        .put("modelLoadMs", modelLoadMs == 0 ? JSONObject.NULL : modelLoadMs)
                        .put("cpuShare", number(cpuShare, 100))
                        .put("behindMs", behindMs)
                        .put("listenedSeconds", listenedMs / 1000))
                .put("microphone", new JSONObject()
                        .put("levelDb", number(levelDb, 10))
                        .put("peakDb", number(peakDb, 10))
                        .put("silent", silent))
                .put("counts", new JSONObject()
                        .put("sentences", sentences)
                        .put("wakeWords", wakeWords)
                        .put("commands", commands)
                        .put("notUnderstood", notUnderstood)
                        .put("unsure", unsure))
                .put("lastCommand", lastCommand == null ? JSONObject.NULL : lastCommand)
                .put("recent", new JSONArray(recent))
                .put("testHooks", BuildConfig.DEBUG);
        return result;
    }

    /** One word for where voice stands, for the controls and the health report. */
    private String state(boolean enabled, boolean modelInstalled, boolean permission) {
        if (!enabled) {
            return "off";
        }
        if (!modelInstalled) {
            return "no-model";
        }
        if (!permission) {
            return "no-permission";
        }
        if (standDown.active(SystemClock.elapsedRealtime())) {
            return "paused";
        }
        if (SystemClock.elapsedRealtime() < pausedUntilElapsed) {
            return "error";
        }
        if (service == null || serviceState.isEmpty() || VoiceService.STATE_STOPPED.equals(serviceState)) {
            return "starting";
        }
        return serviceState;
    }

    private String describeState(boolean enabled, boolean modelInstalled, boolean permission) {
        switch (state(enabled, modelInstalled, permission)) {
            case "off":
                return "Voice commands are switched off";
            case "no-model":
                return "No speech model is installed";
            case "no-permission":
                return "Mirror Home may not use the microphone";
            case "paused":
                return "Paused while an update is installed";
            case "error":
                return SystemClock.elapsedRealtime() < pausedUntilElapsed
                        ? "The recogniser stopped " + MAX_STOPS + " times; trying again later"
                        : detail;
            case VoiceService.STATE_LOADING:
                return "Loading the speech model";
            case VoiceService.STATE_LISTENING:
                return silent ? "Listening, but the microphone is silent" : "Listening";
            default:
                return "Starting";
        }
    }

    private boolean mayRecord() {
        return context.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
    }

    private Object processPssKb(int processId) {
        long now = SystemClock.elapsedRealtime();
        if (processId == measuredPid && now - measuredAtElapsed < MEMORY_INTERVAL_MS) {
            return measuredPssKb;
        }
        ActivityManager manager = (ActivityManager) context.getSystemService(Context.ACTIVITY_SERVICE);
        Debug.MemoryInfo[] memory = manager == null
                ? null
                : manager.getProcessMemoryInfo(new int[]{processId});
        measuredPid = processId;
        measuredAtElapsed = now;
        measuredPssKb = memory == null || memory.length == 0
                ? JSONObject.NULL
                : (Object) memory[0].getTotalPss();
        return measuredPssKb;
    }

    private static Object number(double value, int scale) throws JSONException {
        return Double.isNaN(value) || Double.isInfinite(value)
                ? JSONObject.NULL
                : (Object) (Math.round(value * scale) / (double) scale);
    }

    /** Starts or stops the recogniser to match what is wanted. Runs on the main thread. */
    private void evaluate() {
        boolean wanted;
        boolean retry;
        synchronized (this) {
            long now = SystemClock.elapsedRealtime();
            wanted = started
                    && configStore.isVoiceEnabled()
                    && !installing
                    && models.installed()
                    && mayRecord()
                    && !standDown.active(now)
                    && now >= pausedUntilElapsed;
            retry = wanted
                    && service != null
                    && VoiceService.STATE_ERROR.equals(serviceState)
                    && now - errorAtElapsed >= RETRY_AFTER_ERROR_MS;
        }
        if (wanted && !bound) {
            bound = context.bindService(
                    new Intent(context, VoiceService.class), connection, Context.BIND_AUTO_CREATE);
            if (!bound) {
                Log.e(TAG, "Unable to start the recogniser's process");
            }
        } else if (!wanted && bound) {
            unbind();
        } else if (retry) {
            sendStart();
        }
    }

    private void unbind() {
        if (!bound) {
            return;
        }
        Messenger target;
        synchronized (this) {
            target = service;
            service = null;
            serviceState = "";
            pid = 0;
            levelDb = Double.NaN;
            peakDb = Double.NaN;
            cpuShare = Double.NaN;
            silent = false;
        }
        if (target != null) {
            try {
                target.send(Message.obtain(null, VoiceService.MSG_STOP));
            } catch (RemoteException gone) {
                // It has stopped already.
            }
        }
        context.unbindService(connection);
        bound = false;
    }

    private void sendStart() {
        Messenger target;
        synchronized (this) {
            target = service;
            interpreter.reset();
        }
        if (target == null) {
            return;
        }
        Bundle data = new Bundle();
        data.putString(VoiceService.KEY_MODEL, models.directory().getAbsolutePath());
        data.putString(VoiceService.KEY_GRAMMAR, VoiceCommands.grammar());
        Message message = Message.obtain(null, VoiceService.MSG_START);
        message.setData(data);
        message.replyTo = incoming;
        try {
            target.send(message);
        } catch (RemoteException gone) {
            recogniserStopped();
        }
    }

    /** The recogniser's process ended by itself. Android starts it again while it is wanted. */
    private void recogniserStopped() {
        boolean giveUp;
        synchronized (this) {
            service = null;
            serviceState = "";
            pid = 0;
            restarts++;
            long now = SystemClock.elapsedRealtime();
            stops.addLast(now);
            while (!stops.isEmpty() && now - stops.peekFirst() > STOPS_WINDOW_MS) {
                stops.removeFirst();
            }
            giveUp = stops.size() >= MAX_STOPS;
            if (giveUp) {
                pausedUntilElapsed = now + STOPS_WINDOW_MS;
                stops.clear();
            }
        }
        Log.w(TAG, "The recogniser's process stopped" + (giveUp ? "; leaving it for a while" : ""));
        if (giveUp) {
            handler.post(this::evaluate);
        }
    }

    private void handleMessage(Message message) {
        Bundle data = message.getData();
        switch (message.what) {
            case VoiceService.MSG_STATE:
                synchronized (this) {
                    serviceState = data.getString(VoiceService.KEY_STATE, "");
                    detail = data.getString(VoiceService.KEY_DETAIL, "");
                    pid = data.getInt(VoiceService.KEY_PID, 0);
                    long loadMs = data.getLong(VoiceService.KEY_LOAD_MS, 0);
                    if (loadMs > 0) {
                        modelLoadMs = loadMs;
                    }
                    if (VoiceService.STATE_ERROR.equals(serviceState)) {
                        errorAtElapsed = SystemClock.elapsedRealtime();
                    }
                    // What the process held while it loaded says nothing about now.
                    measuredPid = 0;
                }
                break;
            case VoiceService.MSG_STATS:
                synchronized (this) {
                    levelDb = data.getDouble(VoiceService.KEY_LEVEL_DB, Double.NaN);
                    peakDb = data.getDouble(VoiceService.KEY_PEAK_DB, Double.NaN);
                    silent = data.getBoolean(VoiceService.KEY_SILENT, false);
                    cpuShare = data.getDouble(VoiceService.KEY_CPU_SHARE, Double.NaN);
                    behindMs = data.getLong(VoiceService.KEY_BEHIND_MS, 0);
                    listenedMs = data.getLong(VoiceService.KEY_LISTENED_MS, 0);
                }
                break;
            case VoiceService.MSG_SENTENCE:
                try {
                    VoiceSentence sentence = VoiceSentence.parse(data.getString(VoiceService.KEY_JSON, "{}"));
                    if (sentence != null) {
                        heard(sentence);
                    }
                } catch (JSONException malformed) {
                    Log.w(TAG, "The recogniser reported something unreadable", malformed);
                }
                break;
            default:
                break;
        }
    }

    /** Decides what a sentence means and acts on it. Runs on the main thread. */
    private void heard(VoiceSentence sentence) {
        // Timed by when it arrives: the recogniser's own clock starts afresh with each start.
        long now = SystemClock.elapsedRealtime();
        long began = now - Math.max(0, sentence.endMs - sentence.startMs);
        VoiceInterpreter.Outcome outcome;
        synchronized (this) {
            outcome = interpreter.heard(sentence.text, sentence.lowestConfidence, began, now);
            sentences++;
        }
        String caption = null;
        switch (outcome.kind) {
            case COMMAND:
                caption = carryOut(outcome.command);
                synchronized (this) {
                    commands++;
                    try {
                        lastCommand = new JSONObject()
                                .put("id", outcome.command.id)
                                .put("at", System.currentTimeMillis())
                                .put("shown", caption);
                    } catch (JSONException impossible) {
                        lastCommand = null;
                    }
                }
                show(KIND_COMMAND, caption);
                break;
            case WAKE:
                synchronized (this) {
                    wakeWords++;
                }
                show(KIND_LISTENING, "");
                break;
            case NOT_UNDERSTOOD:
                synchronized (this) {
                    notUnderstood++;
                }
                show(KIND_NOT_UNDERSTOOD, "");
                break;
            case UNSURE:
                synchronized (this) {
                    unsure++;
                }
                break;
            default:
                break;
        }
        if (outcome.kind != VoiceInterpreter.Kind.OTHER) {
            remember(sentence, outcome, caption);
        }
    }

    private synchronized void remember(
            VoiceSentence sentence, VoiceInterpreter.Outcome outcome, String shown) {
        try {
            // Only what was said to the Mirror is kept, and only its words from the command list.
            recent.addLast(new JSONObject()
                    .put("at", System.currentTimeMillis())
                    .put("heard", VoiceCommands.withoutOtherWords(sentence.text))
                    .put("confidence", number(sentence.lowestConfidence, 100))
                    .put("outcome", outcome.kind.name().toLowerCase(java.util.Locale.ROOT).replace('_', '-'))
                    .put("command", outcome.command == null ? JSONObject.NULL : outcome.command.id)
                    .put("shown", shown == null ? JSONObject.NULL : shown));
        } catch (JSONException impossible) {
            return;
        }
        while (recent.size() > MAX_RECENT) {
            recent.removeFirst();
        }
    }

    /**
     * Carries a command out the way the controls would.
     *
     * @return what to show on the glass
     */
    private String carryOut(VoiceCommands.Command command) {
        AutomationManager automation = AutomationManager.getInstance(context);
        if (command == VoiceCommands.Command.SLEEP) {
            automation.setManualSleeping(true);
            return command.caption;
        }
        // Whoever speaks to the Mirror stands before it: anything but "sleep"
        // wakes a dark Mirror, and keeps a lit one awake as being seen does.
        boolean wasSleeping = automation.isSleeping();
        automation.wakeForPresence();
        switch (command) {
            case BRIGHTER:
                return wasSleeping
                        ? VoiceCommands.Command.WAKE.caption
                        : changeBrightness(automation, BRIGHTNESS_STEP, command.caption, "Brightest");
            case DIMMER:
                return wasSleeping
                        ? VoiceCommands.Command.WAKE.caption
                        : changeBrightness(automation, -BRIGHTNESS_STEP, command.caption, "Dimmest");
            case NEXT_VIDEO:
                return nextVideo(command.caption);
            default:
                return command.caption;
        }
    }

    private static String changeBrightness(
            AutomationManager automation, int step, String caption, String atLimit) {
        int before = automation.awakeBrightness();
        int after = automation.stepWakeBrightness(step);
        if (after == AutomationManager.BRIGHTNESS_FOLLOWS_LIGHT) {
            return "Set by the room\u2019s light";
        }
        return after == before ? atLimit : caption;
    }

    private String nextVideo(String caption) {
        if (!"video".equals(configStore.getDashboardLayout().backgroundMode())
                || !configStore.getDashboardUrl().isEmpty()) {
            return "No video to change";
        }
        try {
            BackgroundVideoLibrary library = BackgroundVideoLibrary.getInstance(context);
            JSONObject document = library.document();
            JSONArray videos = document.getJSONArray("videos");
            if (videos.length() < 2) {
                return "No other video";
            }
            String showing = document.optString("effectiveId", "");
            int index = -1;
            for (int position = 0; position < videos.length(); position++) {
                if (videos.getJSONObject(position).getString("id").equals(showing)) {
                    index = position;
                }
            }
            String next = videos.getJSONObject((index + 1) % videos.length()).getString("id");
            if (!library.activate(next)) {
                return "No other video";
            }
            Intent changed = new Intent(ControlServerService.ACTION_CONFIGURATION_CHANGED);
            changed.setPackage(context.getPackageName());
            context.sendBroadcast(changed);
            return caption;
        } catch (IOException | JSONException | IllegalArgumentException error) {
            Log.w(TAG, "Unable to change the background video", error);
            return "No other video";
        }
    }

    private void show(String kind, String caption) {
        Intent event = new Intent(ACTION_VOICE_EVENT);
        event.setPackage(context.getPackageName());
        event.putExtra(EXTRA_KIND, kind);
        event.putExtra(EXTRA_CAPTION, caption);
        context.sendBroadcast(event);
    }
}
