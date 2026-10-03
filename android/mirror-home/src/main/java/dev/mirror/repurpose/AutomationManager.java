package dev.mirror.repurpose;

import android.content.Context;
import android.content.Intent;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Locale;

public final class AutomationManager implements SensorEventListener {
    public static final String ACTION_STATE_CHANGED =
            "dev.mirror.repurpose.AUTOMATION_STATE_CHANGED";

    private static final long EVALUATION_INTERVAL_MS = 5_000L;
    private static final long AMBIENT_UPDATE_INTERVAL_MS = 5_000L;
    private static final long MANUAL_OVERRIDE_MS = 4 * 60 * 60 * 1000L;
    /** {@link #stepWakeBrightness} when a light sensor sets the brightness. */
    static final int BRIGHTNESS_FOLLOWS_LIGHT = -1;
    private static final int MIN_MOTION_TIMEOUT_SECONDS = 30;
    private static final int MAX_MOTION_TIMEOUT_SECONDS = 60 * 60;
    private static volatile AutomationManager instance;

    private final Context context;
    private final ConfigStore configStore;
    private final MirrorBinderClient mirror;
    private final SensorManager sensorManager;
    private final Sensor lightSensor;
    private final MotionDetectionManager motion;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable evaluator = new Runnable() {
        @Override
        public void run() {
            evaluate();
            handler.postDelayed(this, EVALUATION_INTERVAL_MS);
        }
    };
    private final Runnable sleepCommit = new Runnable() {
        @Override
        public void run() {
            synchronized (AutomationManager.this) {
                if (sleepCommitPending && Boolean.TRUE.equals(sleeping)) {
                    commitSleep();
                }
            }
        }
    };

    private Boolean sleeping;
    private Boolean manualState;
    private long manualOverrideUntilElapsed;
    private String sleepReason = "none";
    private float lastLux = Float.NaN;
    private long lastAmbientUpdate;
    private int lastAmbientBrightness = -1;
    private boolean brightnessApplied;
    private boolean sleepCommitPending;
    private long lastMotionElapsed;

    private AutomationManager(Context context) {
        this.context = context.getApplicationContext();
        configStore = new ConfigStore(this.context);
        mirror = MirrorBinderClient.getInstance(this.context);
        sensorManager = (SensorManager) this.context.getSystemService(Context.SENSOR_SERVICE);
        lightSensor = sensorManager == null
                ? null
                : sensorManager.getDefaultSensor(Sensor.TYPE_LIGHT);
        motion = new MotionDetectionManager(this.context, new MotionDetectionManager.Listener() {
            @Override
            public void onMotionDetected(long elapsedRealtime) {
                handleMotionDetected(elapsedRealtime);
            }

            @Override
            public void onMotionStateChanged() {
                handleMotionStateChanged();
            }
        });
        handler.post(this::updateAmbientRegistration);
        handler.post(motion::refresh);
        handler.post(evaluator);
    }

    public static AutomationManager getInstance(Context context) {
        if (instance == null) {
            synchronized (AutomationManager.class) {
                if (instance == null) {
                    instance = new AutomationManager(context);
                }
            }
        }
        return instance;
    }

    public synchronized boolean isSleeping() {
        return Boolean.TRUE.equals(sleeping);
    }

    /** Why the display is dark: "manual", "schedule" or "inactivity"; "none" while it is awake. */
    public synchronized String sleepReason() {
        return sleepReason;
    }

    public synchronized boolean hasAmbientLightSensor() {
        return lightSensor != null;
    }

    /** Backlight level the display should reach when awake. */
    public synchronized int awakeBrightness() {
        return !configStore.isAmbientEnabled() || lightSensor == null
                ? configStore.getWakeBrightness()
                : ambientWakeBrightness();
    }

    /** Whether the stored brightness matches the awake target, so an override can be released. */
    public synchronized boolean isBrightnessApplied() {
        return brightnessApplied && !sleepCommitPending;
    }

    /** MainActivity reports that its sleep fade has fully reached black. */
    public synchronized void onDisplayFadedOut() {
        if (sleepCommitPending && Boolean.TRUE.equals(sleeping)) {
            handler.removeCallbacks(sleepCommit);
            commitSleep();
        }
    }

    public synchronized JSONObject snapshot() throws JSONException {
        return new JSONObject()
                .put("enabled", configStore.isAutomationEnabled())
                .put("wakeTime", formatMinutes(configStore.getWakeMinutes()))
                .put("sleepTime", formatMinutes(configStore.getSleepMinutes()))
                .put("wakeBrightness", configStore.getWakeBrightness())
                .put("sleeping", isSleeping())
                .put("sleepReason", sleepReason)
                .put("manualOverride", manualState != null
                        && SystemClock.elapsedRealtime() < manualOverrideUntilElapsed)
                .put("ambientLightAvailable", hasAmbientLightSensor())
                .put("ambientEnabled", configStore.isAmbientEnabled())
                .put("ambientMinimum", configStore.getAmbientMinimum())
                .put("ambientMaximum", configStore.getAmbientMaximum())
                .put("ambientLux", Float.isNaN(lastLux) ? JSONObject.NULL : lastLux)
                .put("motionEnabled", configStore.isMotionEnabled())
                .put("motionTimeoutSeconds", configStore.getMotionTimeoutSeconds())
                .put("motionSensitivity", configStore.getMotionSensitivity())
                .put("motion", motion.snapshot());
    }

    public synchronized boolean update(JSONObject body) {
        int wakeMinutes = InputValidator.parseTimeMinutes(body.optString("wakeTime", ""));
        int sleepMinutes = InputValidator.parseTimeMinutes(body.optString("sleepTime", ""));
        int wakeBrightness = body.optInt("wakeBrightness", -1);
        int ambientMinimum = body.optInt("ambientMinimum", 20);
        int ambientMaximum = body.optInt("ambientMaximum", 220);
        boolean ambientEnabled = body.optBoolean("ambientEnabled", false);
        boolean motionEnabled = body.has("motionEnabled")
                ? body.optBoolean("motionEnabled", false)
                : configStore.isMotionEnabled();
        int motionTimeoutSeconds = body.has("motionTimeoutSeconds")
                ? body.optInt("motionTimeoutSeconds", -1)
                : configStore.getMotionTimeoutSeconds();
        int motionSensitivity = body.has("motionSensitivity")
                ? body.optInt("motionSensitivity", -1)
                : configStore.getMotionSensitivity();
        if (wakeMinutes < 0
                || sleepMinutes < 0
                || wakeBrightness < 1
                || wakeBrightness > 255
                || ambientMinimum < 1
                || ambientMaximum > 255
                || ambientMinimum > ambientMaximum
                || (ambientEnabled && lightSensor == null)
                || motionTimeoutSeconds < MIN_MOTION_TIMEOUT_SECONDS
                || motionTimeoutSeconds > MAX_MOTION_TIMEOUT_SECONDS
                || motionSensitivity < MotionFrameAnalyzer.MIN_SENSITIVITY
                || motionSensitivity > MotionFrameAnalyzer.MAX_SENSITIVITY) {
            return false;
        }
        configStore.setAutomation(
                body.optBoolean("enabled", false),
                wakeMinutes,
                sleepMinutes,
                wakeBrightness,
                ambientEnabled,
                ambientMinimum,
                ambientMaximum,
                motionEnabled,
                motionTimeoutSeconds,
                motionSensitivity);
        manualState = null;
        manualOverrideUntilElapsed = 0;
        lastMotionElapsed = motionEnabled ? SystemClock.elapsedRealtime() : 0;
        handler.post(this::updateAmbientRegistration);
        handler.post(motion::refresh);
        handler.post(this::evaluate);
        broadcast();
        return true;
    }

    public synchronized void setManualSleeping(boolean shouldSleep) {
        manualState = shouldSleep;
        manualOverrideUntilElapsed = SystemClock.elapsedRealtime() + MANUAL_OVERRIDE_MS;
        applyState(shouldSleep, shouldSleep ? "manual" : "none", true);
    }

    /**
     * Someone at the Mirror asked for the display. That ends a sleep that was
     * asked for and counts as having been seen, so the display goes dark
     * again when the room is empty rather than hours later. Outside the wake
     * hours it stays awake as if woken from the controls.
     */
    public synchronized void wakeForPresence() {
        long now = SystemClock.elapsedRealtime();
        lastMotionElapsed = now;
        boolean keptAwake = Boolean.FALSE.equals(manualState) && now < manualOverrideUntilElapsed;
        if (!keptAwake) {
            manualState = null;
            manualOverrideUntilElapsed = 0;
        }
        evaluate();
        if (isSleeping()) {
            setManualSleeping(false);
        }
    }

    /**
     * Makes the awake display a step brighter or dimmer, as asked for at the
     * Mirror: now, and as the level it wakes to from then on.
     *
     * @return the level it now wakes to, which is the one before at either
     *     end of the range; {@link #BRIGHTNESS_FOLLOWS_LIGHT} if a light
     *     sensor sets the brightness, which leaves no level to change
     */
    public synchronized int stepWakeBrightness(int step) {
        if (configStore.isAmbientEnabled() && lightSensor != null) {
            return BRIGHTNESS_FOLLOWS_LIGHT;
        }
        int current = configStore.getWakeBrightness();
        int target = DisplayAutomationPolicy.steppedBrightness(current, step);
        if (target != current) {
            configStore.setWakeBrightness(target);
            if (!isSleeping()) {
                brightnessApplied = mirror.setBrightness(target);
            }
            broadcast();
        }
        return target;
    }

    public void refresh() {
        motion.refresh();
        handler.post(this::evaluate);
    }

    public void refreshMotionDetection() {
        motion.refresh();
        handler.post(this::evaluate);
    }

    @Override
    public synchronized void onSensorChanged(SensorEvent event) {
        if (event.sensor.getType() != Sensor.TYPE_LIGHT || event.values.length == 0) {
            return;
        }
        lastLux = event.values[0];
        long now = System.currentTimeMillis();
        if (isSleeping()
                || !configStore.isAmbientEnabled()
                || now - lastAmbientUpdate < AMBIENT_UPDATE_INTERVAL_MS) {
            return;
        }
        int brightness = ambientBrightness(lastLux);
        if (lastAmbientBrightness < 0 || Math.abs(brightness - lastAmbientBrightness) >= 4) {
            if (mirror.setBrightness(brightness)) {
                lastAmbientBrightness = brightness;
                lastAmbientUpdate = now;
            }
        }
    }

    @Override
    public void onAccuracyChanged(Sensor sensor, int accuracy) {
    }

    private int ambientBrightness(float lux) {
        double normalized = Math.min(1d, Math.log10(Math.max(0d, lux) + 1d) / 4d);
        int minimum = configStore.getAmbientMinimum();
        return minimum
                + (int) Math.round((configStore.getAmbientMaximum() - minimum) * normalized);
    }

    private int ambientWakeBrightness() {
        if (lastAmbientBrightness > 0) {
            return lastAmbientBrightness;
        }
        return Float.isNaN(lastLux)
                ? configStore.getWakeBrightness()
                : ambientBrightness(lastLux);
    }

    private synchronized void evaluate() {
        long nowElapsed = SystemClock.elapsedRealtime();
        if (manualState != null && nowElapsed < manualOverrideUntilElapsed) {
            applyState(manualState, manualState ? "manual" : "none", false);
            return;
        }
        manualState = null;
        boolean scheduleAllowsWake = isInsideWakeSchedule();
        long inactiveFor = lastMotionElapsed == 0
                ? Long.MAX_VALUE
                : Math.max(0L, nowElapsed - lastMotionElapsed);
        boolean shouldSleep = DisplayAutomationPolicy.shouldSleep(
                scheduleAllowsWake,
                configStore.isMotionEnabled(),
                motion.isMonitoring(),
                MediaPlaybackManager.getInstance(context).isPresentationActive(),
                inactiveFor,
                configStore.getMotionTimeoutSeconds() * 1000L);
        String reason = !scheduleAllowsWake
                ? "schedule"
                : (shouldSleep ? "inactivity" : "none");
        applyState(shouldSleep, reason, sleeping == null);
    }

    private synchronized void applyState(
            boolean shouldSleep,
            String reason,
            boolean force) {
        boolean stateChanged = sleeping == null || sleeping != shouldSleep;
        String nextReason = shouldSleep ? reason : "none";
        boolean reasonChanged = !sleepReason.equals(nextReason);
        if (!force && !stateChanged && !reasonChanged && brightnessApplied) {
            return;
        }
        if (shouldSleep) {
            if (stateChanged && sleeping != null) {
                // MainActivity fades out from the awake level and reports when the
                // panel is black; only then do media stop and stored brightness drop.
                // The timer is a fallback for when no activity is showing.
                handler.removeCallbacks(sleepCommit);
                sleepCommitPending = true;
                brightnessApplied = true;
                handler.postDelayed(sleepCommit, DisplayFadePolicy.sleepCommitFallbackMs());
            } else if (!sleepCommitPending) {
                commitSleep();
            }
        } else {
            handler.removeCallbacks(sleepCommit);
            sleepCommitPending = false;
            if (!configStore.isAmbientEnabled() || lightSensor == null) {
                brightnessApplied = mirror.setBrightness(configStore.getWakeBrightness());
            } else if (stateChanged || !brightnessApplied) {
                // Sleep stored level 1; restore an ambient level as the fade target.
                int level = ambientWakeBrightness();
                brightnessApplied = mirror.setBrightness(level);
                if (brightnessApplied) {
                    lastAmbientBrightness = level;
                }
            } else {
                brightnessApplied = true;
            }
        }
        sleeping = shouldSleep;
        sleepReason = nextReason;
        if (stateChanged || reasonChanged) {
            broadcast();
        }
    }

    private void commitSleep() {
        sleepCommitPending = false;
        MediaPlaybackManager.getInstance(context).stop();
        // The vendor Binder clamps to 1; MainActivity applies the true zero override.
        brightnessApplied = mirror.setBrightness(1);
    }

    private void updateAmbientRegistration() {
        if (sensorManager == null || lightSensor == null) {
            return;
        }
        sensorManager.unregisterListener(this);
        if (configStore.isAmbientEnabled()) {
            sensorManager.registerListener(this, lightSensor, SensorManager.SENSOR_DELAY_NORMAL);
        }
    }

    private synchronized void handleMotionDetected(long elapsedRealtime) {
        boolean wasEmpty = isSleeping() && "inactivity".equals(sleepReason);
        long emptySince = lastMotionElapsed;
        lastMotionElapsed = elapsedRealtime;
        evaluate();
        if (wasEmpty && !isSleeping()) {
            // Someone has come back: the assistant may have something to say.
            AssistantManager.getInstance(context)
                    .presence(Math.max(0L, elapsedRealtime - emptySince) / 1000L);
        }
    }

    private synchronized void handleMotionStateChanged() {
        if (motion.isMonitoring()) {
            // A recovered or delayed camera always gets a full inactivity window.
            lastMotionElapsed = SystemClock.elapsedRealtime();
        }
        evaluate();
        broadcast();
    }

    private boolean isInsideWakeSchedule() {
        if (!configStore.isAutomationEnabled()) {
            return true;
        }
        int currentMinutes = configStore.getUtcOffsetTimeline()
                .minuteOfDayAt(System.currentTimeMillis());
        int wake = configStore.getWakeMinutes();
        int sleep = configStore.getSleepMinutes();
        return wake < sleep
                ? currentMinutes >= wake && currentMinutes < sleep
                : currentMinutes >= wake || currentMinutes < sleep;
    }

    private void broadcast() {
        Intent intent = new Intent(ACTION_STATE_CHANGED);
        intent.setPackage(context.getPackageName());
        context.sendBroadcast(intent);
    }

    private static String formatMinutes(int minutes) {
        return String.format(
                Locale.US,
                "%02d:%02d",
                minutes / 60,
                minutes % 60);
    }
}
