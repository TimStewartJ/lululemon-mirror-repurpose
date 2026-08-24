package dev.mirror.repurpose;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Locale;

final class WeatherConfig {
    static final String UNITS_US = "us";
    static final String UNITS_METRIC = "metric";

    final boolean enabled;
    final double latitude;
    final double longitude;
    final String locationName;
    final String units;

    WeatherConfig(
            boolean enabled,
            double latitude,
            double longitude,
            String locationName,
            String units) {
        this.enabled = enabled;
        this.latitude = latitude;
        this.longitude = longitude;
        this.locationName = locationName;
        this.units = units;
    }

    static WeatherConfig disabled() {
        return new WeatherConfig(false, 0d, 0d, "", UNITS_US);
    }

    static WeatherConfig parse(JSONObject value) throws JSONException {
        boolean enabled = value.optBoolean("enabled", false);
        double latitude = value.optDouble("latitude", Double.NaN);
        double longitude = value.optDouble("longitude", Double.NaN);
        String locationName = value.optString("locationName", "").trim();
        String units = value.optString("units", UNITS_US).toLowerCase(Locale.US);
        if (enabled
                && (Double.isNaN(latitude)
                || Double.isNaN(longitude)
                || latitude < -90d
                || latitude > 90d
                || longitude < -180d
                || longitude > 180d)) {
            throw new JSONException("Weather coordinates are invalid");
        }
        if (locationName.length() > 80 || containsControlCharacter(locationName)) {
            throw new JSONException("Weather location name is invalid");
        }
        if (!UNITS_US.equals(units) && !UNITS_METRIC.equals(units)) {
            throw new JSONException("Weather units must be us or metric");
        }
        return new WeatherConfig(
                enabled,
                enabled ? latitude : 0d,
                enabled ? longitude : 0d,
                locationName,
                units);
    }

    JSONObject toJson(boolean includeCoordinates) throws JSONException {
        JSONObject value = new JSONObject()
                .put("enabled", enabled)
                .put("locationName", locationName)
                .put("units", units);
        if (includeCoordinates && enabled) {
            value.put("latitude", latitude);
            value.put("longitude", longitude);
        } else if (includeCoordinates) {
            value.put("latitude", JSONObject.NULL);
            value.put("longitude", JSONObject.NULL);
        }
        return value;
    }

    String cacheKey() {
        return String.format(
                Locale.US,
                "%.5f,%.5f,%s",
                latitude,
                longitude,
                units);
    }

    private static boolean containsControlCharacter(String value) {
        for (int index = 0; index < value.length(); index++) {
            if (Character.isISOControl(value.charAt(index))) {
                return true;
            }
        }
        return false;
    }
}
