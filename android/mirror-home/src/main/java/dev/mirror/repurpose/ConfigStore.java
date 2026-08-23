package dev.mirror.repurpose;

import android.content.Context;
import android.content.SharedPreferences;

public final class ConfigStore {
    private static final String PREFERENCES = "mirror_home";
    private static final String KEY_DASHBOARD_URL = "dashboard_url";
    private static final String KEY_DISPLAY_NAME = "display_name";

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

    public String getDisplayName() {
        return preferences.getString(KEY_DISPLAY_NAME, "Mirror");
    }

    public void setDisplayName(String displayName) {
        preferences.edit().putString(KEY_DISPLAY_NAME, displayName).apply();
    }
}
