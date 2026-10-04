package dev.mirror.repurpose;

import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.graphics.Point;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.util.DisplayMetrics;
import android.view.Display;
import android.view.WindowManager;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import dev.mirror.repurpose.health.ProcessHealth;

/**
 * Everything needed to judge from another room whether the Mirror is well:
 * restarts and crashes, memory and storage, whether the dashboard is really
 * in front, script errors on the glass, the clock, pairing attempts, and the
 * OTA supervisor. Built on request; it blocks briefly and reads system state.
 */
final class HealthReport {
    private static final String[] WEB_VIEW_PACKAGES = {
            "com.google.android.webview",
            "com.android.webview"
    };

    private HealthReport() {
    }

    static JSONObject build(
            Context context,
            ConfigStore configStore,
            PairingManager pairing,
            AutomationManager automation) throws JSONException {
        long now = System.currentTimeMillis();
        ProcessHealth recorder = ProcessHealth.get();
        JSONObject result = recorder == null ? new JSONObject() : recorder.snapshot();
        JSONObject device = result.optJSONObject("device");
        if (device == null) {
            device = new JSONObject();
            result.put("device", device);
        }
        device.put("sdk", Build.VERSION.SDK_INT)
                .put("release", Build.VERSION.RELEASE)
                .put("model", Build.MODEL)
                .put("fingerprint", Build.FINGERPRINT)
                .put("display", display(context))
                .put("input", input(context))
                .put("power", ForegroundKeeper.power(context))
                .put("webView", webView(context));

        JSONObject memory = result.optJSONObject("memory");
        if (memory == null) {
            memory = new JSONObject();
            result.put("memory", memory);
        }
        RestartAdvice.describe(memory);
        JSONObject supervisor = SupervisorProbe.check(context);

        UtcOffsetTimeline clock = configStore.getUtcOffsetTimeline();
        JSONArray nextChange = clock.changesJson(now, 1);
        return result
                .put("apiVersion", 1)
                .put("appVersion", BuildConfig.VERSION_NAME)
                .put("versionCode", BuildConfig.VERSION_CODE)
                .put(
                        "debuggable",
                        (context.getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0)
                .put("now", now)
                .put("activity", ActivityDiagnostics.snapshot()
                        .put("sleeping", automation.isSleeping())
                        .put("selectedHome", HomeSelection.isMirrorHomeSelected(context))
                        .put("front", ForegroundKeeper.front(context))
                        .put("recovery", ForegroundKeeper.snapshot(context)))
                .put("dashboard", DashboardDiagnostics.snapshot())
                .put("api", ApiDiagnostics.snapshot())
                .put("wifi", wifi(context))
                .put("clock", new JSONObject()
                        .put("timeZone", configStore.getTimeZoneId())
                        .put("utcOffsetMinutes", clock.offsetMinutesAt(now))
                        .put("source", configStore.getClockSource())
                        .put("knownChanges", clock.changesJson(now, Integer.MAX_VALUE).length())
                        .put("nextChange", nextChange.length() == 0
                                ? JSONObject.NULL
                                : nextChange.get(0))
                        .put("bundledTzdata", ZoneOffsetTable.getInstance(context).tzdata()))
                .put("pairing", pairing.securitySnapshot())
                .put("voice", VoiceManager.getInstance(context).snapshot())
                .put("assistant", AssistantManager.getInstance(context).diagnostics())
                .put("otaSupervisor", supervisor)
                .put("restart", RestartAdvice.snapshot());
    }

    private static JSONObject display(Context context) throws JSONException {
        DisplayMetrics metrics = context.getResources().getDisplayMetrics();
        JSONObject result = new JSONObject()
                .put("widthPixels", metrics.widthPixels)
                .put("heightPixels", metrics.heightPixels)
                .put("densityDpi", metrics.densityDpi)
                .put("fontScale", context.getResources().getConfiguration().fontScale);
        WindowManager manager = (WindowManager) context.getSystemService(Context.WINDOW_SERVICE);
        if (manager != null) {
            Display display = manager.getDefaultDisplay();
            Point real = new Point();
            display.getRealSize(real);
            result.put("realWidthPixels", real.x)
                    .put("realHeightPixels", real.y)
                    .put("refreshRate", display.getRefreshRate())
                    .put("rotation", display.getRotation())
                    .put("on", display.getState() == Display.STATE_ON);
        }
        return result;
    }

    /**
     * The input devices Android believes it has. Stock Android 6 shows its
     * "has stopped" and "isn't responding" dialogs only when there is at
     * least one, since nobody could otherwise press their buttons.
     */
    private static JSONObject input(Context context) throws JSONException {
        Configuration configuration = context.getResources().getConfiguration();
        return new JSONObject()
                .put("touchscreen", configuration.touchscreen != Configuration.TOUCHSCREEN_NOTOUCH)
                .put("keyboard", configuration.keyboard != Configuration.KEYBOARD_NOKEYS)
                .put("navigation", configuration.navigation != Configuration.NAVIGATION_NONAV);
    }

    private static Object webView(Context context) throws JSONException {
        PackageManager packages = context.getPackageManager();
        for (String name : WEB_VIEW_PACKAGES) {
            try {
                PackageInfo info = packages.getPackageInfo(name, 0);
                return new JSONObject()
                        .put("package", name)
                        .put("versionName", info.versionName == null ? "" : info.versionName);
            } catch (PackageManager.NameNotFoundException absent) {
                // Try the next provider name.
            }
        }
        return JSONObject.NULL;
    }

    private static JSONObject wifi(Context context) throws JSONException {
        WifiManager manager = (WifiManager) context.getApplicationContext()
                .getSystemService(Context.WIFI_SERVICE);
        WifiInfo info = manager == null ? null : manager.getConnectionInfo();
        if (info == null || info.getNetworkId() < 0) {
            return new JSONObject().put("connected", false);
        }
        return new JSONObject()
                .put("connected", true)
                .put("rssi", info.getRssi())
                .put("signalLevel", WifiManager.calculateSignalLevel(info.getRssi(), 5))
                .put("linkSpeedMbps", info.getLinkSpeed())
                .put("frequencyMhz", info.getFrequency());
    }
}
