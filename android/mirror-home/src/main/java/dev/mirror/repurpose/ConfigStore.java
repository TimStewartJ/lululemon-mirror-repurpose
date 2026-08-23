package dev.mirror.repurpose;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.TimeZone;

public final class ConfigStore {
    private static final String PREFERENCES = "mirror_home";
    private static final String KEY_DASHBOARD_URL = "dashboard_url";
    private static final String KEY_DISPLAY_NAME = "display_name";
    private static final String KEY_MANAGED_WIFI_SSID = "managed_wifi_ssid";
    private static final String KEY_TIME_ZONE = "time_zone";
    private static final String KEY_CLOCK_24_HOUR = "clock_24_hour";
    private static final String KEY_UTC_OFFSET_MINUTES = "utc_offset_minutes";
    private static final String KEY_AUTOMATION_ENABLED = "automation_enabled";
    private static final String KEY_WAKE_MINUTES = "automation_wake_minutes";
    private static final String KEY_SLEEP_MINUTES = "automation_sleep_minutes";
    private static final String KEY_WAKE_BRIGHTNESS = "automation_wake_brightness";
    private static final String KEY_AMBIENT_ENABLED = "automation_ambient_enabled";
    private static final String KEY_AMBIENT_MIN = "automation_ambient_min";
    private static final String KEY_AMBIENT_MAX = "automation_ambient_max";
    private static final String KEY_DASHBOARD_LAYOUT = "dashboard_layout_v1";

    private final SharedPreferences preferences;

    public ConfigStore(Context context) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
    }

    public String getDashboardUrl() {
        return preferences.getString(KEY_DASHBOARD_URL, "");
    }

    public void setDashboardUrl(String dashboardUrl) {
        preferences.edit().putString(KEY_DASHBOARD_URL, dashboardUrl).apply();
    }

    public DashboardLayoutConfig getDashboardLayout() {
        String serialized = preferences.getString(KEY_DASHBOARD_LAYOUT, "");
        if (serialized == null || serialized.isEmpty()) {
            return DashboardLayoutConfig.defaults();
        }
        try {
            return DashboardLayoutConfig.parse(serialized);
        } catch (org.json.JSONException error) {
            return DashboardLayoutConfig.defaults();
        }
    }

    public void setDashboardLayout(DashboardLayoutConfig layout) {
        preferences.edit().putString(KEY_DASHBOARD_LAYOUT, layout.serialize()).apply();
    }

    public void resetDashboardLayout() {
        preferences.edit().remove(KEY_DASHBOARD_LAYOUT).apply();
    }

    public String getDisplayName() {
        return preferences.getString(KEY_DISPLAY_NAME, "Mirror");
    }

    public void setDisplayName(String displayName) {
        preferences.edit().putString(KEY_DISPLAY_NAME, displayName).apply();
    }

    public String getManagedWifiSsid() {
        return preferences.getString(KEY_MANAGED_WIFI_SSID, "");
    }

    public void setManagedWifiSsid(String ssid) {
        preferences.edit().putString(KEY_MANAGED_WIFI_SSID, ssid).apply();
    }

    public String getTimeZoneId() {
        return preferences.getString(KEY_TIME_ZONE, TimeZone.getDefault().getID());
    }

    public void setTimeZoneId(String timeZoneId) {
        preferences.edit().putString(KEY_TIME_ZONE, timeZoneId).apply();
    }

    public int getUtcOffsetMinutes() {
        return preferences.getInt(KEY_UTC_OFFSET_MINUTES, 0);
    }

    public void setUtcOffsetMinutes(int minutes) {
        preferences.edit().putInt(KEY_UTC_OFFSET_MINUTES, minutes).apply();
    }

    public String getEffectiveTimeZoneId() {
        int minutes = getUtcOffsetMinutes();
        int absolute = Math.abs(minutes);
        return String.format(
                java.util.Locale.US,
                "GMT%s%02d:%02d",
                minutes >= 0 ? "+" : "-",
                absolute / 60,
                absolute % 60);
    }

    public boolean isClock24Hour() {
        return preferences.getBoolean(KEY_CLOCK_24_HOUR, false);
    }

    public void setClock24Hour(boolean enabled) {
        preferences.edit().putBoolean(KEY_CLOCK_24_HOUR, enabled).apply();
    }

    public boolean isAutomationEnabled() {
        return preferences.getBoolean(KEY_AUTOMATION_ENABLED, false);
    }

    public int getWakeMinutes() {
        return preferences.getInt(KEY_WAKE_MINUTES, 7 * 60);
    }

    public int getSleepMinutes() {
        return preferences.getInt(KEY_SLEEP_MINUTES, 23 * 60);
    }

    public int getWakeBrightness() {
        return preferences.getInt(KEY_WAKE_BRIGHTNESS, 180);
    }

    public boolean isAmbientEnabled() {
        return preferences.getBoolean(KEY_AMBIENT_ENABLED, false);
    }

    public int getAmbientMinimum() {
        return preferences.getInt(KEY_AMBIENT_MIN, 20);
    }

    public int getAmbientMaximum() {
        return preferences.getInt(KEY_AMBIENT_MAX, 220);
    }

    public void setAutomation(
            boolean enabled,
            int wakeMinutes,
            int sleepMinutes,
            int wakeBrightness,
            boolean ambientEnabled,
            int ambientMinimum,
            int ambientMaximum) {
        preferences.edit()
                .putBoolean(KEY_AUTOMATION_ENABLED, enabled)
                .putInt(KEY_WAKE_MINUTES, wakeMinutes)
                .putInt(KEY_SLEEP_MINUTES, sleepMinutes)
                .putInt(KEY_WAKE_BRIGHTNESS, wakeBrightness)
                .putBoolean(KEY_AMBIENT_ENABLED, ambientEnabled)
                .putInt(KEY_AMBIENT_MIN, ambientMinimum)
                .putInt(KEY_AMBIENT_MAX, ambientMaximum)
                .apply();
    }
}
