package dev.mirror.repurpose;

import android.content.Context;
import android.content.Intent;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Calendar;
import java.util.Locale;
import java.util.TimeZone;

public final class AutomationManager implements SensorEventListener {
    public static final String ACTION_STATE_CHANGED =
            "dev.mirror.repurpose.AUTOMATION_STATE_CHANGED";

    private static final long EVALUATION_INTERVAL_MS = 30_000L;
    private static final long AMBIENT_UPDATE_INTERVAL_MS = 5_000L;
    private static final long MANUAL_OVERRIDE_MS = 4 * 60 * 60 * 1000L;
    private static volatile AutomationManager instance;

    private final Context context;
    private final ConfigStore configStore;
    private final MirrorBinderClient mirror;
    private final SensorManager sensorManager;
    private final Sensor lightSensor;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable evaluator = new Runnable() {
        @Override
        public void run() {
            evaluate();
            handler.postDelayed(this, EVALUATION_INTERVAL_MS);
        }
    };

    private Boolean sleeping;
    private Boolean manualState;
    private long manualOverrideUntil;
    private float lastLux = Float.NaN;
    private long lastAmbientUpdate;
    private int lastAmbientBrightness = -1;

    private AutomationManager(Context context) {
        this.context = context.getApplicationContext();
        configStore = new ConfigStore(this.context);
        mirror = MirrorBinderClient.getInstance(this.context);
        sensorManager = (SensorManager) this.context.getSystemService(Context.SENSOR_SERVICE);
        lightSensor = sensorManager == null
                ? null
                : sensorManager.getDefaultSensor(Sensor.TYPE_LIGHT);
        updateAmbientRegistration();
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

    public synchronized boolean hasAmbientLightSensor() {
        return lightSensor != null;
    }

    public synchronized JSONObject snapshot() throws JSONException {
        return new JSONObject()
                .put("enabled", configStore.isAutomationEnabled())
                .put("wakeTime", formatMinutes(configStore.getWakeMinutes()))
                .put("sleepTime", formatMinutes(configStore.getSleepMinutes()))
                .put("wakeBrightness", configStore.getWakeBrightness())
                .put("sleeping", isSleeping())
                .put("manualOverride", manualState != null
                        && System.currentTimeMillis() < manualOverrideUntil)
                .put("ambientLightAvailable", hasAmbientLightSensor())
                .put("ambientEnabled", configStore.isAmbientEnabled())
                .put("ambientMinimum", configStore.getAmbientMinimum())
                .put("ambientMaximum", configStore.getAmbientMaximum())
                .put("ambientLux", Float.isNaN(lastLux) ? JSONObject.NULL : lastLux);
    }

    public synchronized boolean update(JSONObject body) {
        int wakeMinutes = InputValidator.parseTimeMinutes(body.optString("wakeTime", ""));
        int sleepMinutes = InputValidator.parseTimeMinutes(body.optString("sleepTime", ""));
        int wakeBrightness = body.optInt("wakeBrightness", -1);
        int ambientMinimum = body.optInt("ambientMinimum", 20);
        int ambientMaximum = body.optInt("ambientMaximum", 220);
        boolean ambientEnabled = body.optBoolean("ambientEnabled", false);
        if (wakeMinutes < 0
                || sleepMinutes < 0
                || wakeBrightness < 1
                || wakeBrightness > 255
                || ambientMinimum < 1
                || ambientMaximum > 255
                || ambientMinimum > ambientMaximum
                || (ambientEnabled && lightSensor == null)) {
            return false;
        }
        configStore.setAutomation(
                body.optBoolean("enabled", false),
                wakeMinutes,
                sleepMinutes,
                wakeBrightness,
                ambientEnabled,
                ambientMinimum,
                ambientMaximum);
        manualState = null;
        manualOverrideUntil = 0;
        updateAmbientRegistration();
        handler.post(this::evaluate);
        return true;
    }

    public synchronized void setManualSleeping(boolean shouldSleep) {
        manualState = shouldSleep;
        manualOverrideUntil = System.currentTimeMillis() + MANUAL_OVERRIDE_MS;
        applyState(shouldSleep, true);
    }

    public void refresh() {
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
        double normalized = Math.min(1d, Math.log10(Math.max(0d, lastLux) + 1d) / 4d);
        int minimum = configStore.getAmbientMinimum();
        int brightness = minimum
                + (int) Math.round((configStore.getAmbientMaximum() - minimum) * normalized);
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

    private synchronized void evaluate() {
        long now = System.currentTimeMillis();
        if (manualState != null && now < manualOverrideUntil) {
            applyState(manualState, false);
            return;
        }
        manualState = null;
        if (!configStore.isAutomationEnabled()) {
            if (Boolean.TRUE.equals(sleeping)) {
                applyState(false, true);
            } else if (sleeping == null) {
                sleeping = false;
                broadcast();
            }
            return;
        }

        Calendar calendar = Calendar.getInstance(
                TimeZone.getTimeZone(configStore.getEffectiveTimeZoneId()));
        int currentMinutes = calendar.get(Calendar.HOUR_OF_DAY) * 60
                + calendar.get(Calendar.MINUTE);
        int wake = configStore.getWakeMinutes();
        int sleep = configStore.getSleepMinutes();
        boolean awake = wake < sleep
                ? currentMinutes >= wake && currentMinutes < sleep
                : currentMinutes >= wake || currentMinutes < sleep;
        applyState(!awake, sleeping == null);
    }

    private synchronized void applyState(boolean shouldSleep, boolean force) {
        if (!force && sleeping != null && sleeping == shouldSleep) {
            return;
        }
        if (shouldSleep) {
            MediaPlaybackManager.getInstance(context).stop();
            mirror.setBrightness(1);
        } else if (!configStore.isAmbientEnabled() || lightSensor == null) {
            mirror.setBrightness(configStore.getWakeBrightness());
        }
        sleeping = shouldSleep;
        broadcast();
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
