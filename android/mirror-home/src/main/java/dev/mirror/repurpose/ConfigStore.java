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
    private static final String KEY_MOTION_ENABLED = "automation_motion_enabled";
    private static final String KEY_MOTION_TIMEOUT_SECONDS = "automation_motion_timeout_seconds";
    private static final String KEY_MOTION_SENSITIVITY = "automation_motion_sensitivity";
    private static final String KEY_DASHBOARD_LAYOUT = "dashboard_layout_v1";
    private static final String KEY_WEATHER_ENABLED = "weather_enabled";
    private static final String KEY_WEATHER_LATITUDE = "weather_latitude";
    private static final String KEY_WEATHER_LONGITUDE = "weather_longitude";
    private static final String KEY_WEATHER_LOCATION_NAME = "weather_location_name";
    private static final String KEY_WEATHER_UNITS = "weather_units";
    /* Dashboard sources that no longer ship; a saved pointer to one falls back
       to the built-in dashboard instead of the offline page. */
    private static final String[] RETIRED_DASHBOARD_URLS = {
            "http://127.0.0.1:8787/dashboard/gallery.html",
            "http://127.0.0.1:8787/dashboard/aurora.html"
    };

    private final SharedPreferences preferences;

    public ConfigStore(Context context) {
        preferences = context.getApplicationContext()
                .getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
    }

    public String getDashboardUrl() {
        return normalizeDashboardUrl(preferences.getString(KEY_DASHBOARD_URL, ""));
    }

    static String normalizeDashboardUrl(String dashboardUrl) {
        if (dashboardUrl == null) {
            return "";
        }
        for (String retired : RETIRED_DASHBOARD_URLS) {
            if (retired.equals(dashboardUrl)) {
                return "";
            }
        }
        return dashboardUrl;
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

    public WeatherConfig getWeatherConfig() {
        boolean enabled = preferences.getBoolean(KEY_WEATHER_ENABLED, false);
        if (!enabled) {
            return WeatherConfig.disabled();
        }
        try {
            return new WeatherConfig(
                    true,
                    Double.parseDouble(preferences.getString(KEY_WEATHER_LATITUDE, "0")),
                    Double.parseDouble(preferences.getString(KEY_WEATHER_LONGITUDE, "0")),
                    preferences.getString(KEY_WEATHER_LOCATION_NAME, ""),
                    preferences.getString(KEY_WEATHER_UNITS, WeatherConfig.UNITS_US));
        } catch (NumberFormatException error) {
            return WeatherConfig.disabled();
        }
    }

    public void setWeatherConfig(WeatherConfig config) {
        preferences.edit()
                .putBoolean(KEY_WEATHER_ENABLED, config.enabled)
                .putString(KEY_WEATHER_LATITUDE, Double.toString(config.latitude))
                .putString(KEY_WEATHER_LONGITUDE, Double.toString(config.longitude))
                .putString(KEY_WEATHER_LOCATION_NAME, config.locationName)
                .putString(KEY_WEATHER_UNITS, config.units)
                .apply();
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

    public boolean isMotionEnabled() {
        return preferences.getBoolean(KEY_MOTION_ENABLED, false);
    }

    public int getMotionTimeoutSeconds() {
        return preferences.getInt(KEY_MOTION_TIMEOUT_SECONDS, 5 * 60);
    }

    public int getMotionSensitivity() {
        return preferences.getInt(KEY_MOTION_SENSITIVITY, 6);
    }

    public void setAutomation(
            boolean enabled,
            int wakeMinutes,
            int sleepMinutes,
            int wakeBrightness,
            boolean ambientEnabled,
            int ambientMinimum,
            int ambientMaximum,
            boolean motionEnabled,
            int motionTimeoutSeconds,
            int motionSensitivity) {
        preferences.edit()
                .putBoolean(KEY_AUTOMATION_ENABLED, enabled)
                .putInt(KEY_WAKE_MINUTES, wakeMinutes)
                .putInt(KEY_SLEEP_MINUTES, sleepMinutes)
                .putInt(KEY_WAKE_BRIGHTNESS, wakeBrightness)
                .putBoolean(KEY_AMBIENT_ENABLED, ambientEnabled)
                .putInt(KEY_AMBIENT_MIN, ambientMinimum)
                .putInt(KEY_AMBIENT_MAX, ambientMaximum)
                .putBoolean(KEY_MOTION_ENABLED, motionEnabled)
                .putInt(KEY_MOTION_TIMEOUT_SECONDS, motionTimeoutSeconds)
                .putInt(KEY_MOTION_SENSITIVITY, motionSensitivity)
                .apply();
    }
}
