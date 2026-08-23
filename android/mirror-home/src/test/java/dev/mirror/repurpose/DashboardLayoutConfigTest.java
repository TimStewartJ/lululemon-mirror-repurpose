package dev.mirror.repurpose;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

public final class DashboardLayoutConfigTest {
    @Test
    public void defaultsAreSubtleAndComplete() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        assertEquals(1, value.getInt("version"));
        assertEquals("gradient", value.getJSONObject("background").getString("mode"));
        assertEquals(12, value.getJSONArray("widgets").length());
        assertTrue(value.getJSONObject("background").getInt("dim") >= 50);
        assertEquals(12, DashboardLayoutConfig.parse(value).toJson()
                .getJSONArray("widgets").length());
    }

    @Test(expected = JSONException.class)
    public void rejectsWidgetsOutsideCanvas() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        value.getJSONArray("widgets").getJSONObject(0).put("x", 900);
        value.getJSONArray("widgets").getJSONObject(0).put("w", 300);
        DashboardLayoutConfig.parse(value);
    }

    @Test(expected = JSONException.class)
    public void rejectsUnknownWidgets() throws JSONException {
        JSONObject value = DashboardLayoutConfig.defaults().toJson();
        value.getJSONArray("widgets")
                .put(new JSONObject().put("id", "shell").put("type", "shell"));
        DashboardLayoutConfig.parse(value);
    }
}
