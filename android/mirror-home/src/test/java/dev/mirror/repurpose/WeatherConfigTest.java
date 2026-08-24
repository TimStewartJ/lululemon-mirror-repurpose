package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

public final class WeatherConfigTest {
    @Test
    public void parsesEnabledConfiguration() throws JSONException {
        WeatherConfig config = WeatherConfig.parse(new JSONObject()
                .put("enabled", true)
                .put("latitude", 37.7749)
                .put("longitude", -122.4194)
                .put("locationName", "San Francisco")
                .put("units", "US"));

        assertTrue(config.enabled);
        assertEquals(37.7749, config.latitude, 0.00001);
        assertEquals(-122.4194, config.longitude, 0.00001);
        assertEquals("San Francisco", config.locationName);
        assertEquals(WeatherConfig.UNITS_US, config.units);
    }

    @Test
    public void disabledConfigurationDoesNotRequireCoordinates() throws JSONException {
        WeatherConfig config = WeatherConfig.parse(
                new JSONObject().put("enabled", false));

        assertTrue(!config.enabled);
        assertEquals(WeatherConfig.UNITS_US, config.units);
    }

    @Test(expected = JSONException.class)
    public void rejectsInvalidCoordinates() throws JSONException {
        WeatherConfig.parse(new JSONObject()
                .put("enabled", true)
                .put("latitude", 91)
                .put("longitude", 0));
    }

    @Test(expected = JSONException.class)
    public void rejectsUnknownUnits() throws JSONException {
        WeatherConfig.parse(new JSONObject()
                .put("enabled", false)
                .put("units", "kelvin"));
    }
}
