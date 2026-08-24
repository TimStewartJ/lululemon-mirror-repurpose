package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

public final class WeatherDataTest {
    private static final long NOW_SECONDS = 1_787_600_000L;

    @Test
    public void normalizesCurrentHourlyAndDailyForecasts() throws JSONException {
        WeatherData data = WeatherData.parse(
                response("\u00b0F"),
                new WeatherConfig(true, 37.7, -122.4, "Home", WeatherConfig.UNITS_US),
                NOW_SECONDS * 1000L);

        JSONObject value = data.toJson();
        assertEquals("open-meteo", value.getString("provider"));
        assertEquals("Home", value.getString("locationName"));
        assertEquals(68.5, value.getJSONObject("current").getDouble("temperature"), 0.001);
        assertEquals("Partly cloudy", value.getJSONObject("current").getString("condition"));
        assertEquals(6, value.getJSONArray("hourly").length());
        assertEquals(3, value.getJSONArray("daily").length());
        assertEquals(72.0, value.getJSONArray("daily").getJSONObject(0).getDouble("high"), 0.001);
        assertEquals("Rain", WeatherData.condition(63));
        assertTrue(data.fetchedAt() > 0);
    }

    @Test(expected = JSONException.class)
    public void rejectsInconsistentArrayLengths() throws JSONException {
        JSONObject response = response("\u00b0F");
        response.getJSONObject("hourly")
                .put("weather_code", new JSONArray().put(0));
        WeatherData.parse(
                response,
                new WeatherConfig(true, 37.7, -122.4, "Home", WeatherConfig.UNITS_US),
                NOW_SECONDS * 1000L);
    }

    @Test(expected = JSONException.class)
    public void rejectsUnexpectedUnits() throws JSONException {
        WeatherData.parse(
                response("\u00b0C"),
                new WeatherConfig(true, 37.7, -122.4, "Home", WeatherConfig.UNITS_US),
                NOW_SECONDS * 1000L);
    }

    private static JSONObject response(String temperatureUnit) throws JSONException {
        JSONArray hourlyTimes = new JSONArray();
        JSONArray hourlyTemperatures = new JSONArray();
        JSONArray hourlyPrecipitation = new JSONArray();
        JSONArray hourlyCodes = new JSONArray();
        for (int index = 0; index < 8; index++) {
            hourlyTimes.put(NOW_SECONDS - 3600L + index * 3600L);
            hourlyTemperatures.put(66 + index);
            hourlyPrecipitation.put(index * 10);
            hourlyCodes.put(index < 3 ? 2 : 61);
        }

        JSONArray dailyTimes = new JSONArray();
        JSONArray dailyHighs = new JSONArray();
        JSONArray dailyLows = new JSONArray();
        JSONArray dailyPrecipitation = new JSONArray();
        JSONArray dailyCodes = new JSONArray();
        JSONArray sunrise = new JSONArray();
        JSONArray sunset = new JSONArray();
        for (int index = 0; index < 3; index++) {
            long day = NOW_SECONDS + index * 86400L;
            dailyTimes.put(day);
            dailyHighs.put(72 + index);
            dailyLows.put(55 + index);
            dailyPrecipitation.put(20 + index * 10);
            dailyCodes.put(index == 0 ? 2 : 63);
            sunrise.put(day + 7 * 3600L);
            sunset.put(day + 19 * 3600L);
        }

        return new JSONObject()
                .put("current", new JSONObject()
                        .put("time", NOW_SECONDS)
                        .put("temperature_2m", 68.5)
                        .put("apparent_temperature", 67.2)
                        .put("is_day", 1)
                        .put("precipitation", 0)
                        .put("weather_code", 2)
                        .put("wind_speed_10m", 8.4))
                .put("current_units", new JSONObject()
                        .put("temperature_2m", temperatureUnit)
                        .put("apparent_temperature", temperatureUnit)
                        .put("precipitation", "inch")
                        .put("wind_speed_10m", "mp/h"))
                .put("hourly", new JSONObject()
                        .put("time", hourlyTimes)
                        .put("temperature_2m", hourlyTemperatures)
                        .put("precipitation_probability", hourlyPrecipitation)
                        .put("weather_code", hourlyCodes))
                .put("hourly_units", new JSONObject()
                        .put("temperature_2m", temperatureUnit))
                .put("daily", new JSONObject()
                        .put("time", dailyTimes)
                        .put("temperature_2m_max", dailyHighs)
                        .put("temperature_2m_min", dailyLows)
                        .put("precipitation_probability_max", dailyPrecipitation)
                        .put("weather_code", dailyCodes)
                        .put("sunrise", sunrise)
                        .put("sunset", sunset))
                .put("daily_units", new JSONObject()
                        .put("temperature_2m_max", temperatureUnit));
    }
}
