package dev.mirror.repurpose;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

final class WeatherData {
    private static final int MAX_HOURLY_ENTRIES = 6;
    private static final int MAX_DAILY_ENTRIES = 3;

    private final JSONObject value;

    private WeatherData(JSONObject value) {
        this.value = value;
    }

    static WeatherData parse(
            JSONObject source,
            WeatherConfig config,
            long fetchedAtMillis) throws JSONException {
        JSONObject current = requiredObject(source, "current");
        JSONObject currentUnits = requiredObject(source, "current_units");
        JSONObject hourly = requiredObject(source, "hourly");
        JSONObject hourlyUnits = requiredObject(source, "hourly_units");
        JSONObject daily = requiredObject(source, "daily");
        JSONObject dailyUnits = requiredObject(source, "daily_units");

        long currentTime = secondsToMillis(requiredLong(current, "time"));
        int weatherCode = requiredInt(current, "weather_code");
        JSONObject normalizedCurrent = new JSONObject()
                .put("time", currentTime)
                .put("temperature", requiredFinite(current, "temperature_2m"))
                .put(
                        "apparentTemperature",
                        requiredFinite(current, "apparent_temperature"))
                .put("weatherCode", weatherCode)
                .put("condition", condition(weatherCode))
                .put("daylight", current.optInt("is_day", 1) == 1)
                .put("precipitation", finiteOrNull(current, "precipitation"))
                .put("windSpeed", finiteOrNull(current, "wind_speed_10m"));

        JSONArray hourlyTimes = requiredArray(hourly, "time");
        JSONArray hourlyTemperatures = requiredArray(hourly, "temperature_2m");
        JSONArray hourlyPrecipitation =
                requiredArray(hourly, "precipitation_probability");
        JSONArray hourlyCodes = requiredArray(hourly, "weather_code");
        int hourlyLength = sameLength(
                "Hourly weather arrays",
                hourlyTimes,
                hourlyTemperatures,
                hourlyPrecipitation,
                hourlyCodes);
        JSONArray normalizedHourly = new JSONArray();
        for (int index = 0;
                index < hourlyLength && normalizedHourly.length() < MAX_HOURLY_ENTRIES;
                index++) {
            long time = secondsToMillis(hourlyTimes.getLong(index));
            if (time < currentTime) {
                continue;
            }
            int code = hourlyCodes.getInt(index);
            normalizedHourly.put(new JSONObject()
                    .put("time", time)
                    .put("temperature", finite(hourlyTemperatures.getDouble(index)))
                    .put("precipitationProbability", boundedPercent(
                            hourlyPrecipitation.optInt(index, 0)))
                    .put("weatherCode", code)
                    .put("condition", condition(code)));
        }

        JSONArray dailyTimes = requiredArray(daily, "time");
        JSONArray dailyHighs = requiredArray(daily, "temperature_2m_max");
        JSONArray dailyLows = requiredArray(daily, "temperature_2m_min");
        JSONArray dailyPrecipitation =
                requiredArray(daily, "precipitation_probability_max");
        JSONArray dailyCodes = requiredArray(daily, "weather_code");
        JSONArray dailySunrise = requiredArray(daily, "sunrise");
        JSONArray dailySunset = requiredArray(daily, "sunset");
        int dailyLength = sameLength(
                "Daily weather arrays",
                dailyTimes,
                dailyHighs,
                dailyLows,
                dailyPrecipitation,
                dailyCodes,
                dailySunrise,
                dailySunset);
        JSONArray normalizedDaily = new JSONArray();
        for (int index = 0;
                index < dailyLength && normalizedDaily.length() < MAX_DAILY_ENTRIES;
                index++) {
            int code = dailyCodes.getInt(index);
            normalizedDaily.put(new JSONObject()
                    .put("time", secondsToMillis(dailyTimes.getLong(index)))
                    .put("high", finite(dailyHighs.getDouble(index)))
                    .put("low", finite(dailyLows.getDouble(index)))
                    .put("precipitationProbability", boundedPercent(
                            dailyPrecipitation.optInt(index, 0)))
                    .put("weatherCode", code)
                    .put("condition", condition(code))
                    .put("sunrise", secondsToMillis(dailySunrise.getLong(index)))
                    .put("sunset", secondsToMillis(dailySunset.getLong(index))));
        }
        if (normalizedHourly.length() == 0 || normalizedDaily.length() == 0) {
            throw new JSONException("Weather forecast contains no future data");
        }

        JSONObject units = new JSONObject()
                .put("temperature", requiredString(currentUnits, "temperature_2m"))
                .put("windSpeed", requiredString(currentUnits, "wind_speed_10m"))
                .put("precipitation", requiredString(currentUnits, "precipitation"));
        requireCompatibleUnits(
                units,
                requiredString(hourlyUnits, "temperature_2m"),
                requiredString(dailyUnits, "temperature_2m_max"),
                config.units);

        return new WeatherData(new JSONObject()
                .put("provider", "open-meteo")
                .put("fetchedAt", fetchedAtMillis)
                .put("locationName", config.locationName)
                .put("unitsSystem", config.units)
                .put("units", units)
                .put("current", normalizedCurrent)
                .put("hourly", normalizedHourly)
                .put("daily", normalizedDaily));
    }

    static WeatherData fromCache(JSONObject value) throws JSONException {
        if (!"open-meteo".equals(value.optString("provider", ""))
                || value.optLong("fetchedAt", 0L) <= 0L
                || value.optJSONObject("current") == null
                || value.optJSONArray("hourly") == null
                || value.optJSONArray("daily") == null
                || value.optJSONObject("units") == null) {
            throw new JSONException("Cached weather data is invalid");
        }
        return new WeatherData(new JSONObject(value.toString()));
    }

    JSONObject toJson() {
        try {
            return new JSONObject(value.toString());
        } catch (JSONException impossible) {
            throw new IllegalStateException("Weather data is invalid", impossible);
        }
    }

    long fetchedAt() {
        return value.optLong("fetchedAt", 0L);
    }

    static String condition(int code) {
        if (code == 0) return "Clear";
        if (code == 1) return "Mostly clear";
        if (code == 2) return "Partly cloudy";
        if (code == 3) return "Cloudy";
        if (code == 45 || code == 48) return "Fog";
        if (code >= 51 && code <= 57) return "Drizzle";
        if (code >= 61 && code <= 67) return "Rain";
        if (code >= 71 && code <= 77) return "Snow";
        if (code >= 80 && code <= 82) return "Rain showers";
        if (code >= 85 && code <= 86) return "Snow showers";
        if (code >= 95 && code <= 99) return "Thunderstorms";
        return "Unknown";
    }

    private static void requireCompatibleUnits(
            JSONObject normalized,
            String hourlyTemperature,
            String dailyTemperature,
            String configuredUnits) throws JSONException {
        String currentTemperature = normalized.getString("temperature");
        if (!currentTemperature.equals(hourlyTemperature)
                || !currentTemperature.equals(dailyTemperature)) {
            throw new JSONException("Weather temperature units are inconsistent");
        }
        boolean fahrenheit = currentTemperature.contains("F");
        if ((WeatherConfig.UNITS_US.equals(configuredUnits) && !fahrenheit)
                || (WeatherConfig.UNITS_METRIC.equals(configuredUnits) && fahrenheit)) {
            throw new JSONException("Weather response uses unexpected units");
        }
    }

    private static int sameLength(String name, JSONArray... arrays) throws JSONException {
        int length = arrays[0].length();
        if (length == 0) {
            throw new JSONException(name + " are empty");
        }
        for (JSONArray array : arrays) {
            if (array.length() != length) {
                throw new JSONException(name + " have inconsistent lengths");
            }
        }
        return length;
    }

    private static JSONObject requiredObject(JSONObject source, String name)
            throws JSONException {
        JSONObject value = source.optJSONObject(name);
        if (value == null) {
            throw new JSONException("Weather response is missing " + name);
        }
        return value;
    }

    private static JSONArray requiredArray(JSONObject source, String name)
            throws JSONException {
        JSONArray value = source.optJSONArray(name);
        if (value == null) {
            throw new JSONException("Weather response is missing " + name);
        }
        return value;
    }

    private static String requiredString(JSONObject source, String name)
            throws JSONException {
        String value = source.optString(name, "");
        if (value.isEmpty() || value.length() > 16) {
            throw new JSONException("Weather response is missing " + name);
        }
        return value;
    }

    private static double requiredFinite(JSONObject source, String name)
            throws JSONException {
        if (!source.has(name) || source.isNull(name)) {
            throw new JSONException("Weather response is missing " + name);
        }
        return finite(source.getDouble(name));
    }

    private static Object finiteOrNull(JSONObject source, String name)
            throws JSONException {
        if (!source.has(name) || source.isNull(name)) {
            return JSONObject.NULL;
        }
        return finite(source.getDouble(name));
    }

    private static double finite(double value) throws JSONException {
        if (Double.isNaN(value) || Double.isInfinite(value)) {
            throw new JSONException("Weather response contains a non-finite value");
        }
        return value;
    }

    private static long requiredLong(JSONObject source, String name) throws JSONException {
        if (!source.has(name) || source.isNull(name)) {
            throw new JSONException("Weather response is missing " + name);
        }
        return source.getLong(name);
    }

    private static int requiredInt(JSONObject source, String name) throws JSONException {
        if (!source.has(name) || source.isNull(name)) {
            throw new JSONException("Weather response is missing " + name);
        }
        return source.getInt(name);
    }

    private static long secondsToMillis(long value) throws JSONException {
        if (value < 946684800L || value > 4102444800L) {
            throw new JSONException("Weather timestamp is out of range");
        }
        return value * 1000L;
    }

    private static int boundedPercent(int value) {
        return Math.max(0, Math.min(100, value));
    }
}
